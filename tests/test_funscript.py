"""FunScript 由来の画像切替タイミングの検証。

- パーサ: pos 差分が 0 以上の at を切替点として採用する規則。
- BeatMapper: FunScript があれば音声解析ビートより優先してグリッド化する。
"""
from __future__ import annotations

import json

from rhythmslide.core.beatmapper import build_timeline
from rhythmslide.core.funscript import (
    find_funscript, load_funscript, make_preset, sanitize_presets, switch_times,
)
from rhythmslide.core.models import AudioInfo, PhotoItem, Settings

# 仕様例の "beat tester"（120BPM 相当・250ms 間隔の pos 揺れ）
_EXAMPLE = {
    "actions": [
        {"at": 0, "pos": 0}, {"at": 250, "pos": 0}, {"at": 500, "pos": 100},
        {"at": 750, "pos": 0}, {"at": 1000, "pos": 100}, {"at": 1250, "pos": 0},
        {"at": 1500, "pos": 100}, {"at": 1750, "pos": 0}, {"at": 2000, "pos": 100},
        {"at": 2250, "pos": 0}, {"at": 2500, "pos": 100}, {"at": 2750, "pos": 0},
        {"at": 3000, "pos": 100}, {"at": 3250, "pos": 0}, {"at": 3500, "pos": 100},
        {"at": 3750, "pos": 0}, {"at": 4000, "pos": 100}, {"at": 4250, "pos": 0},
        {"at": 4500, "pos": 100}, {"at": 4750, "pos": 0}, {"at": 5000, "pos": 100},
        {"at": 5250, "pos": 0}, {"at": 5500, "pos": 100}, {"at": 5750, "pos": 0},
        {"at": 6000, "pos": 100}, {"at": 6250, "pos": 0}, {"at": 6500, "pos": 100},
        {"at": 6750, "pos": 0}, {"at": 7000, "pos": 100}, {"at": 7250, "pos": 0},
        {"at": 7500, "pos": 100}, {"at": 7750, "pos": 0}, {"at": 8000, "pos": 100},
        {"at": 8250, "pos": 0}, {"at": 8500, "pos": 100}, {"at": 8750, "pos": 0},
        {"at": 9000, "pos": 100}, {"at": 9250, "pos": 0}, {"at": 9500, "pos": 100},
        {"at": 9750, "pos": 0},
    ],
    "inverted": False,
    "range": 100,
    "version": "1.0",
}


def _expected_switches() -> list[float]:
    # pos 差分が正（>0）: at=500,1000,1500,...,9500（偶数 index の +100 差分のみ）
    return [i * 0.5 for i in range(1, 20)]  # 0.5,1.0,...,9.5


def test_switch_times_rule():
    got = switch_times(_EXAMPLE["actions"])
    assert got == _expected_switches()


def test_switch_times_only_positive_diff():
    # pos が下がるとき（差分 <0）や横ばい（差分 =0）は切替点にしない
    acts = [
        {"at": 0, "pos": 100},
        {"at": 1000, "pos": 0},   # 0-100 <0 → 不採用
        {"at": 2000, "pos": 50},  # 50-0 >0 → 採用
        {"at": 3000, "pos": 50},  # 50-50 =0 → 不採用
    ]
    assert switch_times(acts) == [2.0]


# --- mode="up" + 振幅しきい値 ----------------------------------------------
def test_switch_times_up_threshold():
    # 微小な上昇（差分 <= threshold）は無視し、大きな上昇だけ採用
    acts = [
        {"at": 0, "pos": 0},
        {"at": 500, "pos": 10},    # +10 <= 50 → 不採用
        {"at": 1000, "pos": 100},  # +90 > 50 → 採用
        {"at": 1500, "pos": 95},   # -5 → 不採用
        {"at": 2000, "pos": 98},   # +3 <= 50 → 不採用
    ]
    assert switch_times(acts, mode="up", threshold=50.0) == [1.0]


# --- mode="peak"（局所極値）-----------------------------------------------
def test_switch_times_peak_single():
    # 上昇頂点と下降頂点の両方で切替わる。末尾の点は後ろに点が無いため頂点と確定しない
    acts = [
        {"at": 0, "pos": 0},
        {"at": 1000, "pos": 100},  # 上昇の頂点 → 採用
        {"at": 2000, "pos": 0},    # 下降の頂点 → 採用
        {"at": 3000, "pos": 100},  # 末尾 → 頂点と確定できず不採用
    ]
    assert switch_times(acts, mode="peak") == [1.0, 2.0]


def test_switch_times_peak_flat_top_ignored():
    # 平坦な天板（100,100）は頂点として二重計上しない
    acts = [
        {"at": 0, "pos": 0},
        {"at": 1000, "pos": 100},
        {"at": 2000, "pos": 100},
        {"at": 3000, "pos": 0},
    ]
    # 1000: 100>=0 and 100>=100, min(100,0)=0 → 不採用
    # 2000: 100>=100 and 100>=0, min(0,100)=0 → 不採用
    assert switch_times(acts, mode="peak") == []


def test_switch_times_peak_threshold():
    # 頂点の高さ（prominence）が threshold 未満なら無視
    acts = [
        {"at": 0, "pos": 0},
        {"at": 1000, "pos": 30},   # prominence=min(30,30)=30 <= 50 → 不採用
        {"at": 2000, "pos": 0},
        {"at": 3000, "pos": 90},   # prominence=min(90,90)=90 > 50 → 採用
        {"at": 4000, "pos": 0},
    ]
    assert switch_times(acts, mode="peak", threshold=50.0) == [3.0]


def test_switch_times_peak_example_double():
    # 仕様例では up が 19 件（上昇のみ）なのに対し peak は 37 件（頂点+谷）
    up = switch_times(_EXAMPLE["actions"], mode="up")
    peak = switch_times(_EXAMPLE["actions"], mode="peak")
    assert len(up) == 19
    assert len(peak) == 37
    assert peak[:3] == [0.5, 0.75, 1.0]


# --- mode="cumulative"（累積移動距離）-------------------------------------
def test_switch_times_cumulative_step():
    # |Δpos| を積算し step に達するたびに切替わる
    acts = [
        {"at": 0, "pos": 0},
        {"at": 1000, "pos": 40},   # acc=40
        {"at": 2000, "pos": 0},    # acc=80
        {"at": 3000, "pos": 60},   # acc=140 >= 100 → 採用, acc=40
        {"at": 4000, "pos": 0},    # acc=100 >= 100 → 採用, acc=0
    ]
    assert switch_times(acts, mode="cumulative", step=100.0) == [3.0, 4.0]


def test_switch_times_cumulative_threshold_skips_noise():
    # threshold 未満の揺れは積算に含めない
    acts = [
        {"at": 0, "pos": 0},
        {"at": 1000, "pos": 5},    # |5| <= 10 → 無視
        {"at": 2000, "pos": 0},    # |5| <= 10 → 無視
        {"at": 3000, "pos": 100},  # |100| > 10 → acc=100 >= 100 → 採用
    ]
    assert switch_times(acts, mode="cumulative", threshold=10.0, step=100.0) == [3.0]


def test_switch_times_cumulative_step_zero_no_switch():
    acts = [{"at": 0, "pos": 0}, {"at": 1000, "pos": 100}]
    assert switch_times(acts, mode="cumulative", step=0.0) == []


# --- Settings の新フィールド往復（.rslide 後方互換）----------------------
def test_settings_new_fields_roundtrip():
    s = Settings(
        funscript_mode="peak", funscript_threshold=25.0,
        funscript_step=200.0, photo_order="pingpong", render_fps=144,
    )
    got = Settings.from_dict(s.to_dict())
    assert got.funscript_mode == "peak"
    assert got.funscript_threshold == 25.0
    assert got.funscript_step == 200.0
    assert got.photo_order == "pingpong"
    assert got.render_fps == 144


def test_settings_defaults_and_invalid_fallback():
    # 旧プロジェクト（フィールド無し）は既定値にフォールバック
    old = Settings.from_dict({"unit_beats": 4})
    assert old.funscript_mode == "up"
    assert old.funscript_threshold == 0.0
    assert old.funscript_step == 100.0
    assert old.photo_order == "forward"
    assert old.render_fps == 60
    # 不正値は既定へ正規化
    bad = Settings.from_dict({
        "funscript_mode": "hoge", "funscript_threshold": -5,
        "funscript_step": 0, "photo_order": "???", "render_fps": 0,
    })
    assert bad.funscript_mode == "up"
    assert bad.funscript_threshold == 0.0
    assert bad.funscript_step == 100.0
    assert bad.photo_order == "forward"
    assert bad.render_fps == 60
    # 範囲内の任意 FPS はそのまま採用（メニュー外値も許容）
    assert Settings.from_dict({"render_fps": 120}).render_fps == 120


# --- 名前付きプリセット（make_preset / sanitize_presets）------------------
def test_make_preset_shape():
    p = make_preset("王道", "up", 0.0, 100.0, "forward")
    assert p == {
        "name": "王道", "mode": "up", "threshold": 0.0,
        "step": 100.0, "order": "forward",
    }


def test_sanitize_presets_valid_roundtrip():
    raw = [
        make_preset("A", "peak", 15.0, 100.0, "pingpong"),
        make_preset("B", "cumulative", 10.0, 200.0, "forward"),
    ]
    got = sanitize_presets(raw)
    assert [p["name"] for p in got] == ["A", "B"]
    assert got[0]["mode"] == "peak" and got[0]["order"] == "pingpong"
    assert got[1]["step"] == 200.0


def test_sanitize_presets_drops_bad_and_dupes():
    raw = [
        make_preset("A", "up", 0.0, 100.0, "forward"),
        make_preset("A", "peak", 0.0, 100.0, "forward"),  # 同名 → 2つ目は捨てる
        make_preset("  ", "up", 0.0, 100.0, "forward"),   # 空名 → 捨てる
        {"mode": "up"},                                   # name 無し → 捨てる
        "not-a-dict",                                     # dict 以外 → 捨てる
    ]
    got = sanitize_presets(raw)
    assert [p["name"] for p in got] == ["A"]


def test_sanitize_presets_normalizes_invalid_values():
    raw = [{
        "name": "X", "mode": "hoge", "threshold": -5,
        "step": 0, "order": "???",
    }]
    got = sanitize_presets(raw)
    assert got[0]["mode"] == "up"
    assert got[0]["threshold"] == 0.0
    assert got[0]["step"] == 100.0
    assert got[0]["order"] == "forward"


def test_sanitize_presets_coerces_numeric_strings():
    raw = [{"name": "N", "mode": "peak", "threshold": "20", "step": "150", "order": "pingpong"}]
    got = sanitize_presets(raw)
    assert got[0]["threshold"] == 20.0
    assert got[0]["step"] == 150.0


def test_sanitize_presets_non_list_returns_empty():
    assert sanitize_presets(None) == []
    assert sanitize_presets({"name": "A"}) == []
    assert sanitize_presets("hoge") == []


def test_load_funscript_file(tmp_path):
    p = tmp_path / "beat.funscript"
    p.write_text(json.dumps(_EXAMPLE), encoding="utf-8")
    assert load_funscript(p) == _expected_switches()


def test_load_funscript_bad_json(tmp_path):
    p = tmp_path / "bad.funscript"
    p.write_text("{not json", encoding="utf-8")
    try:
        load_funscript(p)
    except ValueError:
        pass
    else:
        raise AssertionError("不正 JSON で ValueError が出るはず")


def test_load_funscript_missing(tmp_path):
    try:
        load_funscript(tmp_path / "nope.funscript")
    except ValueError:
        pass
    else:
        raise AssertionError("不存在で ValueError が出るはず")


# --- BeatMapper 連携 -------------------------------------------------------
def _photos(n: int) -> list[PhotoItem]:
    return [PhotoItem(path=f"/p/{i:03d}.jpg", folder=".") for i in range(n)]


def _audio(dur: float, switches: list[float], conf: float = 0.0,
           method: str = "fixed") -> AudioInfo:
    # 信頼度を低くし method も fixed にしても、FunScript があればそれが優先される
    return AudioInfo(
        path="/a.mp3", duration=dur, bpm=0.0, confidence=conf, beats=[],
        method=method, funscript_switches=list(switches),
    )


def test_funscript_grid_drives_boundaries():
    a = _audio(4.0, [1.0, 2.0, 3.0])
    plan = build_timeline(_photos(2), a, Settings())
    assert plan.method.startswith("funscript")
    starts = [round(s.start, 6) for s in plan.slides]
    # 境界 0,1,2,3,4 → 4 スロット。写真 2 枚 < 4 なので循環
    assert starts == [0.0, 1.0, 2.0, 3.0]
    assert "cycle" in plan.method


def test_funscript_overrides_low_confidence():
    # 音声解析は信頼度不足（fixed）でも、FunScript があれば funscript 経路
    a = _audio(3.0, [1.0, 2.0], conf=0.0, method="fixed")
    plan = build_timeline(_photos(3), a, Settings())
    assert plan.method.startswith("funscript")
    assert len(plan.slides) == 3  # 境界 0,1,2,3 → 3 スロット = 写真 3 枚で 1:1


def test_no_funscript_falls_back_to_fixed():
    a = _audio(3.0, [], conf=0.0, method="fixed")
    plan = build_timeline(_photos(3), a, Settings())
    assert plan.method.startswith("fixed")


# ---------------------------------------------------------------------------
# find_funscript: 音声・動画と同じベースファイル名の .funscript を探す
# ---------------------------------------------------------------------------
def _write_funscript(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"actions": [{"at": 0, "pos": 0}]}), encoding="utf-8")
    return path


def test_find_funscript_adjacent(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    fs = _write_funscript(tmp_path / "song.funscript")
    assert find_funscript(audio, []) == fs


def test_find_funscript_recursive_in_search_dir(tmp_path):
    audio = tmp_path / "media" / "song.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"")
    # 検索フォルダの深い場所に同名 .funscript
    fs = _write_funscript(tmp_path / "libs" / "a" / "b" / "song.funscript")
    found = find_funscript(audio, [tmp_path / "libs"])
    assert found == fs


def test_find_funscript_case_insensitive_stem(tmp_path):
    audio = tmp_path / "MySong.mp3"
    audio.write_bytes(b"")
    fs = _write_funscript(tmp_path / "libs" / "mysong.funscript")
    found = find_funscript(audio, [tmp_path / "libs"])
    assert found == fs


def test_find_funscript_prefers_adjacent(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    near = _write_funscript(tmp_path / "song.funscript")
    _write_funscript(tmp_path / "libs" / "song.funscript")
    assert find_funscript(audio, [tmp_path / "libs"]) == near


def test_find_funscript_not_found(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    _write_funscript(tmp_path / "libs" / "other.funscript")
    assert find_funscript(audio, [tmp_path / "libs"]) is None


def test_find_funscript_skips_missing_dir(tmp_path):
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"")
    # 存在しない検索フォルダは例外なく無視される
    assert find_funscript(audio, [tmp_path / "does_not_exist"]) is None
