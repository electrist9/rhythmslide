"""撮影日（EXIF）抽出。

1 万枚規模の再帰スキャンで遅くならないよう、JPEG はファイルヘッダだけを読んで
APP1/Exif の DateTimeOriginal を直接探す高速パスを持つ（Pillow で画像全体を
オープンしない）。PNG/WebP/TIFF は Pillow の遅延ヘッダ読み込みにフォールバックする。
"""
from __future__ import annotations

import struct
from datetime import datetime
from pathlib import Path
from typing import Optional

# EXIF タグ
_TAG_DATETIME = 306
_TAG_DATETIME_ORIGINAL = 36867
_TAG_DATETIME_DIGITIZED = 36868
_TAG_EXIF_IFD = 34667
_TAG_EXIF_IFD_POINTER = 34665

_READ_HEAD = 128 * 1024  # EXIF は先頭付近にあることがほとんど


def parse_exif_datetime(raw: bytes) -> Optional[datetime]:
    """TIFF ヘッダ（"Exif\\0\\0" 以降のバイト列）から撮影日を拾う。"""
    if len(raw) < 8:
        return None
    order = raw[:2]
    if order == b"II":
        endian = "<"
    elif order == b"MM":
        endian = ">"
    else:
        return None
    try:
        magic, ifd0 = struct.unpack_from(endian + "HI", raw, 2)
    except struct.error:
        return None
    if magic != 42:
        return None

    def read_ifd(offset: int, wanted: set[int]) -> dict[int, tuple[int, int, int]]:
        """tag -> (type, count, value_offset) を返す（不正なら空）。"""
        out: dict[int, tuple[int, int, int]] = {}
        if offset <= 0 or offset + 2 > len(raw):
            return out
        try:
            (count,) = struct.unpack_from(endian + "H", raw, offset)
        except struct.error:
            return out
        pos = offset + 2
        for _ in range(count):
            if pos + 12 > len(raw):
                break
            try:
                tag, typ, cnt = struct.unpack_from(endian + "HHI", raw, pos)
                val = struct.unpack_from(endian + "I", raw, pos + 8)[0]
            except struct.error:
                break
            out[tag] = (typ, cnt, val)
            pos += 12
        return out

    def read_string(typ: int, cnt: int, val: int) -> Optional[str]:
        if typ != 2:  # ASCII
            return None
        off = val if cnt <= 4 else val
        if off < 0 or off + cnt > len(raw) or cnt <= 0:
            return None
        try:
            return raw[off : off + cnt].decode("ascii", "ignore").strip("\x00").strip()
        except Exception:
            return None

    ifd0 = read_ifd(ifd0, set())
    # Exif サブ IFD を優先
    sub = ifd0.get(_TAG_EXIF_IFD_POINTER)
    candidates: list[Optional[str]] = []
    if sub:
        sub_ifd = read_ifd(sub[2], set())
        for tag in (_TAG_DATETIME_ORIGINAL, _TAG_DATETIME_DIGITIZED, _TAG_DATETIME):
            entry = sub_ifd.get(tag)
            if entry:
                candidates.append(read_string(*entry))
    for tag in (_TAG_DATETIME_ORIGINAL, _TAG_DATETIME):
        entry = ifd0.get(tag)
        if entry:
            candidates.append(read_string(*entry))

    for text in candidates:
        dt = _to_datetime(text)
        if dt:
            return dt
    return None


def _to_datetime(text: Optional[str]) -> Optional[datetime]:
    if not text:
        return None
    text = text.strip()
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y:%m:%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 6].strip(), fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _jpeg_taken_at(path: Path) -> Optional[datetime]:
    try:
        with path.open("rb") as fh:
            head = fh.read(_READ_HEAD)
    except OSError:
        return None
    if len(head) < 4 or head[:2] != b"\xff\xd8":
        return None
    pos = 2
    n = len(head)
    while pos + 4 <= n:
        if head[pos] != 0xFF:
            pos += 1
            continue
        marker = head[pos + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            pos += 2
            continue
        if marker == 0xDA:  # SOS: セグメントデータに入る
            break
        seglen = struct.unpack_from(">H", head, pos + 2)[0]
        if seglen < 2:
            break
        if marker == 0xE1:  # APP1
            seg = head[pos + 4 : pos + 2 + seglen]
            if seg[:6] == b"Exif\x00\x00":
                dt = parse_exif_datetime(seg[6:])
                if dt:
                    return dt
        pos += 2 + seglen
    return None


def _pillow_taken_at(path: Path) -> Optional[datetime]:
    try:
        from PIL import Image

        with Image.open(path) as im:
            exif = getattr(im, "_getexif", None)
            data = exif() if callable(exif) else None
            if data:
                for tag in (_TAG_DATETIME_ORIGINAL, _TAG_DATETIME_DIGITIZED, _TAG_DATETIME):
                    if tag in data:
                        dt = _to_datetime(str(data[tag]))
                        if dt:
                            return dt
            # PNG/WebP 系のテキストメタデータ
            info = getattr(im, "info", {}) or {}
            for key in ("Creation Time", "creation_time", "DateTimeOriginal"):
                dt = _to_datetime(str(info.get(key, "")))
                if dt:
                    return dt
    except Exception:
        return None
    return None


def read_taken_at(path: Path | str) -> Optional[datetime]:
    """撮影日を返す。無ければ None（呼び出し側が更新日で代替する）。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in (".jpg", ".jpeg", ".jpe"):
        dt = _jpeg_taken_at(p)
        if dt:
            return dt
    return _pillow_taken_at(p)


def is_animated(path: Path | str) -> bool:
    """アニメ GIF / アニメ WebP かどうか（計画書 F2）。"""
    p = Path(path)
    if p.suffix.lower() not in (".gif", ".webp"):
        return False
    try:
        from PIL import Image

        with Image.open(p) as im:
            return bool(getattr(im, "is_animated", False))
    except Exception:
        return False
