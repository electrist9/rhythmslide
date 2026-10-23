"""データ保存先がワークスペース直下に限定されることの検証。"""
from __future__ import annotations

import os
from pathlib import Path

from rhythmslide.core import paths


def test_default_data_dir_under_workspace(monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    root = paths.workspace_root()
    d = paths.data_dir()
    assert d == root / paths.DATA_SUBDIR
    assert Path(root) in d.parents or d == root


def test_candidates_stay_out_of_profile(monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    local = os.environ.get("LOCALAPPDATA")
    appdata = os.environ.get("APPDATA")
    for c in paths._candidate_dirs():
        for base in (local, appdata):
            if base:
                bp = Path(base)
                assert c != bp and bp not in c.parents


def test_legacy_dir_migrated_to_new_location(tmp_path, monkeypatch):
    """旧 .rhythmslide_data があれば新 rhythmslide/data へ自動で引っ越す。"""
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setattr(paths, "workspace_root", lambda: tmp_path)

    legacy = tmp_path / paths.LEGACY_DATA_SUBDIR
    (legacy / "thumbcache").mkdir(parents=True)
    (legacy / "config.json").write_text("{}", encoding="utf-8")
    (legacy / "thumbcache" / "thumbnails.db").write_text("db", encoding="utf-8")

    target = paths.ensure_data_dir()
    assert target == tmp_path / paths.DATA_SUBDIR
    assert (target / "config.json").read_text(encoding="utf-8") == "{}"
    assert (target / "thumbcache" / "thumbnails.db").read_text(encoding="utf-8") == "db"
    assert not legacy.exists()  # 中身が空になったら旧場所を削除


def test_legacy_migration_keeps_existing_new_files(tmp_path, monkeypatch):
    """新場所に既に同名がある項目は上書きせず、旧場所を残す。"""
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setattr(paths, "workspace_root", lambda: tmp_path)

    legacy = tmp_path / paths.LEGACY_DATA_SUBDIR
    legacy.mkdir(parents=True)
    (legacy / "config.json").write_text("old", encoding="utf-8")

    target = tmp_path / paths.DATA_SUBDIR
    target.mkdir(parents=True)
    (target / "config.json").write_text("new", encoding="utf-8")

    paths.ensure_data_dir()
    assert (target / "config.json").read_text(encoding="utf-8") == "new"
    assert (legacy / "config.json").exists()  # 上書きしないので旧場所に残る
