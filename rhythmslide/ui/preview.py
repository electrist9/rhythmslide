"""プレビューキャンバス（計画書 F6 / M4）。

音声クロックの現在時刻を受け取り、SlideRenderer で毎フレーム描画するだけの
軽量 QWidget。再生・シークは MainWindow が担い、このウィジェットは「時刻 → 絵」に専念する。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget

from ..core.imagecache import ImageCache
from ..core.models import Slide
from .renderer import SlideRenderer


class PreviewWidget(QWidget):
    def __init__(self, cache: ImageCache, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.renderer = SlideRenderer(cache)
        self._slides: list[Slide] = []
        self._t = 0.0
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)

    def set_slides(self, slides: list[Slide]) -> None:
        self._slides = slides
        self.renderer.clear_cache()
        self.update()

    def set_time(self, t: float) -> None:
        self._t = t
        self.update()

    def set_background(self, color: str) -> None:
        self.renderer.set_background(color)
        self.update()

    def set_background_mode(self, mode: str) -> None:
        self.renderer.set_background_mode(mode)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt シグネチャ
        painter = QPainter(self)
        self.renderer.paint(painter, self.width(), self.height(), self._t, self._slides)
        painter.end()
