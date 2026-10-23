"""プロジェクトファイルの保存・読込（計画書 7 章 / M5）。

`.rslide` に素の JSON（UTF-8・indent=2）で保存する（git で差分が見やすい）。
旧形式（マジック + zlib 圧縮）のファイルも load_project で読み込める。
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
_MAGIC = b"RSLD1"  # 旧形式（zlib 圧縮）の読込互換用。保存では使わない。


def save_project(project: Project, path: Path | str) -> Path:
    """プロジェクトを素の JSON（UTF-8・indent=2）として保存する。

    旧形式（マジック + zlib 圧縮）の読み込みは load_project 側で互換維持する。
    """
    p = Path(path)
    if p.suffix.lower() != PROJECT_EXTENSION:
        p = p.with_suffix(PROJECT_EXTENSION)
    payload = json.dumps(project.to_dict(), ensure_ascii=False, indent=2)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(p)  # 原子的に差し替え
    return p


def load_project(path: Path | str) -> Project:
    """プロジェクトを読み込む。素の JSON を読む（旧形式の zlib 圧縮も自動判別）。"""
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
    """失敗しても例外を投げず (project, error_code) を返す（UI 用）。

    error_code は表示文言ではなく安定した識別子（"not_found" / "load_failed"）。
    UI 側で言語ごとの表示文言へ変換する。
    """
    try:
        return load_project(path), None
    except FileNotFoundError:
        return None, "not_found"
    except (ValueError, zlib.error, OSError) as exc:
        log.exception("プロジェクト読込失敗: %s", path)
        return None, "load_failed"
