"""プロジェクトファイルの保存・読込（計画書 7 章 / M5）。

JSON を zlib で圧縮して `.rslide` に保存する（1 万枚でも小さい）。
版数フィールドで将来のスキーマ変更に対応する。
"""
from __future__ import annotations

import json
import logging
import zlib
from pathlib import Path

from .models import PROJECT_VERSION, Project

log = logging.getLogger(__name__)

PROJECT_EXTENSION = ".rslide"
_MAGIC = b"RSLD1"  # 先頭マジック（zlib 圧縮の前）


def save_project(project: Project, path: Path | str) -> Path:
    """プロジェクトを zlib 圧縮 JSON として保存する。"""
    p = Path(path)
    if p.suffix.lower() != PROJECT_EXTENSION:
        p = p.with_suffix(PROJECT_EXTENSION)
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=None)
    blob = _MAGIC + zlib.compress(payload.encode("utf-8"), level=6)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(blob)
    tmp.replace(p)  # 原子的に差し替え
    return p


def load_project(path: Path | str) -> Project:
    """プロジェクトを読み込む。マジックが無ければ素の JSON として扱う。"""
    p = Path(path)
    blob = p.read_bytes()
    if blob.startswith(_MAGIC):
        payload = zlib.decompress(blob[len(_MAGIC):])
    else:
        payload = blob
    data = json.loads(payload.decode("utf-8"))
    version = int(data.get("version", PROJECT_VERSION))
    if version > PROJECT_VERSION:
        log.warning("より新しい版数のプロジェクトです（version=%s）", version)
    return Project.from_dict(data)


def try_load_project(path: Path | str) -> tuple[Project | None, str | None]:
    """失敗しても例外を投げず (project, error_message) を返す（UI 用）。"""
    try:
        return load_project(path), None
    except FileNotFoundError:
        return None, "ファイルが見つかりません"
    except (ValueError, zlib.error, OSError) as exc:
        log.exception("プロジェクト読込失敗: %s", path)
        return None, f"読み込みに失敗しました（{exc}）"
