"""プロジェクト保存/読み込みの検証（計画書 7 章 / F11）。"""
from __future__ import annotations

from rhythmslide.core.models import AudioInfo, PhotoItem, Project, Settings
from rhythmslide.core.projectstore import (
    PROJECT_EXTENSION, load_project, save_project, try_load_project,
)


def make_project() -> Project:
    p = Project(photo_root="/photos", settings=Settings(unit_beats=8, transition="zoom"))
    p.photos = [
        PhotoItem(path="/photos/a.jpg", taken_at="2024-01-01T10:00:00", lock=True),
        PhotoItem(path="/photos/b.jpg", taken_at="2024-01-02T11:00:00"),
    ]
    p.audio = AudioInfo(path="/a.mp3", duration=12.0, bpm=120.0, confidence=0.8,
                        beats=[0.0, 0.5, 1.0], method="beat")
    return p


def test_roundtrip(tmp_path):
    proj = make_project()
    out = tmp_path / "proj"  # 拡張子自動付与
    save_project(proj, str(out))
    loaded = load_project(str(out.with_suffix(PROJECT_EXTENSION)))
    assert loaded.photo_root == proj.photo_root
    assert loaded.settings.unit_beats == 8
    assert loaded.settings.transition == "zoom"
    assert len(loaded.photos) == 2
    assert loaded.photos[0].lock is True
    assert loaded.audio.bpm == 120.0
    assert loaded.audio.beats == [0.0, 0.5, 1.0]


def test_file_is_plain_json(tmp_path):
    proj = make_project()
    out = tmp_path / ("proj" + PROJECT_EXTENSION)
    save_project(proj, str(out))
    raw = out.read_bytes()
    assert not raw.startswith(b"RSLD1")  # 圧縮（旧形式）ではない
    import json
    data = json.loads(raw.decode("utf-8"))  # 素の JSON としてそのまま読める
    assert data["photo_root"] == proj.photo_root


def test_try_load_legacy_compressed(tmp_path):
    # 旧形式（マジック + zlib 圧縮）のファイルも読み込めること（下位互換）。
    import json
    import zlib
    proj = make_project()
    p = tmp_path / ("legacy" + PROJECT_EXTENSION)
    blob = b"RSLD1" + zlib.compress(
        json.dumps(proj.to_dict(), ensure_ascii=False).encode("utf-8")
    )
    p.write_bytes(blob)
    loaded, err = try_load_project(str(p))
    assert err is None
    assert loaded.photo_root == proj.photo_root
    assert loaded.settings.unit_beats == 8


def test_try_load_missing(tmp_path):
    proj, err = try_load_project(str(tmp_path / "nope.rslide"))
    assert proj is None
    assert err


def test_try_load_garbage(tmp_path):
    bad = tmp_path / "bad.rslide"
    bad.write_bytes(b"not a project at all")
    proj, err = try_load_project(str(bad))
    assert proj is None
    assert err


def test_try_load_plain_json(tmp_path):
    proj = make_project()
    p = tmp_path / "plain.rslide"
    import json
    p.write_text(json.dumps(proj.to_dict()), encoding="utf-8")
    loaded, err = try_load_project(str(p))
    assert err is None
    assert loaded.photo_root == proj.photo_root


def test_random_order_default_off():
    assert Settings().random_order is False


def test_random_order_roundtrip(tmp_path):
    proj = make_project()
    proj.settings.random_order = True
    out = tmp_path / ("proj" + PROJECT_EXTENSION)
    save_project(proj, str(out))
    loaded = load_project(str(out))
    assert loaded.settings.random_order is True


def test_background_mode_default_solid():
    assert Settings().presentation.background_mode == "solid"


def test_background_mode_roundtrip(tmp_path):
    proj = make_project()
    proj.settings.presentation.background_mode = "dominant"
    out = tmp_path / ("proj" + PROJECT_EXTENSION)
    save_project(proj, str(out))
    loaded = load_project(str(out))
    assert loaded.settings.presentation.background_mode == "dominant"
