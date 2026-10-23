"""タイムライン編集（計画書 F5 / M4）。

横スクロールする時間軸にビート目盛りとスライドブロックを描画し、
ドラッグで開始位置をビートへスナップ移動、右端ドラッグで長さ変更、
Shift で自由配置、右クリックでトランジション/ロックを変更できる。
編集内容は MainWindow 側で QUndoStack 経由のモデル変更に変換する。
"""
from __future__ import annotations

from bisect import bisect_left

from PySide6.QtCore import Qt, QRectF, Signal, QPoint
from PySide6.QtGui import (
    QAction, QBrush, QColor, QFont, QMouseEvent, QPainter, QPen, QPolygonF,
)
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QMenu, QScrollArea, QSizePolicy, QWidget

from ..core.models import Slide, TRANSITION_TYPES

_ROW_H = 56
_RULER_H = 26
_MIN_W = 24
_HIT_EDGE = 8

_BLOCK_COLORS = [
    QColor(70, 130, 190), QColor(90, 160, 120), QColor(170, 120, 90),
    QColor(140, 110, 170),
]


class TimelineCanvas(QWidget):
    slideMoved = Signal(int, float, float)      # photo_index, start, duration
    transitionRequested = Signal(int, str)
    lockToggled = Signal(int)
    seekRequested = Signal(float)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._slides: list[Slide] = []
        self._beats: list[float] = []
        self._duration = 0.0
        self._px_per_sec = 40.0
        self._playhead = 0.0
        self.setMinimumHeight(_RULER_H + _ROW_H + 20)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMouseTracking(True)

        self._drag_index: int | None = None
        self._drag_mode = ""  # move | resize
        self._drag_orig_start = 0.0
        self._drag_orig_dur = 0.0
        self._drag_offset = 0.0
        self._preview_start = 0.0
        self._preview_dur = 0.0

    # ---- データ --------------------------------------------------------
    def set_data(self, slides: list[Slide], beats: list[float], duration: float) -> None:
        self._slides = slides
        self._beats = beats
        self._duration = max(duration, (slides[-1].end if slides else 0.0))
        self._update_width()
        self.update()

    def set_playhead(self, t: float) -> None:
        self._playhead = t
        self.update()

    def _update_width(self) -> None:
        w = int(self._duration * self._px_per_sec) + 40
        self.setMinimumWidth(max(w, 100))

    # ---- 座標変換 ------------------------------------------------------
    def _x(self, t: float) -> float:
        return t * self._px_per_sec

    def x_of_time(self, t: float) -> float:
        """時刻 t のキャンバス内 X 座標（スクロール追従用）。"""
        return self._x(t)

    def _t(self, x: float) -> float:
        return x / self._px_per_sec

    def _snap(self, t: float) -> float:
        if not self._beats:
            return max(0.0, t)
        i = bisect_left(self._beats, t)
        best = t
        for j in (i - 1, i):
            if 0 <= j < len(self._beats):
                if abs(self._beats[j] - t) < abs(best - t):
                    best = self._beats[j]
        return max(0.0, best)

    def _block_rect(self, s: Slide) -> QRectF:
        x = self._x(s.start)
        w = max(_MIN_W, (s.end - s.start) * self._px_per_sec)
        return QRectF(x, _RULER_H, w, _ROW_H)

    def _hit(self, pos: QPointF) -> tuple[int | None, str]:
        for i, s in enumerate(self._slides):
            r = self._block_rect(s)
            if r.contains(pos):
                if pos.x() >= r.right() - _HIT_EDGE:
                    return i, "resize"
                return i, "move"
        return None, ""

    # ---- 描画 ----------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()
        p.fillRect(0, 0, w, h, QColor(28, 28, 30))

        self._draw_ruler(p, w)
        self._draw_beats(p, h)
        self._draw_blocks(p)
        self._draw_playhead(p, h)
        p.end()

    def _draw_ruler(self, p: QPainter, w: int) -> None:
        p.fillRect(0, 0, w, _RULER_H, QColor(40, 40, 44))
        p.setPen(QPen(QColor(180, 180, 180)))
        step = self._ruler_step()
        t = 0.0
        while t * self._px_per_sec < w:
            x = self._x(t)
            p.drawLine(int(x), _RULER_H - 6, int(x), _RULER_H)
            p.drawText(QRectF(x + 2, 2, 60, 16), Qt.AlignmentFlag.AlignLeft, f"{t:.0f}s")
            t += step

    def _ruler_step(self) -> float:
        # 目盛り間隔が 60px 以上になる最小の「良い」間隔
        for step in (0.5, 1, 2, 5, 10, 15, 30, 60):
            if step * self._px_per_sec >= 60:
                return step
        return 60

    def _draw_beats(self, p: QPainter, h: int) -> None:
        p.setPen(QPen(QColor(90, 90, 96), 1))
        top = _RULER_H
        for b in self._beats:
            x = self._x(b)
            if 0 <= x <= self.width():
                p.drawLine(int(x), top, int(x), h)

    def _draw_blocks(self, p: QPainter) -> None:
        for i, s in enumerate(self._slides):
            if self._drag_index == i:
                start, dur = self._preview_start, self._preview_dur
            else:
                start, dur = s.start, s.end - s.start
            x = self._x(start)
            w = max(_MIN_W, dur * self._px_per_sec)
            r = QRectF(x, _RULER_H, w, _ROW_H)
            color = _BLOCK_COLORS[i % len(_BLOCK_COLORS)]
            p.setBrush(QBrush(color))
            p.setPen(QPen(QColor(20, 20, 20) if not s.locked else QColor(240, 200, 60), 2))
            p.drawRoundedRect(r, 4, 4)
            # ラベル
            p.setPen(QPen(QColor(245, 245, 245)))
            name = s.path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            p.drawText(r.adjusted(6, 4, -6, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, name)
            p.setPen(QPen(QColor(220, 220, 220)))
            info = f"{dur:.1f}s"
            if s.locked:
                info += " 🔒"
            p.drawText(r.adjusted(6, 0, -6, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, info)

    def _draw_playhead(self, p: QPainter, h: int) -> None:
        x = self._x(self._playhead)
        if 0 <= x <= self.width():
            p.setPen(QPen(QColor(255, 90, 90), 2))
            p.drawLine(int(x), 0, int(x), h)
            p.setBrush(QBrush(QColor(255, 90, 90)))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x - 5, 0), QPointF(x + 5, 0), QPointF(x, 8)]))

    # ---- マウス --------------------------------------------------------
    def mousePressEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            idx, mode = self._hit(e.position())
            if idx is None:
                self.seekRequested.emit(max(0.0, self._t(e.position().x())))
                return
            s = self._slides[idx]
            self._drag_index = idx
            self._drag_mode = mode
            self._drag_orig_start = s.start
            self._drag_orig_dur = s.end - s.start
            self._preview_start = s.start
            self._preview_dur = self._drag_orig_dur
            self._drag_offset = self._t(e.position().x()) - s.start
        elif e.button() == Qt.MouseButton.RightButton:
            idx, _ = self._hit(e.position())
            if idx is not None:
                self._context_menu(e.globalPosition().toPoint(), idx)

    def mouseMoveEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag_index is None:
            return
        free = bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        t = self._t(e.position().x())
        s = self._slides[self._drag_index]
        if self._drag_mode == "move":
            new_start = t - self._drag_offset
            if not free:
                new_start = self._snap(new_start)
            self._preview_start = max(0.0, new_start)
            self._preview_dur = self._drag_orig_dur
        else:  # resize
            end = t
            if not free:
                end = self._snap(end)
            new_dur = max(0.2, end - self._drag_orig_start)
            self._preview_start = self._drag_orig_start
            self._preview_dur = new_dur
        self.update()

    def mouseReleaseEvent(self, e: QMouseEvent) -> None:  # noqa: N802
        if self._drag_index is None:
            return
        s = self._slides[self._drag_index]
        self.slideMoved.emit(s.photo_index, self._preview_start, self._preview_dur)
        self._drag_index = None
        self._drag_mode = ""

    def wheelEvent(self, e) -> None:  # noqa: N802
        delta = e.angleDelta().y()
        if delta == 0:
            return
        factor = 1.15 if delta > 0 else 1 / 1.15
        self._px_per_sec = max(4.0, min(400.0, self._px_per_sec * factor))
        self._update_width()
        self.update()

    # ---- コンテキストメニュー -----------------------------------------
    def _context_menu(self, global_pos: QPoint, idx: int) -> None:
        s = self._slides[idx]
        menu = QMenu(self)
        trans = menu.addMenu("トランジション")
        current = s.transition
        for kind in TRANSITION_TYPES:
            act = trans.addAction(kind)
            act.setCheckable(True)
            act.setChecked(kind == current)
        chosen: dict[str, str | None] = {"t": None}

        def _pick(kind: str):
            chosen["t"] = kind

        for act in trans.actions():
            act.triggered.connect(lambda checked, k=act.text(): _pick(k))
        menu.addSeparator()
        lock_act = QAction("ロック解除" if s.locked else "ロック", self)
        menu.addAction(lock_act)

        action = menu.exec(global_pos)
        if action is lock_act:
            self.lockToggled.emit(s.photo_index)
        elif chosen["t"] and chosen["t"] != current:
            self.transitionRequested.emit(s.photo_index, chosen["t"])


class TimelineView(QScrollArea):
    """TimelineCanvas を横スクロール付きで収めるラッパ。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.canvas = TimelineCanvas(self)
        self.setWidget(self.canvas)
        self.setWidgetResizable(False)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 再生位置の自動追従（再生中に再生箇所が視界から外れないようにスクロール）
        self.auto_follow = True
        # シグナル中継
        self.slideMoved = self.canvas.slideMoved
        self.transitionRequested = self.canvas.transitionRequested
        self.lockToggled = self.canvas.lockToggled
        self.seekRequested = self.canvas.seekRequested

    def set_data(self, slides, beats, duration) -> None:
        self.canvas.set_data(slides, beats, duration)

    def set_playhead(self, t: float) -> None:
        self.canvas.set_playhead(t)
        if self.auto_follow:
            self._follow_playhead(t)

    def _follow_playhead(self, t: float) -> None:
        """再生位置が視界の外に出そうなら、舒适的な位置まで横スクロールする。"""
        sb = self.horizontalScrollBar()
        if sb.maximum() <= 0:
            return  # スクロール不要（全尺が視界内）
        vp = self.viewport().width()
        if vp <= 0:
            return
        x = self.canvas.x_of_time(t)
        left = sb.value()
        right = left + vp
        margin = max(24, int(vp * 0.12))
        if x > right - margin:
            # 右端に近づいた → 視界の約 70% 位置に寄せる
            new = int(x - vp * 0.7)
        elif x < left + margin:
            # 左端の外（巻き戻し等）→ 視界の約 30% 位置に寄せる
            new = int(x - vp * 0.3)
        else:
            return  # 視界内に収まっており追従不要
        sb.setValue(max(0, min(new, sb.maximum())))
