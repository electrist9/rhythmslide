"""リズム解析（計画書 5.1 / M2）。

librosa と同じ手順を numpy だけで実装する（Python 3.12+ で numba 依存を避けるため。
計画書 3.2 の librosa は本実装では「同等アルゴリズム」として置き換えている）:

1. モノラル 22050Hz float 配列
2. オンセット強度（スペクトルフラックス）
3. テンポ推定（オンセット包絡の自己相関 +  octave 処理）
4. ビートグリッド生成（位相推定 → 等間隔グリッド → 局所オンセットで微調整）
5. エネルギープロファイル（100ms 窓 RMS）
6. 信頼度スコア（0〜1）

N1: 5 分間の音楽を 3 秒以内で解析できることを想定した実装（チャンク STFT）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio import ANALYSIS_SR, decode_mono

log = logging.getLogger(__name__)

N_FFT = 2048
HOP = 512
MIN_BPM = 45.0
MAX_BPM = 240.0
#: 自己相関の絶対下限（これを下回る周期性しか無いならリズム不可とみなす）
_MIN_ACF_PEAK = 0.18
_STFT_CHUNK = 2048


@dataclass
class AnalysisResult:
    duration: float
    bpm: float
    beats: list[float]
    confidence: float
    #: "beat" = ビートグリッド / "fixed" = 固定間隔（低信頼度）
    method: str = "beat"
    energy_times: list[float] = field(default_factory=list)
    energy_values: list[float] = field(default_factory=list)
    tempo_candidates: list[tuple[float, float]] = field(default_factory=list)

    @property
    def beat_interval(self) -> float:
        if self.bpm > 0:
            return 60.0 / self.bpm
        if len(self.beats) >= 2:
            return float(np.mean(np.diff(self.beats)))
        return 0.5


class AudioAnalyzer:
    """音声 → BPM / ビート位置 / 信頼度 / エネルギー。"""

    def __init__(self, sr: int = ANALYSIS_SR, n_fft: int = N_FFT, hop: int = HOP) -> None:
        self.sr = int(sr)
        self.n_fft = int(n_fft)
        self.hop = int(hop)
        self._window = np.hanning(self.n_fft).astype(np.float32)

    # ---------------------------------------------------------------
    def analyze_file(self, path: Path | str) -> AnalysisResult:
        pcm, sr = decode_mono(path, self.sr)
        samples = np.frombuffer(pcm, dtype="<f4")
        return self.analyze(samples, sr)

    def analyze(self, samples: np.ndarray, sr: int | None = None) -> AnalysisResult:
        sr = int(sr or self.sr)
        x = np.asarray(samples, dtype=np.float32).ravel()
        duration = float(x.size) / sr if sr else 0.0

        env, fps = self._onset_envelope(x, sr)
        bpm, confidence, candidates = self._estimate_tempo(env, fps)
        beats = self._beat_grid(env, bpm, fps, duration) if bpm > 0 else []

        energy_times, energy_values = self._energy_profile(x, sr)

        method = "beat"
        if len(beats) < 4 or confidence <= 0.0:
            method = "fixed"

        return AnalysisResult(
            duration=duration,
            bpm=float(bpm),
            beats=[float(b) for b in beats],
            confidence=float(confidence),
            method=method,
            energy_times=energy_times,
            energy_values=energy_values,
            tempo_candidates=candidates,
        )

    # ---------------------------------------------------------------
    def _stft_magnitude(self, x: np.ndarray) -> np.ndarray:
        """対数圧縮したスペクトル幅度 (frames, bins) を返す（メモリ節約のためチャンク処理）。"""
        pad = self.n_fft
        padded = np.concatenate(
            [np.zeros(self.n_fft // 2, dtype=np.float32), x, np.zeros(self.n_fft, dtype=np.float32)]
        )
        n_frames = 1 + (padded.size - self.n_fft) // self.hop
        out = np.empty((n_frames, self.n_fft // 2 + 1), dtype=np.float32)
        view = np.lib.stride_tricks.sliding_window_view(padded, self.n_fft)
        for start in range(0, n_frames, _STFT_CHUNK):
            stop = min(start + _STFT_CHUNK, n_frames)
            frames = view[start * self.hop : stop * self.hop : self.hop]
            spec = np.fft.rfft(frames * self._window, axis=1)
            mag = np.abs(spec, dtype=np.float32)
            np.log1p(mag * 64.0, out=mag)  # 音量差よりスペクトル形状を優先
            out[start:stop] = mag
        return out

    def _onset_envelope(self, x: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
        """スペクトルフラックスによるオンセット強度（計画書 5.1-2）。"""
        fps = sr / self.hop
        if x.size < self.n_fft:
            return np.zeros(1, dtype=np.float32), fps
        mag = self._stft_magnitude(x)
        diff = np.diff(mag, axis=0)
        flux = np.maximum(diff, 0.0).sum(axis=1, dtype=np.float32)
        flux = np.concatenate([flux[:1], flux])  # 先頭フレーム分を補う

        kernel = np.array([0.25, 0.5, 0.25], dtype=np.float32)
        env = np.convolve(flux, kernel, mode="same")

        mean = float(env.mean())
        std = float(env.std())
        if std > 1e-9:
            env = (env - mean) / std
        else:
            env = np.zeros_like(env)
        return env, fps

    def _estimate_tempo(self, env: np.ndarray, fps: float) -> tuple[float, float, list[tuple[float, float]]]:
        """オンセット包絡の自己相関からテンポと信頼度を求める（計画書 5.1-3 / 5.1-6）。"""
        if env.size < 8 or fps <= 0:
            return 0.0, 0.0, []

        n = 1 << max(8, int(math.ceil(math.log2(2 * env.size))))
        spec = np.fft.rfft(env, n=n)
        acf = np.fft.irfft(spec * np.conj(spec), n=n)[: env.size]
        if acf[0] <= 1e-9:
            return 0.0, 0.0, []
        acf = acf / acf[0]

        lag_lo = max(2, int(round(fps * 60.0 / MAX_BPM)))
        lag_hi = min(acf.size - 1, int(round(fps * 60.0 / MIN_BPM)))
        if lag_hi <= lag_lo:
            return 0.0, 0.0, []

        lags = np.arange(lag_lo, lag_hi + 1)
        scores = acf[lags].astype(np.float64)
        bpms = fps * 60.0 / lags

        # 120 BPM 周辺をわずかに優遇する事前分布（octave ミス対策）
        prior = np.exp(-0.5 * (np.log2(bpms / 120.0) / 1.15) ** 2)
        weighted = scores * (0.72 + 0.28 * prior)

        order = np.argsort(weighted)[::-1]
        candidates: list[tuple[float, float]] = []
        for idx in order[:5]:
            candidates.append((float(bpms[idx]), float(scores[idx])))

        best = int(order[0])
        peak = float(scores[best])
        # 信頼度: 自己相関ピークの突出度 × 絶対的な周期性の強さ
        mask = np.ones(scores.size, dtype=bool)
        mask[max(0, best - 2) : best + 3] = False
        rest = scores[mask]
        if rest.size:
            prominence = (peak - float(np.median(rest))) / max(peak - float(np.min(rest)) + 1e-9, 1e-9)
        else:
            prominence = 0.0
        strength = float(np.clip((peak - _MIN_ACF_PEAK) / 0.30, 0.0, 1.0))
        confidence = float(np.clip(prominence, 0.0, 1.0) * strength)

        # 放物線補間で BPM を refine
        bpm = float(bpms[best])
        if 0 < best < scores.size - 1:
            y0, y1, y2 = scores[best - 1], scores[best], scores[best + 1]
            denom = y0 - 2 * y1 + y2
            if abs(denom) > 1e-12:
                shift = 0.5 * (y0 - y2) / denom
                shift = float(np.clip(shift, -1.0, 1.0))
                lag_ref = lags[best] + shift
                if lag_ref > 0:
                    bpm = float(fps * 60.0 / lag_ref)

        # 半テンポ/倍テンポの曖昧さを 60〜150 BPM 帯へ寄せる
        bpm = _fold_bpm(bpm, scores, lags, fps)
        return bpm, confidence, candidates

    def _beat_grid(
        self, env: np.ndarray, bpm: float, fps: float, duration: float
    ) -> list[float]:
        """ビートグリッド生成（計画書 5.1-4）。位相推定 → 等間隔 → 局所オンセットで微調整。"""
        if bpm <= 0 or fps <= 0 or env.size < 4:
            return []
        period = fps * 60.0 / bpm
        if period < 2:
            return []
        period_int = int(round(period))

        # 位相探索: comb filters との一致が最大のオフセット
        n_beats = max(1, int((env.size - 1) // period))
        best_phase, best_score = 0, -np.inf
        for phase in range(max(1, period_int)):
            idx = np.round(phase + np.arange(n_beats) * period).astype(np.int64)
            idx = idx[idx < env.size]
            if idx.size < 2:
                continue
            score = float(env[idx].sum())
            if score > best_score:
                best_score, best_phase = score, phase

        idx = np.round(best_phase + np.arange(n_beats) * period).astype(np.int64)
        idx = idx[idx < env.size]

        # 局所オンセットへ微調整（±1/4 ビートまで）
        radius = max(2, int(period * 0.25))
        smoothed = np.convolve(env, np.ones(3, dtype=np.float32) / 3.0, mode="same")
        refined: list[int] = []
        for center in idx:
            lo = max(0, center - radius)
            hi = min(smoothed.size, center + radius + 1)
            window = smoothed[lo:hi]
            if window.size:
                refined.append(int(lo + int(np.argmax(window))))
            else:
                refined.append(int(center))

        beats: list[float] = []
        prev = -1.0
        min_gap = 0.25 * (60.0 / bpm)
        for frame in refined:
            t = frame / fps  # フレーム番号 → 秒（fps = sr / hop）
            if t < 0 or t > duration:
                continue
            if beats and t - prev < min_gap:
                continue
            beats.append(float(t))
            prev = t
        return beats

    def _energy_profile(self, x: np.ndarray, sr: int) -> tuple[list[float], list[float]]:
        """RMS を 100ms 窓で平滑化（計画書 5.1-5）。"""
        win = max(1, int(0.1 * sr))
        n = x.size // win
        if n < 2:
            return [], []
        frames = x[: n * win].reshape(n, win)
        rms = np.sqrt(np.mean(frames.astype(np.float32) ** 2, axis=1))
        kernel = np.ones(5, dtype=np.float32) / 5.0
        rms = np.convolve(rms, kernel, mode="same")
        peak = float(rms.max()) if rms.size else 0.0
        if peak > 1e-9:
            rms = rms / peak
        times = [(i + 0.5) * win / sr for i in range(n)]
        return times, [float(v) for v in rms]


def _fold_bpm(bpm: float, scores: np.ndarray, lags: np.ndarray, fps: float) -> float:
    """半テンポ/倍テンポの曖昧性を選ぶ。"""
    if bpm <= 0:
        return bpm
    def score_at(candidate: float) -> float:
        lag = fps * 60.0 / candidate
        if lag < lags[0] or lag > lags[-1]:
            return -1.0
        i = int(np.argmin(np.abs(lags - lag)))
        return float(scores[i])

    doubled = bpm * 2.0
    halved = bpm / 2.0
    if bpm < 70.0 and doubled <= MAX_BPM:
        if score_at(doubled) >= 0.85 * score_at(bpm):
            return doubled
    if bpm > 170.0 and halved >= MIN_BPM:
        if score_at(halved) >= 0.85 * score_at(bpm):
            return halved
    return bpm


def uniform_grid(bpm: float, duration: float, phase: float = 0.0) -> list[float]:
    """手動 BPM 指定時に使う等間隔グリッド。"""
    if bpm <= 0 or duration <= 0:
        return []
    interval = 60.0 / bpm
    beats: list[float] = []
    t = float(phase)
    while t < 0:
        t += interval
    while t <= duration:
        beats.append(float(t))
        t += interval
    return beats


def fixed_grid(duration: float, interval: float) -> list[float]:
    """リズム検出失敗時の固定間隔グリッド（計画書 5.2 フォールバック）。"""
    if duration <= 0 or interval <= 0:
        return []
    n = int(math.floor(duration / interval))
    return [i * interval for i in range(n + 1)]
