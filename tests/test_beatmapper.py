"""自動レイアウト（BeatMapper）の検証（計画書 5.2 / F4）。"""
from __future__ import annotations

import random

from rhythmslide.core.beatmapper import build_timeline
from rhythmslide.core.models import AudioInfo, PhotoItem, Settings, TRANSITION_TYPES


def photos(n: int) -> list[PhotoItem]:
    return [PhotoItem(path=f"/p/{i:03d}.jpg", folder=".") for i in range(n)]


def audio(bpm=120.0, dur=12.0, conf=0.9, method="beat", beats=None,
          energy=None) -> AudioInfo:
    if beats is None:
        beats = [i * (60.0 / bpm) for i in range(int(dur * bpm / 60))]
    et, ev = ([], [])
    if energy is not None:
        et = [i * 0.1 for i in range(len(energy))]
        ev = list(energy)
    return AudioInfo(path="/a.mp3", duration=dur, bpm=bpm, confidence=conf,
                     beats=beats, method=method, energy_times=et, energy_values=ev)


def test_empty_photos():
    plan = build_timeline([], audio(), Settings())
    assert plan.slides == []


def test_no_audio_uses_fixed_and_places_all():
    plan = build_timeline(photos(10), None, Settings(fixed_interval_s=3.5))
    assert plan.method.startswith("fixed")
    assert len(plan.slides) == 10
    starts = [s.start for s in plan.slides]
    assert all(b > a for a, b in zip(starts, starts[1:]))


def test_beat_one_to_one():
    # 12 秒 @120BPM, unit=4 → 6 スロット, 写真 6 枚で 1:1
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(photos(6), a, Settings(unit_beats=4))
    assert len(plan.slides) == 6
    assert not plan.dropped


def test_more_photos_than_slots_thins():
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(photos(20), a, Settings(unit_beats=4))
    assert len(plan.slides) <= 6
    assert len(plan.dropped) >= 14
    assert "thin" in plan.method


def test_fewer_photos_than_slots_cycles():
    # 写真 3 枚 < スロット 6 → 写真を循環させて毎スロット切り替え
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(photos(3), a, Settings(unit_beats=4))
    assert len(plan.slides) == 6
    assert "cycle" in plan.method
    idx = [s.photo_index for s in plan.slides]
    assert set(idx) == {0, 1, 2}  # 全写真が循環して使われる
    assert all(idx.count(i) == 2 for i in (0, 1, 2))


def test_cycle_forward_order_sequence():
    # 順送り（既定）は 0,1,2,0,1,2,...
    from rhythmslide.core.beatmapper import _cycle_index
    assert [_cycle_index(k, 3, "forward") for k in range(6)] == [0, 1, 2, 0, 1, 2]


def test_cycle_pingpong_sequence():
    # 往復は三角波: 0,1,2,1,0,1,2,1,...（周期 2*(n-1)=4）
    from rhythmslide.core.beatmapper import _cycle_index
    assert [_cycle_index(k, 3, "pingpong") for k in range(8)] == [0, 1, 2, 1, 0, 1, 2, 1]
    # n=2 は 0,1,0,1（往復も順送りと同じ）
    assert [_cycle_index(k, 2, "pingpong") for k in range(4)] == [0, 1, 0, 1]
    # n=1 は常に 0
    assert [_cycle_index(k, 1, "pingpong") for k in range(4)] == [0, 0, 0, 0]


def test_pingpong_photo_order_in_timeline():
    # 写真 3 枚 < スロット 6、photo_order="pingpong" → 循環順が往復になる
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(photos(3), a, Settings(unit_beats=4, photo_order="pingpong"))
    assert "cycle" in plan.method and "pingpong" in plan.method
    idx = [s.photo_index for s in plan.slides]
    assert idx == [0, 1, 2, 1, 0, 1]


def test_default_unit_is_half_beat_eight_changes_per_bar():
    # 既定の単位は半拍(0.5)。120BPM(4/4)なら 1 小節=2 秒に 8 回切り替わる
    assert Settings().unit_beats == 0.5
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(photos(3), a, Settings())  # 既定 unit=0.5
    assert "cycle" in plan.method
    starts = [s.start for s in plan.slides]
    # 各スロットは約半拍(0.25 秒)間隔 → 小節(2 秒)あたり 8 回
    diffs = [round(b - a0, 3) for a0, b in zip(starts, starts[1:])]
    assert all(abs(d - 0.25) < 1e-6 for d in diffs)
    assert len(plan.slides) == 47


def test_low_confidence_falls_back_to_fixed():
    a = audio(bpm=120.0, dur=12.0, conf=0.1)
    plan = build_timeline(photos(8), a, Settings(min_confidence=0.5, fixed_interval_s=1.0))
    assert plan.method.startswith("fixed")
    assert len(plan.slides) == 8


def test_locked_override_honored():
    ps = photos(4)
    ps[0].override_start = 5.0
    ps[0].override_duration = 1.5
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(ps, a, Settings(unit_beats=4))
    s0 = next(s for s in plan.slides if s.photo_index == 0)
    assert abs(s0.start - 5.0) < 1e-6
    assert abs((s0.end - s0.start) - 1.5) < 1e-6
    assert s0.locked


def test_transition_and_ken_burns_defaults():
    ps = photos(2)
    ps[1].animated = True
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(ps, a, Settings(unit_beats=4, transition="crossfade", ken_burns=True))
    s0 = next(s for s in plan.slides if s.photo_index == 0)
    s1 = next(s for s in plan.slides if s.photo_index == 1)
    assert s0.transition == "crossfade"
    assert s0.ken_burns is True
    assert s1.ken_burns is False  # アニメは Ken Burns 無効


def test_default_settings_plain_slideshow():
    # 既定設定ではエフェクトなし・遷移なしの素のスライドショーになる
    assert Settings().transition == "none"
    assert Settings().ken_burns is False
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(photos(3), a, Settings(unit_beats=4))
    assert plan.slides
    for s in plan.slides:
        assert s.transition == "none"
        assert s.ken_burns is False


def test_random_transition_off_uses_default():
    # random_transition が OFF なら既定トランジット（既定は "none"）が全スライドに適用
    a = audio(bpm=120.0, dur=12.0)
    plan = build_timeline(photos(6), a, Settings(unit_beats=4))
    assert all(s.transition == "none" for s in plan.slides)


def test_random_transition_per_switch():
    # ON にすると各スライドの遷移が "none" 以外の効果から選ばれる
    pool = {t for t in TRANSITION_TYPES if t != "none"}
    a = audio(bpm=120.0, dur=12.0)
    random.seed(20240521)
    plan = build_timeline(photos(6), a, Settings(unit_beats=4, random_transition=True))
    kinds = [s.transition for s in plan.slides]
    assert kinds  # 空でない
    assert all(k in pool for k in kinds)
    # 6 回抽選で全て同一にはなりにくい（少なくとも 2 種以上出ることを確認）
    assert len(set(kinds)) >= 2


def test_random_transition_respects_photo_override():
    # 写真個別のトランジット指定はランダム化より優先される
    ps = photos(6)
    ps[0].transition = "zoom"
    a = audio(bpm=120.0, dur=12.0)
    random.seed(11)
    plan = build_timeline(ps, a, Settings(unit_beats=4, random_transition=True))
    s0 = next(s for s in plan.slides if s.photo_index == 0)
    assert s0.transition == "zoom"
