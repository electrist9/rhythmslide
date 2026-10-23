"""アプリ共通の設定（前回のフォルダなど）をデータ保存先に JSON で保存する。

セッションをまたいで「前回選択したフォルダ」を記憶するために使う。
読み書きに失敗してもアプリを止めない（警告のみ）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .paths import ensure_data_dir

log = logging.getLogger(__name__)

CONFIG_NAME = "config.json"


def config_path() -> Path:
    """設定ファイルのパス（書き込めるデータ保存先に確定して返す）。"""
    return ensure_data_dir() / CONFIG_NAME


def load() -> dict:
    """設定 dict を読む。存在しない・壊れている場合は空 dict。"""
    try:
        p = config_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError) as exc:
        log.warning("設定の読み込みに失敗: %s", exc)
    return {}


def save(data: dict) -> None:
    """設定 dict を保存する。失敗しても例外は投げない。"""
    try:
        p = config_path()
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        log.warning("設定の保存に失敗: %s", exc)


# FunScript 切替ルールの名前付きプリセットは config.json とは別ファイルに保存する。
PRESETS_NAME = "funscript_presets.json"


def funscript_presets_path() -> Path:
    """FunScript プリセット保存ファイルのパス（書き込めるデータ保存先に確定）。"""
    return ensure_data_dir() / PRESETS_NAME


def load_funscript_presets() -> list:
    """FunScript プリセット一覧を別ファイルから読む（壊れていれば空 list）。

    旧形式（config.json 内の ``funscript_presets``）が残っていれば、初回に
    別ファイルへ移行し、config.json からはそのキーを削除する。
    """
    try:
        p = funscript_presets_path()
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
    except (OSError, ValueError) as exc:
        log.warning("FunScript プリセットの読み込みに失敗: %s", exc)
    # 旧形式からの一度きりの移行
    cfg = load()
    legacy = cfg.get("funscript_presets")
    if isinstance(legacy, list):
        save_funscript_presets(legacy)
        cfg.pop("funscript_presets", None)
        save(cfg)
        return legacy
    return []


def save_funscript_presets(presets: list) -> None:
    """FunScript プリセット一覧を別ファイルに保存する。失敗しても例外は投げない。"""
    try:
        p = funscript_presets_path()
        p.write_text(
            json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        log.warning("FunScript プリセットの保存に失敗: %s", exc)
