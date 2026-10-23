"""ImageCache のデコード（縮小・クロップ）回帰テスト。

大きな写真で setScaledSize 経路（PySide6 はキャメルケース）を通す。
"""
from __future__ import annotations

from PIL import Image

from rhythmslide.core.imagecache import ImageCache
from rhythmslide.core.models import CropRect


def _big(tmp_path, name="big.jpg", w=1200, h=900):
    p = tmp_path / name
    Image.new("RGB", (w, h), (10, 120, 200)).save(p, "JPEG")
    return p


def test_downscale_large_image(tmp_path):
    cache = ImageCache()
    p = _big(tmp_path)
    img = cache.image(p, 400, 300)
    assert img is not None and not img.isNull()
    # 指定内に収まっている（縮小が効いている）
    assert img.width() <= 400 and img.height() <= 300


def test_no_downscale_when_small(tmp_path):
    cache = ImageCache()
    p = _big(tmp_path, w=100, h=80)
    img = cache.image(p, 400, 300)
    assert img is not None and img.width() == 100 and img.height() == 80


def test_crop_decode(tmp_path):
    cache = ImageCache()
    p = _big(tmp_path)
    img = cache.image(p, 400, 300, CropRect(0.25, 0.25, 0.5, 0.5))
    assert img is not None and not img.isNull()
