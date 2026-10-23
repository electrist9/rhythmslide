"""素材管理: 写真フォルダの再帰スキャン（計画書 F2 / M1 / N4）。

- 単一フォルダ指定で配下を再帰的に検索（os.scandir で高速走査）
- junction / シンボリックリンクによる循環は実パスで訪問済み集合を作り回避
- アクセス拒否フォルダはログして継続（クラッシュしない）
- 撮影日は EXIF、無ければファイル更新日で代替してソート
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .exif import is_animated, read_taken_at
from .models import PhotoItem, isoformat

log = logging.getLogger(__name__)

IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".jpe", ".png", ".gif", ".webp"}
)

MAX_DEPTH = 64


@dataclass
class ScanResult:
    photos: list[PhotoItem] = field(default_factory=list)
    #: (パス, 理由) の形でスキップした項目
    skipped: list[tuple[str, str]] = field(default_factory=list)
    directories: int = 0

    @property
    def count(self) -> int:
        return len(self.photos)


def _is_reparse(entry: os.DirEntry) -> bool:
    """junction / シンボリックリンクかどうか（Windows の junction を拾う）。"""
    try:
        if entry.is_symlink():
            return True
    except OSError:
        return False
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    reparse = getattr(st, "st_reparse_tag", 0)
    return bool(attrs & 0x400) or reparse == 0xA0000003  # FILE_ATTRIBUTE_REPARSE_POINT / IO_REPARSE_TAG_MOUNT_POINT


def scan_photo_root(
    root: Path | str,
    *,
    extensions: frozenset[str] = IMAGE_EXTENSIONS,
    read_exif: bool = True,
    follow_links: bool = True,
    max_photos: int | None = None,
) -> ScanResult:
    """写真フォルダを再帰スキャンして撮影日順の PhotoItem 一覧を返す。"""
    root_path = Path(root)
    result = ScanResult()
    if not root_path.exists():
        result.skipped.append((str(root_path), "フォルダが存在しません"))
        return result
    if not root_path.is_dir():
        result.skipped.append((str(root_path), "フォルダではありません"))
        return result

    root_resolved = _safe_realpath(root_path)
    visited: set[str] = {root_resolved}
    stack: list[tuple[Path, int]] = [(root_path, 0)]

    while stack:
        current, depth = stack.pop()
        try:
            entries = list(os.scandir(current))
        except PermissionError:
            log.warning("アクセス拒否: %s", current)
            result.skipped.append((str(current), "アクセス拒否"))
            continue
        except OSError as exc:
            log.warning("スキャン失敗 %s: %s", current, exc)
            result.skipped.append((str(current), f"読み取りエラー ({exc.strerror or exc})"))
            continue

        result.directories += 1
        rel_folder = "" if current == root_path else _relative_folder(current, root_path)

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    child = Path(entry.path)
                    if _is_reparse(entry):
                        if not follow_links:
                            result.skipped.append((entry.path, "リンク先フォルダ（スキップ）"))
                            continue
                        real = _safe_realpath(child)
                        if real in visited:
                            result.skipped.append((entry.path, "循環リンク（スキップ）"))
                            continue
                        visited.add(real)
                    if depth + 1 > MAX_DEPTH:
                        result.skipped.append((entry.path, "深さ上限"))
                        continue
                    stack.append((child, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=True):
                    continue
                suffix = os.path.splitext(entry.name)[1].lower()
                if suffix not in extensions:
                    continue
                path = Path(entry.path)
                taken = None
                if read_exif:
                    taken = read_taken_at(path)
                stamp = taken or _mtime(path)
                result.photos.append(
                    PhotoItem(
                        path=str(path),
                        taken_at=isoformat(stamp),
                        folder=rel_folder,
                        animated=_animated_hint(path, suffix),
                    )
                )
                if max_photos is not None and len(result.photos) >= max_photos:
                    return _finalize(result)
            except OSError as exc:  # 個別ファイルのエラーでは止まらない
                log.warning("ファイル読み飛ばし %s: %s", entry.path, exc)
                result.skipped.append((entry.path, f"ファイルエラー ({exc.strerror or exc})"))

    return _finalize(result)


def _animated_hint(path: Path, suffix: str) -> bool | None:
    """スキャン段階ではアニメ GIF を開かない（1 万枚でも速くするため遅延判定）。"""
    if suffix in (".gif", ".webp"):
        return None  # 未調査
    return False


def _finalize(result: ScanResult) -> ScanResult:
    result.photos.sort(key=lambda p: (p.taken_at or "9999", p.path))
    return result


def _relative_folder(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def _safe_realpath(path: Path) -> str:
    try:
        return os.path.realpath(str(path)).lower()
    except OSError:
        return str(path).lower()


def _mtime(path: Path) -> datetime:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime)
    except OSError:
        return datetime.fromtimestamp(0)


def enrich_dates(photos: list[PhotoItem], *, executor: Executor | None = None) -> list[Future]:
    """EXIF 撮影日が未読の項目へ撮影日を与えるためのヘルパー。

    UI 側では QThreadPool ではなく ThreadPoolExecutor を使い、完了ごとに再ソートする。
    戻り値は Future のリスト（完了進捗の監視用）。
    """
    futures: list[Future] = []
    if executor is None:
        for item in photos:
            _apply_date(item)
        return futures
    for item in photos:
        if item.taken_at is None:
            futures.append(executor.submit(_apply_date, item))
    return futures


def _apply_date(item: PhotoItem) -> PhotoItem:
    dt = read_taken_at(item.path)
    if dt is not None:
        item.taken_at = isoformat(dt)
    return item


def resorted(photos: list[PhotoItem]) -> list[PhotoItem]:
    """撮影日 → パスの順。撮影日不明は末尾にまとめる。"""
    return sorted(photos, key=lambda p: (p.taken_at or "9999", p.path))


def folders_of(photos: list[PhotoItem]) -> list[str]:
    """素材パネルのフォルダフィルタ用（"." は直下）。"""
    seen: set[str] = set()
    for p in photos:
        seen.add(p.folder or ".")
    return sorted(seen)
