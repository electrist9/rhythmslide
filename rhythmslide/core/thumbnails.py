"""サムネイルの SQLite キャッシュ（計画書 F2 / N4）。

素材パネルは 1 万枚でも扱うため、フル解像度ではなく縮小版をキャッシュして再利用する。
従来は 1 元画像につき 1 JPEG ファイルを thumbcache/ に置いていたが、枚数が増えると
小さなファイルが数万個並び、列挙・バックアップ・削除が遅くなる。そこで JPEG を BLOB として
1 個の SQLite DB に集約した（ワークスペース内の thumbnails.db。APPDATA 等は使わない）。

キャッシュキーは (絶対パス + mtime_ns + size + 要求辺長)。壊れた画像はグレーのプレースホルダ。
同一インスタンスを GUI スレッドと QThreadPool のワーカスレッドが同時に使うため、
コネクションは check_same_thread=False で開き、全操作をロックで直列化する（WAL で並発改善）。
"""
from __future__ import annotations

import hashlib
import io
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .paths import ensure_dir, thumbnail_db_path

log = logging.getLogger(__name__)

#: 素材パネル向けの標準サムネイル辺長（px）
THUMB_SIZE = 256
_PLACEHOLDER = (60, 60, 60)

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS thumbnails (
    key        TEXT PRIMARY KEY,
    path       TEXT,
    size       INTEGER,
    width      INTEGER,
    height     INTEGER,
    mtime_ns   INTEGER,
    src_size   INTEGER,
    blob       BLOB NOT NULL,
    created_at REAL
)
"""


def thumb_key(path: Path | str, size: int = THUMB_SIZE) -> str:
    p = Path(path)
    h = hashlib.sha1()
    h.update(str(p).lower().encode("utf-8"))
    try:
        st = p.stat()
        h.update(str(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))).encode())
        h.update(str(st.st_size).encode())
    except OSError:
        h.update(b"<nostat>")
    h.update(f"|{size}".encode())
    return h.hexdigest()


class ThumbnailCache:
    def __init__(self, db_path: Path | str | None = None, size: int = THUMB_SIZE) -> None:
        self.db_path = Path(db_path) if db_path else thumbnail_db_path()
        self.size = int(size)
        self._lock = threading.RLock()
        self._con: sqlite3.Connection | None = None

    # ---- DB 接続（遅延オープン） --------------------------------------
    def _conn(self) -> sqlite3.Connection:
        if self._con is None:
            ensure_dir(self.db_path.parent)
            con = sqlite3.connect(str(self.db_path), check_same_thread=False)
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA synchronous=NORMAL;")
            con.execute(_CREATE_SQL)
            con.commit()
            self._con = con
        return self._con

    def close(self) -> None:
        """DB を閉じる（終了時などに呼ぶ。二重呼び出し・未オープンでも安全）。"""
        with self._lock:
            if self._con is not None:
                try:
                    self._con.close()
                except Exception:  # noqa: BLE001
                    pass
                self._con = None

    # ---- 公開 API（戻り値は JPEG の bytes） ----------------------------
    def get(self, path: Path | str) -> bytes | None:
        """キャッシュ済みなら JPEG BLOB を返す（生成はしない）。"""
        return self._get_by_key(thumb_key(path, self.size))

    def ensure(self, path: Path | str) -> bytes | None:
        """サムネイル JPEG（bytes）を確保して返す。生成失敗時は None。"""
        key = thumb_key(path, self.size)
        blob = self._get_by_key(key)
        if blob is not None:
            return blob
        rendered = self._render_blob(path)
        if rendered is None:
            return None
        blob, w, h = rendered
        self._put(key, path, w, h, blob)
        return blob

    # ---- 内部 ----------------------------------------------------------
    def _get_by_key(self, key: str) -> bytes | None:
        with self._lock:
            row = self._conn().execute(
                "SELECT blob FROM thumbnails WHERE key=?", (key,)
            ).fetchone()
        return row[0] if row else None

    def _put(self, key: str, path: Path | str, w: int, h: int, blob: bytes) -> None:
        mtime_ns: int | None = None
        src_size: int | None = None
        try:
            st = Path(path).stat()
            mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)))
            src_size = int(st.st_size)
        except OSError:
            pass
        with self._lock:
            con = self._conn()
            con.execute(
                "INSERT OR REPLACE INTO thumbnails"
                "(key,path,size,width,height,mtime_ns,src_size,blob,created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (key, str(path), self.size, w, h, mtime_ns, src_size, blob, time.time()),
            )
            con.commit()

    def _render_blob(self, path: Path | str) -> tuple[bytes, int, int] | None:
        """元画像からサムネイル JPEG をメモリ生成して (bytes, w, h) を返す。"""
        try:
            from PIL import Image, ImageOps

            with Image.open(str(path)) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                im.thumbnail((self.size, self.size))
                buf = io.BytesIO()
                im.save(buf, "JPEG", quality=82)
                return buf.getvalue(), im.width, im.height
        except Exception as exc:  # noqa: BLE001 - 壊画像でも止めない
            log.debug("サムネイル生成失敗 %s: %s", path, exc)
            return self._placeholder_blob()

    def _placeholder_blob(self) -> tuple[bytes, int, int] | None:
        try:
            from PIL import Image

            im = Image.new("RGB", (self.size // 2, self.size // 2), _PLACEHOLDER)
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=70)
            return buf.getvalue(), im.width, im.height
        except Exception:  # noqa: BLE001
            return None
