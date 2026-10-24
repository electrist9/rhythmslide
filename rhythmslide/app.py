"""アプリのエントリポイント（計画書 M0 / F12）。

`python -m rhythmslide` で起動する。ログ設定、Qt アプリ生成、
メインウィンドウ表示、コマンドラインのプロジェクトファイル引数を扱う。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__
from .core import appconfig
from .core.paths import ensure_data_dir
from .i18n import install_translator, resolve_language
from .ui.mainwindow import MainWindow

log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    _configure_logging()
    argv = list(sys.argv[1:] if argv is None else argv)

    # HiDPI: Qt6 は既定でスケール追従。追加の角丸/角シャープ指定のみ補正。
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_DontUseNativeMenuBar, False)

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_NAME)

    data = ensure_data_dir()
    log.info("データ保存先: %s", data)

    # 言語は config.json から読み、翻訳をインストールしてから UI を構築する
    # （翻訳は MainWindow 構築前に適用する必要がある）。
    language = resolve_language(appconfig.load())
    install_translator(app, language)
    log.info("表示言語: %s", language)

    win = MainWindow()
    win.show()

    # プロジェクトファイル引数
    for arg in argv:
        p = Path(arg)
        if p.suffix.lower() == ".rslide" and p.exists():
            win._load_project_path(str(p))  # noqa: SLF001 - 起動時ヘルパ
            break

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
