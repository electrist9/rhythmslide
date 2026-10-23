"""写真スキャンの検証（計画書 F1 / 10 章リスク）。"""
from __future__ import annotations

from PIL import Image

from rhythmslide.core.scanner import IMAGE_EXTENSIONS, scan_photo_root


def _touch_images(root, names):
    for n in names:
        p = root / n
        p.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (8, 8), (10, 10, 10)).save(p)


def test_recursive_and_extension_filter(tmp_path):
    _touch_images(tmp_path, ["a.jpg", "b.png", "c.gif", "sub/d.jpeg"])
    (tmp_path / "notes.txt").write_text("not an image", encoding="utf-8")
    res = scan_photo_root(str(tmp_path), read_exif=False)
    paths = {p.path.replace("\\", "/").rsplit("/", 1)[-1] for p in res.photos}
    assert paths == {"a.jpg", "b.png", "c.gif", "d.jpeg"}
    assert res.count == 4


def test_sorted_by_path_when_no_exif(tmp_path):
    _touch_images(tmp_path, ["z.jpg", "a.jpg", "m.jpg"])
    res = scan_photo_root(str(tmp_path), read_exif=False)
    names = [p.path.rsplit("\\", 1)[-1] for p in res.photos]
    assert names == ["a.jpg", "m.jpg", "z.jpg"]


def test_max_photos_limit(tmp_path):
    _touch_images(tmp_path, [f"{i:03d}.jpg" for i in range(10)])
    res = scan_photo_root(str(tmp_path), read_exif=False, max_photos=5)
    assert res.count == 5


def test_empty_dir(tmp_path):
    res = scan_photo_root(str(tmp_path), read_exif=False)
    assert res.count == 0


def test_extensions_constant_has_common_types():
    assert {".jpg", ".jpeg", ".png", ".gif", ".webp"} <= IMAGE_EXTENSIONS
