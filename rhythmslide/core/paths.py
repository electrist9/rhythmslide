"""アプリのデータ保存先（ワークスペース直下）解決.

キャッシュ（ビート・サムネイル）と設定ファイルは、原則として
**アプリを格納するワークスペース（プロジェクト）フォルダ直下** にだけ保存する。
%APPDATA% / %LOCALAPPDATA% などユーザープロファイル下には保存しない。

開発・テスト時は環境変数 ``RHYTHMSLIDE_DATA_DIR`` で明示的に上書きできる。
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

log = logging.getLogger(__name__)

ENV_VAR = "RHYTHMSLIDE_DATA_DIR"
DATA_SUBDIR = "rhythmslide/data"
# 旧バージョンの既定場所。存在すれば新場所へ 1 度だけ自動で引っ越す。
LEGACY_DATA_SUBDIR = ".rhythmslide_data"


def workspace_root() -> Path:
    """アプリを格納するワークスペース（プロジェクト）フォルダ。

    - 開発時: ``rhythmslide/core/paths.py`` から見て 2 階層上（パッケージの親）。
    - PyInstaller 同梱時: 実行ファイルのあるフォルダ。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """アプリ固有のデータ保存ディレクトリを返す（存在するとは限らない）。"""
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser()
    return workspace_root() / DATA_SUBDIR


def beat_cache_dir() -> Path:
    return data_dir() / "beatcache"


def thumbnail_cache_dir() -> Path:
    return data_dir() / "thumbcache"


def thumbnail_db_path() -> Path:
    """サムネイルキャッシュの SQLite DB ファイルパス（1 元画像=1 ファイルをやめ 1 DB に集約）。"""
    return thumbnail_cache_dir() / "thumbnails.db"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _candidate_dirs() -> list[Path]:
    """書き込みを試すデータ保存先の候補（優先順・ワークスペース内に限定）。"""
    override = os.environ.get(ENV_VAR)
    if override:
        # 明示指定は代替しない（失敗時はそのまま報告する）。
        return [Path(override).expanduser()]
    cands: list[Path] = [workspace_root() / DATA_SUBDIR]
    # 起動フォルダがワークスペース直下と異なる場合に備え、そこも候補に置く
    # （いずれもワークスペース/起動フォルダ配下であり、プロファイル下は使わない）。
    try:
        cwd = Path.cwd() / DATA_SUBDIR
        if cwd not in cands:
            cands.append(cwd)
    except OSError:
        pass
    return cands


def _is_writable(path: Path) -> bool:
    """実際にファイルを書けるかを確認する（mkdir だけでは不十分なため）。"""
    probe = path / ".write_test"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        try:
            probe.unlink()
        except OSError:
            pass
        return False


def _migrate_legacy_data(target: Path) -> None:
    """旧既定場所 ``.rhythmslide_data`` があれば新場所へ 1 度だけ引っ越す。

    環境変数で明示上書きしている場合は行わない。新場所に既に同名がある項目は
    上書きせずスキップし、旧場所が空になったら旧ディレクトリを削除する。
    """
    legacy = workspace_root() / LEGACY_DATA_SUBDIR
    if not legacy.is_dir() or legacy == target:
        return
    try:
        ensure_dir(target)
        moved = False
        for child in legacy.iterdir():
            dst = target / child.name
            if dst.exists():
                continue
            shutil.move(str(child), str(dst))
            moved = True
        if not any(legacy.iterdir()):
            legacy.rmdir()
        if moved:
            log.info("データ保存先を %s から %s へ引っ越しました", legacy, target)
    except OSError as exc:
        log.warning("旧データの引っ越しに失敗しました（%s → %s）: %s", legacy, target, exc)


def ensure_data_dir() -> Path:
    """ワークスペース内で書き込めるデータ保存先を 1 度だけ決め、環境変数に固定して返す。

    既定はワークスペース直下の ``rhythmslide/data``。書き込めない場合も
    ワークスペース/起動フォルダ内の代替のみを試み、%APPDATA% 等へは逃がさない。
    すべて失敗した場合は最初の候補を返し、キャッシュは書けないがアプリは動く。
    既定場所を使う場合、旧 ``.rhythmslide_data`` があれば自動で引っ越す。
    """
    override = os.environ.get(ENV_VAR)
    cands = _candidate_dirs()
    for cand in cands:
        try:
            ensure_dir(cand)
        except OSError as exc:
            log.warning("データ保存先を作成できません（%s）: %s", cand, exc)
            continue
        if not _is_writable(cand):
            log.warning("データ保存先に書き込めません（%s）", cand)
            continue
        if cand != cands[0]:
            log.info("データ保存先を %s に変更しました", cand)
        if not override:
            _migrate_legacy_data(cand)
        os.environ[ENV_VAR] = str(cand)
        return cand
    return cands[0]
