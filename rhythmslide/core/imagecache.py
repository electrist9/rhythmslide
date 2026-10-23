"""表示用画像キャッシュ（計画書 5.4 / F7 / N4）。

フル解像度をそのまま保持すると 1 万枚でメモリが膨らむため、
・表示領域に合わせた縮小デコード（QImageReader の setScaledSize / setClipRect）
・バイト数上限付き LRU
・アニメ GIF のフレーム先読み（フレーム数・辺長の上限で暴走防止）
を行う。Qt に依存するが、UI ロジックはここを通すことで単一経路に寄せる。
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage, QImageReader

log = logging.getLogger(__name__)

#: メモリキャッシュの上限（計画書 N4: 1 万枚で 1.5GB 以内の一端緒）
DEFAULT_BUDGET = 512 * 1024 * 1024
#: アニメ GIF の先読み上限（計画書 10 章リスク対策）
MAX_GIF_FRAMES = 200
MAX_GIF_SIDE = 2048


def _qimage_bytes(img: QImage) -> int:
    """QImage のメモリ使用量（PySide6 は sizeInBytes、旧 API は byteCount）。"""
    size_in_bytes = getattr(img, "sizeInBytes", None)
    if size_in_bytes is not None:
        return int(size_in_bytes())
    return int(img.byteCount())


@dataclass
class AnimatedImage:
    frames: list[QImage]
    durations_ms: list[int]
    loop: bool

    @property
    def is_animated(self) -> bool:
        return len(self.frames) > 1


class ImageCache:
    def __init__(self, budget_bytes: int = DEFAULT_BUDGET) -> None:
        self.budget = int(budget_bytes)
        self._used = 0
        self._cache: "OrderedDict[tuple, QImage]" = OrderedDict()
        self._animated: "OrderedDict[str, AnimatedImage]" = OrderedDict()

    # ---------------------------------------------------------------
    def image(self, path: Path | str, max_w: int, max_h: int, crop=None) -> QImage | None:
        """表示領域 (max_w,max_h) に収まるよう縮小デコードした QImage を返す。"""
        p = Path(path)
        key = (str(p).lower(), int(max_w), int(max_h), _crop_key(crop))
        hit = self._cache.get(key)
        if hit is not None:
            self._cache.move_to_end(key)
            return hit

        img = self._decode(p, max_w, max_h, crop)
        if img is None or img.isNull():
            return None
        self._cache[key] = img
        self._used += _qimage_bytes(img)
        self._cache.move_to_end(key)
        self._evict()
        return img

    def _decode(self, p: Path, max_w: int, max_h: int, crop) -> QImage | None:
        reader = QImageReader(str(p))
        reader.setAutoTransform(True)  # EXIF 回転を適用
        size = reader.size()
        if not size.isValid():
            return None
        w0, h0 = size.width(), size.height()
        if crop is not None:
            cx, cy = int(crop.x * w0), int(crop.y * h0)
            cw, ch = max(1, int(crop.w * w0)), max(1, int(crop.h * h0))
            reader.setClipRect(QRect(cx, cy, cw, ch))
            w0, h0 = cw, ch
        if max_w > 0 and max_h > 0 and (w0 > max_w or h0 > max_h):
            scale = min(max_w / w0, max_h / h0)
            reader.setScaledSize(QSize(max(1, int(w0 * scale)), max(1, int(h0 * scale))))
        img = reader.read()
        if img.isNull():
            log.debug("デコード失敗 %s: %s", p, reader.errorString())
            return None
        return img

    # ---------------------------------------------------------------
    def animated(self, path: Path | str, max_w: int, max_h: int) -> AnimatedImage | None:
        """アニメ GIF/WebP のフレームを先読み（上限付き）。非アニメは None。"""
        p = Path(path)
        key = str(p).lower()
        hit = self._animated.get(key)
        if hit is not None:
            self._animated.move_to_end(key)
            return hit
        anim = self._decode_animated(p, max_w, max_h)
        if anim is not None:
            self._animated[key] = anim
            self._animated.move_to_end(key)
            self._evict_animated()
        return anim

    def _decode_animated(self, p: Path, max_w: int, max_h: int) -> AnimatedImage | None:
        try:
            from PIL import Image, ImageSequence

            im = Image.open(str(p))
        except Exception as exc:  # noqa: BLE001
            log.debug("アニメ開けず %s: %s", p, exc)
            return None
        n_frames = getattr(im, "n_frames", 1)
        if n_frames <= 1:
            im.close()
            return None

        w0, h0 = im.size
        scale = 1.0
        if max_w > 0 and max_h > 0:
            scale = min(1.0, max_w / w0, max_h / h0)
        scale = min(scale, MAX_GIF_SIDE / max(w0, h0, 1))
        tw, th = max(1, int(w0 * scale)), max(1, int(h0 * scale))

        frames: list[QImage] = []
        durations: list[int] = []
        loop = True
        try:
            for i, frame in enumerate(ImageSequence.Iterator(im)):
                if i >= MAX_GIF_FRAMES:
                    break
                rgb = frame.convert("RGB")
                if scale < 1.0:
                    rgb = rgb.resize((tw, th))
                qimg = _pil_to_qimage(rgb)
                frames.append(qimg)
                durations.append(int(frame.info.get("duration", 100) or 100))
                if frame.info.get("loop") == 1:
                    loop = False
        except Exception as exc:  # noqa: BLE001
            log.debug("アニメフレーム読込中断 %s: %s", p, exc)
        finally:
            im.close()
        if not frames:
            return None
        return AnimatedImage(frames=frames, durations_ms=durations, loop=loop)

    # ---------------------------------------------------------------
    def _evict(self) -> None:
        while self._used > self.budget and self._cache:
            _, img = self._cache.popitem(last=False)
            self._used -= _qimage_bytes(img)

    def _evict_animated(self, max_items: int = 24) -> None:
        while len(self._animated) > max_items:
            self._animated.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()
        self._animated.clear()
        self._used = 0


def _pil_to_qimage(rgb) -> QImage:
    data = rgb.tobytes("raw", "RGB")
    img = QImage(data, rgb.width, rgb.height, QImage.Format.Format_RGB888)
    return img.copy()  # data の寿命に依存しないよう独立コピー


def _crop_key(crop) -> tuple:
    if crop is None:
        return ()
    return (round(crop.x, 4), round(crop.y, 4), round(crop.w, 4), round(crop.h, 4))
