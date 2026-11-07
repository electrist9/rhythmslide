"""差し込み可能なビート検出（beat provider）の検証。

- 登録表（get / list / register）と既定フォールバック
- 内蔵プロバイダ（acf / funscript）の正規化と解析
- analyze_audio の未知 id・不正 params への耐性
- load_beat_provider_plugins の外部プラグイン読み込み
"""
from __future__ import annotations

import numpy as np

from rhythmslide.core import beatproviders as bp
from rhythmslide.core.beatproviders import (
    DEFAULT_PROVIDER_ID,
    AudioFeatures,
    BeatDetection,
    analyze_audio,
    get_beat_provider,
    list_beat_providers,
    load_beat_provider_plugins,
    register_beat_provider,
)

# pos が 0/100 で交互に揺れる 500ms 間隔の譜面（"up" で 0.5,1.5,2.5 が切替点）
_FS_ACTIONS = [
    {"at": 0, "pos": 0}, {"at": 500, "pos": 100}, {"at": 1000, "pos": 0},
    {"at": 1500, "pos": 100}, {"at": 2000, "pos": 0}, {"at": 2500, "pos": 100},
]


def _write_funscript(tmp_path, name: str = "a.funscript") -> str:
    import json

    path = tmp_path / name
    path.write_text(
        json.dumps({"actions": _FS_ACTIONS, "inverted": False, "range": 100, "version": "1.0"}),
        encoding="utf-8",
    )
    return str(path)


# --- 登録表 ---------------------------------------------------------------
def test_builtin_providers_registered():
    ids = {p.id for p in list_beat_providers()}
    assert ids == {"acf", "funscript"}
    assert DEFAULT_PROVIDER_ID == "acf"
    assert get_beat_provider("acf") is not None
    assert get_beat_provider("funscript").requires_audio is False
    assert get_beat_provider("unknown") is None


def test_register_class_and_reject_empty_id():
    class _Tmp:
        id = "tmp_test_provider"
        label = "tmp"
        version = "1"
        requires_audio = False

        def default_params(self):
            return {}

        def sanitize_params(self, raw):
            return {}

        def analyze(self, feats, params):
            return BeatDetection()

    try:
        prov = register_beat_provider(_Tmp)
        assert not isinstance(prov, type)
        assert get_beat_provider("tmp_test_provider") is prov
        try:
            register_beat_provider(type("Empty", (), {"id": ""}))
        except ValueError:
            pass
        else:
            raise AssertionError("空 id は ValueError にすべき")
    finally:
        bp._REGISTRY.pop("tmp_test_provider", None)


# --- ACF（標準） -----------------------------------------------------------
def test_acf_provider_on_click(click_wav):
    path, bpm, dur = click_wav
    feats = bp.extract_features(path)
    assert feats.duration > dur - 0.5
    assert feats.onset_env.size > 0
    det = get_beat_provider("acf").analyze(feats, {})
    assert abs(det.bpm - bpm) <= 3.0
    assert det.confidence >= 0.5
    assert len(det.beats) >= 4
    assert det.method == "beat"
    assert det.tempo_candidates


def test_acf_provider_empty_features_is_fixed():
    det = get_beat_provider("acf").analyze(AudioFeatures(), {})
    assert det.beats == []
    assert det.method == "fixed"
    assert det.confidence == 0.0


# --- FunScript プロバイダ ---------------------------------------------------
def test_funscript_sanitize_params():
    p = get_beat_provider("funscript")
    got = p.sanitize_params({"mode": "bogus", "threshold": -5, "step": 0, "search_dirs": "x"})
    assert got["mode"] == "up"
    assert got["threshold"] == 0.0
    assert got["step"] == 100.0
    assert got["search_dirs"] == []
    got = p.sanitize_params({"mode": "peak", "threshold": 25.0, "step": 200.0,
                             "search_dirs": ["d1", "", "d2"]})
    assert got["mode"] == "peak"
    assert got["threshold"] == 25.0
    assert got["step"] == 200.0
    assert got["search_dirs"] == ["d1", "d2"]


def test_funscript_explicit_path(tmp_path):
    fs = _write_funscript(tmp_path)
    feats = AudioFeatures(audio_path=str(tmp_path / "a.mp3"), duration=3.0)
    det = get_beat_provider("funscript").analyze(feats, {"path": fs})
    assert det.beats == [0.5, 1.5, 2.5]
    assert det.confidence == 1.0
    assert det.method == "beat"
    assert abs(det.bpm - 60.0) < 1e-6  # 中央値 1.0s → 60 BPM


def test_funscript_autodetect_same_basename(tmp_path):
    _write_funscript(tmp_path)  # a.funscript
    feats = AudioFeatures(audio_path=str(tmp_path / "a.mp3"), duration=3.0)
    det = get_beat_provider("funscript").analyze(feats, {})
    assert det.beats == [0.5, 1.5, 2.5]


def test_funscript_search_dirs(tmp_path):
    sub = tmp_path / "fs"
    sub.mkdir()
    _write_funscript(sub)
    feats = AudioFeatures(audio_path=str(tmp_path / "elsewhere" / "a.mp3"), duration=3.0)
    det = get_beat_provider("funscript").analyze(feats, {"search_dirs": [str(sub)]})
    assert det.beats == [0.5, 1.5, 2.5]


def test_funscript_missing_or_broken(tmp_path):
    feats = AudioFeatures(audio_path=str(tmp_path / "a.mp3"), duration=3.0)
    det = get_beat_provider("funscript").analyze(feats, {})
    assert det.beats == []
    assert det.method == "fixed"
    assert det.confidence == 0.0
    bad = tmp_path / "a.funscript"
    bad.write_text("{ not json", encoding="utf-8")
    det = get_beat_provider("funscript").analyze(feats, {})
    assert det.beats == []
    assert det.method == "fixed"


def test_funscript_filters_by_duration(tmp_path):
    fs = _write_funscript(tmp_path)
    feats = AudioFeatures(audio_path=str(tmp_path / "a.mp3"), duration=1.2)
    det = get_beat_provider("funscript").analyze(feats, {"path": fs})
    assert det.beats == [0.5]
    assert det.bpm == 0.0  # 2 点未満なので BPM は出さない


# --- 手動 BPM / 固定間隔はプロバイダ化しない --------------------------------
def test_manual_bpm_and_fixed_not_registered():
    # メニューの選択肢は標準（acf）と FunScript の 2 つだけ
    assert get_beat_provider("manual_bpm") is None
    assert get_beat_provider("fixed") is None


# --- analyze_audio（オーケストレーター） -------------------------------------
def test_analyze_audio_acf(click_wav):
    path, bpm, _ = click_wav
    res = analyze_audio(path)
    assert abs(res.bpm - bpm) <= 3.0
    assert res.method == "beat"
    assert len(res.beats) >= 4
    assert res.energy_times and len(res.energy_times) == len(res.energy_values)


def test_analyze_audio_unknown_id_falls_back(click_wav):
    path, bpm, _ = click_wav
    res = analyze_audio(path, "no_such_provider", {"bpm": 999})
    assert abs(res.bpm - bpm) <= 3.0  # acf として解析されている
    assert res.method == "beat"


def test_analyze_audio_bad_params(click_wav):
    path, bpm, _ = click_wav
    res = analyze_audio(path, "acf", "not-a-dict")
    assert abs(res.bpm - bpm) <= 3.0


def test_analyze_audio_funscript_skips_decode(tmp_path):
    # 音声ファイルは実在しない（probe 失敗でも duration 0 で進む）
    _write_funscript(tmp_path)
    res = analyze_audio(tmp_path / "a.mp3", "funscript")
    assert res.beats == [0.5, 1.5, 2.5]
    assert res.confidence == 1.0
    assert res.method == "beat"
    assert res.duration == 0.0
    # ファイルが見つかれば ACF のビート作成はしない（実体は funscript のまま）
    assert res.provider == "funscript"


def test_analyze_audio_funscript_missing_falls_back_to_acf(click_wav):
    # FunScript ファイルが見つからない場合は標準 ACF でビートを作る
    path, bpm, _ = click_wav
    res = analyze_audio(path, "funscript", {})
    assert res.provider == "acf"
    assert res.method == "beat"
    assert len(res.beats) >= 4
    assert abs(res.bpm - bpm) <= 3.0


# --- プラグイン読み込み ------------------------------------------------------
_PLUGIN_SRC = """
from rhythmslide.core.beatproviders import BeatDetection, register_beat_provider


@register_beat_provider
class _Plug:
    id = "plug_test"
    label = "プラグイン試験"
    version = "1"
    requires_audio = False

    def default_params(self):
        return {}

    def sanitize_params(self, raw):
        return {}

    def analyze(self, feats, params):
        return BeatDetection(bpm=42.0, beats=[0.0, 1.0], confidence=1.0, method="beat")
"""


def test_load_plugins(tmp_path):
    (tmp_path / "plug.py").write_text(_PLUGIN_SRC, encoding="utf-8")
    (tmp_path / "_private.py").write_text("raise RuntimeError('読んではいけない')", encoding="utf-8")
    (tmp_path / "broken.py").write_text("this is not python(", encoding="utf-8")
    try:
        loaded = load_beat_provider_plugins(tmp_path)
        assert loaded == ["plug"]  # _private はスキップ、broken は警告のみ
        prov = get_beat_provider("plug_test")
        assert prov is not None
        res = analyze_audio("dummy.mp3", "plug_test")
        assert res.bpm == 42.0
        assert res.beats == [0.0, 1.0]
    finally:
        bp._REGISTRY.pop("plug_test", None)


def test_load_plugins_missing_dir_is_empty(tmp_path):
    assert load_beat_provider_plugins(tmp_path / "nope") == []
