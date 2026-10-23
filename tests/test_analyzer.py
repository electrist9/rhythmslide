"""リズム解析の検証（計画書 5.1 / N1）。"""
from __future__ import annotations

from rhythmslide.core.analyzer import AudioAnalyzer, fixed_grid, uniform_grid


def test_click_track_bpm(click_wav):
    path, bpm, dur = click_wav
    ar = AudioAnalyzer().analyze_file(path)
    assert abs(ar.bpm - bpm) <= 3.0
    assert ar.confidence >= 0.5
    assert ar.duration > dur - 0.5
    # ビート間隔は BPM と整合
    assert abs(ar.beat_interval - 60.0 / bpm) < 0.05


def test_beats_monotonic_and_in_range(click_wav):
    path, bpm, dur = click_wav
    ar = AudioAnalyzer().analyze_file(path)
    assert len(ar.beats) >= 4
    for a, b in zip(ar.beats, ar.beats[1:]):
        assert b > a
    assert ar.beats[0] >= 0.0
    assert ar.beats[-1] <= dur + 0.5


def test_energy_profile_normalized(click_wav):
    path, _, _ = click_wav
    ar = AudioAnalyzer().analyze_file(path)
    assert len(ar.energy_times) == len(ar.energy_values)
    assert max(ar.energy_values) <= 1.0001
    assert min(ar.energy_values) >= -0.0001


def test_uniform_grid_spacing():
    beats = uniform_grid(120.0, 6.0, 0.0)
    assert len(beats) >= 10
    diffs = [b - a for a, b in zip(beats, beats[1:])]
    assert all(abs(d - 0.5) < 1e-6 for d in diffs)


def test_fixed_grid_spacing():
    g = fixed_grid(10.0, 3.5)
    assert g[0] == 0.0
    assert all(b > a for a, b in zip(g, g[1:]))
    assert g[-1] < 10.0
