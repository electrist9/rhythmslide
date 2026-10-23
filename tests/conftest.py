"""pytest 共通フィクスチャ。"""
from __future__ import annotations

import math
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
import soundfile as sf


@pytest.fixture()
def click_wav(tmp_path):
    """120 BPM・6 秒のクリック音源（メトロノーム的な強いオンセット）。"""
    sr = 22050
    dur = 6.0
    bpm = 120.0
    period = 60.0 / bpm
    n = int(sr * dur)
    y = np.zeros(n, dtype=np.float32)
    for b in range(int(dur / period) + 1):
        start = int(b * period * sr)
        L = int(0.02 * sr)
        if start + L <= n:
            t = np.arange(L) / sr
            y[start:start + L] += 0.6 * np.sin(2 * math.pi * 1000 * t) * np.exp(-t * 60)
    path = tmp_path / "click.wav"
    sf.write(str(path), y, sr)
    return str(path), bpm, dur
