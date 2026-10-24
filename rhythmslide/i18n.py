"""多言語対応（方式 A: Qt tr() + .ts/.qm + QTranslator）。

言語は config.json の ``language`` キーで記憶し、**再起動で反映**する。
既定は日本語（原文）。未設定時は OS のロケールから en / zh を推定し、
いずれでもなければ日本語にフォールバックする。

翻訳ファイル（``rhythmslide_<lang>.qm``）は本モジュールと同じ ``locale/``
フォルダに置く。PyInstaller で凍結した実行ファイルでは ``sys._MEIPASS`` 配下
の ``locale/`` から読む。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import QLocale, QTranslator
from PySide6.QtWidgets import QApplication

log = logging.getLogger(__name__)

#: 対応言語（コード）。日本語は原文なので翻訳ファイル不要。
SUPPORTED_LANGS: tuple[str, ...] = ("ja", "en", "zh")

#: 言語メニューに表示するラベル（コード → 自国語表記）。翻訳しない固有名。
LANGUAGE_LABELS: dict[str, str] = {
    "ja": "日本語",
    "en": "English",
    "zh": "中文",
}


def fmt(template: str, *args: object) -> str:
    """Qt 形式の %1, %2, ... プレースホルダを順に埋めて返す。

    PySide6 の QObject.tr() は Python の str を返すため、Qt の .arg() は
    使えない。翻訳済みの文言（原文・翻訳文ともに %1 形式を保持）に対し、
    このヘルパーで位置引数を置き換える。翻訳者は %1 の並び順を維持する。
    """
    out = template
    for i, a in enumerate(args, 1):
        out = out.replace(f"%{i}", str(a))
    return out


# インストール済み翻訳者を保持する（Python の GC で解放されぬよう参照を持つ）。
_installed: list[QTranslator] = []


def locale_dir() -> Path:
    """翻訳ファイル（.qm）が置かれるフォルダ。"""
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
        return base / "locale"
    return Path(__file__).resolve().parent / "locale"


def resolve_language(config: dict) -> str:
    """config から言語を決める。未設定なら OS ロケールから推定、既定は ja。"""
    lang = str(config.get("language", "") or "").strip().lower()
    if lang in SUPPORTED_LANGS:
        return lang
    base = QLocale.system().name().split("_")[0].lower()
    if base in SUPPORTED_LANGS:
        return base
    return "ja"


def install_translator(app: QApplication, language: str) -> None:
    """言語に対応する .qm をロードしてアプリにインストールする。

    日本語は原文なので翻訳不要（既存の tr() 原文がそのまま使われる）。
    翻訳者オブジェクトはモジュール側で参照を保持し、GC で解放されないようにする。
    """
    _installed.clear()
    if language == "ja":
        return
    path = locale_dir() / f"rhythmslide_{language}.qm"
    if not path.exists():
        log.warning("翻訳ファイルが見つかりません: %s（日本語で表示）", path)
        return
    translator = QTranslator()
    if translator.load(str(path)):
        app.installTranslator(translator)
        _installed.append(translator)
        log.info("翻訳を適用: %s (%s)", language, path)
    else:
        log.warning("翻訳ファイルの読み込みに失敗: %s（日本語で表示）", path)
