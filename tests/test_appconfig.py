"""アプリ設定（前回のフォルダ記憶）の検証。"""
from __future__ import annotations

from rhythmslide.core import appconfig


def test_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    appconfig.save({"photo_dir": str(tmp_path / "pics")})
    assert appconfig.load().get("photo_dir") == str(tmp_path / "pics")


def test_load_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    assert appconfig.load() == {}


def test_load_ignores_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    (tmp_path / "config.json").write_text("{壊れた json", encoding="utf-8")
    assert appconfig.load() == {}


# --- FunScript プリセット（config.json とは別ファイル）---------------------

def test_presets_roundtrip_separate_file(tmp_path, monkeypatch):
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    appconfig.save_funscript_presets([{"name": "A", "mode": "up"}])
    # 別ファイルに保存され、config.json には入らない
    assert (tmp_path / "funscript_presets.json").exists()
    assert "funscript_presets" not in appconfig.load()
    assert appconfig.load_funscript_presets() == [{"name": "A", "mode": "up"}]


def test_presets_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    assert appconfig.load_funscript_presets() == []


def test_presets_ignores_corrupt(tmp_path, monkeypatch):
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    (tmp_path / "funscript_presets.json").write_text("壊れた json", encoding="utf-8")
    assert appconfig.load_funscript_presets() == []


def test_presets_migrated_from_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    # 旧形式: config.json 内に funscript_presets を置く
    appconfig.save({"photo_dir": "x", "funscript_presets": [{"name": "旧", "mode": "peak"}]})
    got = appconfig.load_funscript_presets()
    assert got == [{"name": "旧", "mode": "peak"}]
    # 移行後は別ファイルにあり、config.json からはキーが消えている
    assert (tmp_path / "funscript_presets.json").exists()
    cfg = appconfig.load()
    assert "funscript_presets" not in cfg
    assert cfg.get("photo_dir") == "x"
