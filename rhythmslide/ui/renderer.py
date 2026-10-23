"""スライド描画の共通エンジン（計画書 F8 / F9 / 5.3）。

プレビューとプレゼンで同じ描画経路を使い、トランジションと Ken Burns を
「時刻 t の純粋な状態」から毎回描画する（QPropertyAnimation に頼らない）。
これにより音声クロック ±30ms で正確に切り替わる。
"""
from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap

from ..core.imagecache import AnimatedImage, ImageCache
from ..core.models import Slide
from ..core.renderlogic import RenderState, render_state

# Ken Burns のズーム上限（計画書: 控えめな動き）
_KB_ZOOM_IN = (1.02, 1.14)
_KB_ZOOM_OUT = (1.14, 1.02)
_KB_PAN_ZOOM = 1.12


def _ease(p: float) -> float:
    """滑らかな加減速（smoothstep）。"""
    p = min(1.0, max(0.0, p))
    return p * p * (3.0 - 2.0 * p)


class SlideRenderer:
    def __init__(self, cache: ImageCache, background: str = "#000000") -> None:
        self.cache = cache
        self.background = QColor(background)
        #: "solid" = 背景色で一律 / "dominant" = 画像の支配色で余白を塗る
        self.background_mode = "solid"
        self._pixmaps: dict[str, QPixmap] = {}
        self._animated: dict[str, AnimatedImage] = {}
        self._anim_clock: dict[str, float] = {}
        self._dominant: dict[str, Optional[QColor]] = {}

    def set_background(self, color: str) -> None:
        self.background = QColor(color)

    def set_background_mode(self, mode: str) -> None:
        self.background_mode = mode if mode in ("solid", "dominant") else "solid"

    # ------------------------------------------------------------------
    def paint(self, painter: QPainter, vw: int, vh: int, t: float, slides: list[Slide]) -> None:
        rs = render_state(slides, t) if (slides and vw > 0 and vh > 0) else None
        bg = self.background
        if self.background_mode == "dominant" and rs is not None and rs.current is not None:
            c = self._dominant_for(rs.current, vw, vh)
            if c is not None:
                bg = c
        painter.fillRect(0, 0, vw, vh, bg)
        if rs is None or rs.current is None:
            return
        self._draw(painter, rs.current, vw, vh, rs.ken_progress, 1.0, t)
        if rs.incoming is not None:
            self._draw_transition(painter, rs, vw, vh, t)

    # ------------------------------------------------------------------
    def _draw_transition(self, painter: QPainter, rs: RenderState, vw: int, vh: int, t: float) -> None:
        e = _ease(rs.progress)
        cur, nxt = rs.current, rs.incoming
        kind = rs.transition

        if kind == "crossfade" or kind == "fade":
            self._draw(painter, nxt, vw, vh, 0.0, e, t)
        elif kind == "wipe_left":
            painter.save()
            painter.setClipRect(QRect(0, 0, int(vw * e), vh))
            self._draw(painter, nxt, vw, vh, 0.0, 1.0, t)
            painter.restore()
        elif kind == "wipe_right":
            painter.save()
            painter.setClipRect(QRect(int(vw * (1 - e)), 0, int(vw * e), vh))
            self._draw(painter, nxt, vw, vh, 0.0, 1.0, t)
            painter.restore()
        elif kind == "slide_left":
            painter.save()
            painter.translate(vw * (e - 1.0), 0)
            self._draw(painter, nxt, vw, vh, 0.0, 1.0, t)
            painter.restore()
        elif kind == "slide_right":
            painter.save()
            painter.translate(vw * (1.0 - e), 0)
            self._draw(painter, nxt, vw, vh, 0.0, 1.0, t)
            painter.restore()
        elif kind == "zoom":
            # 次スライドを 0.9→1.0 で拡大しながら重ねる
            self._draw(painter, nxt, vw, vh, 0.0, e, t, extra_zoom=0.9 + 0.1 * e)
        else:
            self._draw(painter, nxt, vw, vh, 0.0, e, t)

    # ------------------------------------------------------------------
    def _draw(
        self,
        painter: QPainter,
        slide: Slide,
        vw: int,
        vh: int,
        ken: float,
        opacity: float,
        t: float,
        extra_zoom: float = 1.0,
    ) -> None:
        img = self._image_for(slide, vw, vh)
        if img is None:
            return
        if slide.ken_burns:
            zoom, panx, pany = self._ken_params(slide.motion, ken)
        else:
            zoom, panx, pany = 1.0, 0.0, 0.0
        zoom *= extra_zoom
        self._blit_cover(painter, img, vw, vh, zoom, panx, pany, opacity)

    def _image_for(self, slide: Slide, vw: int, vh: int) -> Optional[QImage]:
        """スライドに対応する QImage（アニメなら現在フレーム）を返す。"""
        if slide.animated:
            anim = self._animated.get(slide.path)
            if anim is None:
                anim = self.cache.animated(slide.path, vw, vh)
                self._animated[slide.path] = anim or False  # False で非アニメ扱い
            if isinstance(anim, AnimatedImage) and anim.is_animated:
                return self._current_frame(anim, slide.path)
        key = slide.path
        pix = self._pixmaps.get(key)
        if pix is None:
            qimg = self.cache.image(slide.path, int(vw * 1.3), int(vh * 1.3), self._crop(slide))
            if qimg is None:
                return None
            pix = QPixmap.fromImage(qimg)
            self._pixmaps[key] = pix
        return pix.toImage() if not pix.isNull() else None

    def _crop(self, slide: Slide):
        # Slide には crop が無い（PhotoItem 側）ため、ここでは None。
        return None

    # ------------------------------------------------------------------
    def _dominant_for(self, slide: Slide, vw: int, vh: int) -> Optional[QColor]:
        """スライド画像の支配色（キャッシュ付き）。取得できなければ None。"""
        key = slide.path
        if key in self._dominant:
            return self._dominant[key]
        img = self._image_for(slide, vw, vh)
        color = self._dominant_color(img) if img is not None else None
        self._dominant[key] = color
        return color

    @staticmethod
    def _dominant_color(img: QImage) -> Optional[QColor]:
        """画像を縮小し、色を 16 段階に量子化したヒストグラムで最頻色の平均を返す。"""
        if img is None or img.width() <= 0 or img.height() <= 0:
            return None
        small = img.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                           Qt.TransformationMode.FastTransformation)
        if small.isNull():
            return None
        counts: dict[tuple[int, int, int], int] = {}
        sums: dict[tuple[int, int, int], tuple[int, int, int]] = {}
        for y in range(small.height()):
            for x in range(small.width()):
                c = small.pixelColor(x, y)
                bucket = (c.red() >> 4, c.green() >> 4, c.blue() >> 4)
                counts[bucket] = counts.get(bucket, 0) + 1
                s = sums.get(bucket, (0, 0, 0))
                sums[bucket] = (s[0] + c.red(), s[1] + c.green(), s[2] + c.blue())
        if not counts:
            return None
        best = max(counts, key=lambda k: counts[k])
        r, g, b = sums[best]
        n = counts[best] or 1
        return QColor(r // n, g // n, b // n)

    def _current_frame(self, anim: AnimatedImage, path: str) -> QImage:
        # 実時間ベースでフレームを進める
        now = self._anim_clock.get(path, 0.0)
        total = sum(anim.durations_ms) or 1
        pos = now % total
        idx = 0
        acc = 0
        for i, d in enumerate(anim.durations_ms):
            acc += d
            if pos < acc:
                idx = i
                break
        return anim.frames[min(idx, len(anim.frames) - 1)]

    def advance_animation(self, dt_ms: float) -> None:
        for path in self._animated:
            self._anim_clock[path] = self._anim_clock.get(path, 0.0) + dt_ms

    # ------------------------------------------------------------------
    def _ken_params(self, motion: int, p: float) -> tuple[float, float, float]:
        if motion == 0:  # ズームイン
            z = _KB_ZOOM_IN[0] + (_KB_ZOOM_IN[1] - _KB_ZOOM_IN[0]) * p
            return z, 0.0, 0.0
        if motion == 1:  # ズームアウト
            z = _KB_ZOOM_OUT[0] + (_KB_ZOOM_OUT[1] - _KB_ZOOM_OUT[0]) * p
            return z, 0.0, 0.0
        if motion == 2:  # 左→右
            return _KB_PAN_ZOOM, -1.0 + 2.0 * p, 0.0
        # 上→下
        return _KB_PAN_ZOOM, 0.0, -1.0 + 2.0 * p

    def _blit_cover(
        self,
        painter: QPainter,
        img: QImage,
        vw: int,
        vh: int,
        zoom: float,
        panx: float,
        pany: float,
        opacity: float,
    ) -> None:
        iw, ih = img.width(), img.height()
        if iw <= 0 or ih <= 0:
            return
        # contain: アスペクト比を保ち画面内に収める（縦長は縦いっぱい・横長は横いっぱい）。
        # 余白は背景色。panx=pany=0 なら -ox/2 で中央配置。
        fit = min(vw / iw, vh / ih) * zoom
        dw, dh = iw * fit, ih * fit
        ox = dw - vw
        oy = dh - vh
        dx = -ox * (0.5 + 0.5 * panx)
        dy = -oy * (0.5 + 0.5 * pany)
        painter.setOpacity(max(0.0, min(1.0, opacity)))
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.drawImage(QRect(int(round(dx)), int(round(dy)), int(round(dw)), int(round(dh))), img)
        painter.setOpacity(1.0)

    def clear_cache(self) -> None:
        self._pixmaps.clear()
        self._animated.clear()
        self._anim_clock.clear()
        self._dominant.clear()
