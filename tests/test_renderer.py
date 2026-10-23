"""SlideRenderer の画像フィット（contain）回帰テスト。

縦長画像は縦方向いっぱい・横方向は背景の余白、
横長画像は横方向いっぱい・縦方向は背景の余白になることを確認する。
"""
from __future__ import annotations

from PIL import Image

from PySide6.QtGui import QImage, QPainter

from rhythmslide.core.imagecache import ImageCache
from rhythmslide.core.models import Slide
from rhythmslide.ui.renderer import SlideRenderer


def _solid(tmp_path, name, w, h, rgb):
    p = tmp_path / name
    Image.new("RGB", (w, h), rgb).save(p, "JPEG")
    return str(p)


def _paint(path, vw, vh):
    cache = ImageCache()
    renderer = SlideRenderer(cache, "#000000")
    slides = [Slide(0, path, 0.0, 5.0, transition="none", ken_burns=False)]
    img = QImage(vw, vh, QImage.Format.Format_RGB32)
    img.fill(0)
    p = QPainter(img)
    renderer.paint(p, vw, vh, 1.0, slides)
    p.end()
    return img


def _rgb(img, x, y):
    c = img.pixelColor(x, y)
    return c.red(), c.green(), c.blue()


def _is_bg(c):
    return c[0] < 40 and c[1] < 40 and c[2] < 40


def _is_img(c):
    return c[0] > 150 and c[1] < 90 and c[2] < 90  # 赤系


def test_portrait_fills_height_with_side_bars(tmp_path):
    path = _solid(tmp_path, "portrait.jpg", 1000, 2000, (200, 30, 30))
    img = _paint(path, 800, 600)
    # 縦方向いっぱい: 上端中央は画像色
    assert _is_img(_rgb(img, 400, 2))
    assert _is_img(_rgb(img, 400, 597))
    # 横方向は余白: 左右端は背景
    assert _is_bg(_rgb(img, 2, 300))
    assert _is_bg(_rgb(img, 797, 300))


def test_landscape_fills_width_with_top_bottom_bars(tmp_path):
    path = _solid(tmp_path, "landscape.jpg", 2000, 1000, (200, 30, 30))
    img = _paint(path, 800, 600)
    # 横方向いっぱい: 左右端中央は画像色
    assert _is_img(_rgb(img, 2, 300))
    assert _is_img(_rgb(img, 797, 300))
    # 縦方向は余白: 上下端は背景
    assert _is_bg(_rgb(img, 400, 2))
    assert _is_bg(_rgb(img, 400, 597))


def test_dominant_background_fills_bars_with_image_color(tmp_path):
    """余白モードを dominant にすると、余白が画像の支配色（赤系）で塗られる。"""
    path = _solid(tmp_path, "portrait.jpg", 1000, 2000, (200, 30, 30))
    cache = ImageCache()
    renderer = SlideRenderer(cache, "#000000")
    renderer.set_background_mode("dominant")
    slides = [Slide(0, path, 0.0, 5.0, transition="none", ken_burns=False)]
    img = QImage(800, 600, QImage.Format.Format_RGB32)
    img.fill(0)
    p = QPainter(img)
    renderer.paint(p, 800, 600, 1.0, slides)
    p.end()
    # 左右の余白が黒ではなく赤系（支配色）になっている
    assert _is_img(_rgb(img, 2, 300))
    assert _is_img(_rgb(img, 797, 300))
    # 画像本体も赤系
    assert _is_img(_rgb(img, 400, 300))


def test_dominant_color_is_most_frequent(tmp_path):
    """支配色は最頻色。赤背景に青い一部がある画像なら赤が支配色。"""
    path = tmp_path / "mixed.jpg"
    base = Image.new("RGB", (200, 200), (200, 30, 30))
    for y in range(20):
        for x in range(200):
            base.putpixel((x, y), (30, 30, 200))  # 上端 10% だけ青
    base.save(path, "JPEG")
    cache = ImageCache()
    renderer = SlideRenderer(cache)
    qimg = cache.image(str(path), 200, 200, None)
    color = renderer._dominant_color(qimg)
    assert color is not None
    assert color.red() > 150 and color.blue() < 90
