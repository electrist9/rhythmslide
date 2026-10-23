"""サムネイル SQLite キャッシュの検証（計画書 F1 / N4）。"""
from __future__ import annotations

import io
import threading

from PIL import Image

from rhythmslide.core.thumbnails import ThumbnailCache


def _img(tmp_path, name="x.jpg", size=(400, 300)):
    p = tmp_path / name
    Image.new("RGB", size, (200, 30, 30)).save(p)
    return str(p)


def _db(tmp_path):
    return str(tmp_path / "thumbs.db")


def test_ensure_returns_jpeg_bytes(tmp_path):
    cache = ThumbnailCache(db_path=_db(tmp_path))
    src = _img(tmp_path)
    out = cache.ensure(src)
    assert out is not None
    assert isinstance(out, (bytes, bytearray))
    im = Image.open(io.BytesIO(out))
    assert im.size[0] <= 256
    cache.close()


def test_db_file_created(tmp_path):
    cache = ThumbnailCache(db_path=_db(tmp_path))
    cache.ensure(_img(tmp_path))
    assert (tmp_path / "thumbs.db").exists()
    cache.close()


def test_get_is_cache_hit(tmp_path):
    cache = ThumbnailCache(db_path=_db(tmp_path))
    src = _img(tmp_path)
    cache.ensure(src)
    hit = cache.get(src)
    assert hit is not None
    cache.close()


def test_get_missing_before_ensure(tmp_path):
    cache = ThumbnailCache(db_path=_db(tmp_path))
    assert cache.get(_img(tmp_path)) is None
    cache.close()


def test_second_ensure_is_no_rerender(tmp_path):
    cache = ThumbnailCache(db_path=_db(tmp_path))
    src = _img(tmp_path)
    first = cache.ensure(src)
    second = cache.ensure(src)
    assert first == second
    cache.close()


def test_persists_across_instances(tmp_path):
    """1 つの DB ファイルに集約され、別インスタンス（＝別プロセス相当）でも再利用できる。"""
    src = _img(tmp_path)
    a = ThumbnailCache(db_path=_db(tmp_path))
    blob = a.ensure(src)
    a.close()
    b = ThumbnailCache(db_path=_db(tmp_path))
    assert b.get(src) == blob
    b.close()


def test_broken_image_yields_placeholder(tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not-an-image")
    cache = ThumbnailCache(db_path=_db(tmp_path))
    out = cache.ensure(str(bad))
    assert out is not None
    assert Image.open(io.BytesIO(out)).size[0] <= 256
    cache.close()


def test_concurrent_ensure_is_thread_safe(tmp_path):
    """GUI スレッドとワーカスレッドが同時に触る構成を再現し、ロックで直列化されることを確認。"""
    cache = ThumbnailCache(db_path=_db(tmp_path))
    srcs = [_img(tmp_path, name=f"i{i}.jpg", size=(300 + i * 10, 200)) for i in range(12)]
    results: list = []
    results_lock = threading.Lock()

    def worker(paths):
        for p in paths:
            blob = cache.ensure(p)
            with results_lock:
                results.append(blob)

    threads = [threading.Thread(target=worker, args=(srcs[i::3],)) for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == len(srcs)
    assert all(r is not None for r in results)
    # 全画像が DB に残っている（上書き消失がない）
    for p in srcs:
        assert cache.get(p) is not None
    cache.close()
