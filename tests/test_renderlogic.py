"""描画判定ロジックの検証（計画書 5.3 / F8）。"""
from __future__ import annotations

from rhythmslide.core.models import Slide
from rhythmslide.core.renderlogic import render_state, slide_at, total_duration


def slides():
    return [
        Slide(0, "/a.jpg", 0.0, 3.0, transition="crossfade", transition_ms=400, ken_burns=True),
        Slide(1, "/b.jpg", 3.0, 6.0, transition="wipe_left", transition_ms=500),
        Slide(2, "/c.jpg", 6.0, 9.0, transition="none", transition_ms=400),
    ]


def test_slide_at_boundaries():
    s = slides()
    assert slide_at(s, 0.0).photo_index == 0
    assert slide_at(s, 2.99).photo_index == 0
    assert slide_at(s, 3.0).photo_index == 1
    assert slide_at(s, 8.99).photo_index == 2


def test_total_duration():
    assert total_duration(slides()) == 9.0
    assert total_duration([]) == 0.0


def test_transition_active_near_end():
    s = slides()
    # 1 枚目(a)の終わりは 3.0、遷移 0.4s → 2.6s 以降で遷移開始
    assert render_state(s, 2.5).transition == "none"
    rs = render_state(s, 2.8)
    assert rs.transition == "crossfade"
    assert rs.current.photo_index == 0
    assert rs.incoming.photo_index == 1
    assert 0.0 < rs.progress < 1.0


def test_transition_progress_near_one():
    s = slides()
    # 遷移完了直前（3.0 ちょうどでは次スライドへ切り替わる）
    rs = render_state(s, 2.999)
    assert rs.transition == "crossfade"
    assert rs.progress > 0.99


def test_no_transition_when_type_none():
    s = slides()
    # 末尾スライド(c)の transition="none" → 境界 9.0 付近で遷移なし
    assert render_state(s, 8.9).transition == "none"
    assert render_state(s, 8.9).current.photo_index == 2


def test_ken_progress_spans_slide():
    s = slides()
    assert render_state(s, 0.0).ken_progress == 0.0
    assert abs(render_state(s, 1.5).ken_progress - 0.5) < 1e-6
    assert render_state(s, 2.99).ken_progress <= 1.0


def test_ken_zero_when_disabled():
    s = slides()
    # 2 枚目(b)は ken_burns=False
    assert render_state(s, 4.5).ken_progress == 0.0


def test_transition_seconds_capped_at_40_percent():
    # 短いスライド（0.25s）で 100ms 指定 → 上限は表示長の 40%(=0.10s)で 100ms がそのまま効く
    short = Slide(0, "/a.jpg", 0.0, 0.25, transition="crossfade", transition_ms=100)
    assert abs(short.transition_seconds - 0.10) < 1e-9
    # 遷移は末尾 0.10s（0.15s 以降）で開始する
    pair = [short, Slide(1, "/b.jpg", 0.25, 0.5, transition="none", transition_ms=100)]
    assert render_state(pair, 0.14).transition == "none"
    rs = render_state(pair, 0.20)
    assert rs.transition == "crossfade"
    assert 0.0 < rs.progress < 1.0


def test_transition_seconds_long_slide_uses_ms():
    # 長いスライドでは上限に当たらず指定 ms がそのまま採用される
    long = Slide(0, "/a.jpg", 0.0, 5.0, transition="crossfade", transition_ms=100)
    assert abs(long.transition_seconds - 0.10) < 1e-9
