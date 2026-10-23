"""全画面プレゼンテーション（計画書 F10 / M4）。

選択スクリーンにボーダーレス全画面を開き、同じ SlideRenderer で描画する。
操作はキーボード中心（Space 一時停止 / ←→ 送り / Esc 終了）。音声クロックは
MainWindow が同じ時刻を push するため、プレビューと完全に同期する。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QWidget

from ..core.imagecache import ImageCache
from ..core.models import PresentationSettings, Slide
from .renderer import SlideRenderer


class PresentationWindow(QWidget):
    closeRequested = Signal()
    nextRequested = Signal()
    prevRequested = Signal()
    togglePauseRequested = Signal()

    def __init__(self, cache: ImageCache, settings: PresentationSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.renderer = SlideRenderer(cache, settings.background)
        self.renderer.set_background_mode(settings.background_mode)
        self._slides: list[Slide] = []
        self._t = 0.0
        self.setWindowTitle("RhythmSlide プレゼンテーション")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setCursor(Qt.CursorShape.BlankCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(33)
        self._anim_timer.timeout.connect(self._tick_anim)
        self._anim_timer.start()

    # ------------------------------------------------------------------
    def show_on_screen(self, monitor: str = "auto") -> None:
        screen = self._pick_screen(monitor)
        if screen is not None:
            self.setScreen(screen)
            geo = screen.geometry()
            self.setGeometry(geo)
        self.showFullScreen()
        self.activateWindow()
        self.raise_()

    def _pick_screen(self, monitor: str):
        screens = QGuiApplication.screens()
        if not screens:
            return None
        if monitor and monitor != "auto":
            for s in screens:
                if s.name() == monitor:
                    return s
            try:
                idx = int(monitor)
                if 0 <= idx < len(screens):
                    return screens[idx]
            except ValueError:
                pass
        # auto: 一番広いスクリーン（外部ディスプレイが出ていることが多い）
        return max(screens, key=lambda s: s.geometry().width() * s.geometry().height())

    # ------------------------------------------------------------------
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

    def set_render_interval(self, ms: int) -> None:
        """GIF アニメと再描画を駆動する自前タイマの間隔（ms）を設定する。"""
        self._anim_timer.setInterval(max(1, int(ms)))

    def _tick_anim(self) -> None:
        self.renderer.advance_animation(self._anim_timer.interval())
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        self.renderer.paint(painter, self.width(), self.height(), self._t, self._slides)
        painter.end()

    # ------------------------------------------------------------------
    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        if key == Qt.Key.Key_Escape:
            self.closeRequested.emit()
        elif key in (Qt.Key.Key_Right, Qt.Key.Key_PageDown):
            self.nextRequested.emit()
        elif key in (Qt.Key.Key_Left, Qt.Key.Key_PageUp):
            self.prevRequested.emit()
        elif key == Qt.Key.Key_Space:
            self.togglePauseRequested.emit()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._anim_timer.stop()
        self.closeRequested.emit()
        super().closeEvent(event)
