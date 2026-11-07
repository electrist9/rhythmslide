"""音声特徴量の抽出（計画書 5.1 / M2）と後方互換の解析入口.

librosa と同じ手順を numpy だけで実装する（Python 3.12+ で numba 依存を避けるため。
計画書 3.2 の librosa は本実装では「同等アルゴリズム」として置き換えている）:

1. モノラル 22050Hz float 配列
2. オンセット強度（スペクトルフラックス）

テンポ推定（自己相関 + octave 処理）とビートグリッド生成は
``beatproviders`` の標準プロバイダ（acf）へ移った。本モジュールは
音声 → 特徴量（オンセット包絡・エネルギー）の抽出を受け持ち、
``analyze_file`` / ``analyze`` は既存呼び出し側向けの互換ラッパーである。

N1: 5 分間の音楽を 3 秒以内で解析できることを想定した実装（チャンク STFT）。
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .audio import ANALYSIS_SR

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
    #: ビートを実際に生成したプロバイダ id（空 = 依頼されたプロバイダのまま）
    provider: str = ""

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
    def extract(self, path: Path | str) -> "AudioFeatures":  # noqa: F821
        """音声ファイルから共通特徴量（オンセット包絡・エネルギー）を作る。"""
        from .beatproviders import extract_features

        return extract_features(path, self)

    def analyze_file(self, path: Path | str) -> AnalysisResult:
        """後方互換: 標準プロバイダ（acf）でファイルを解析する。"""
        from .beatproviders import DEFAULT_PROVIDER_ID, analyze_audio

        return analyze_audio(path, DEFAULT_PROVIDER_ID, {})

    def analyze(self, samples: np.ndarray, sr: int | None = None) -> AnalysisResult:
        """後方互換: 渡された音声配列を標準プロバイダ（acf）で解析する。"""
        from .beatproviders import AcfBeatProvider, AudioFeatures

        sr = int(sr or self.sr)
        x = np.asarray(samples, dtype=np.float32).ravel()
        duration = float(x.size) / sr if sr else 0.0
        env, fps = self.onset_envelope(x, sr)
        energy_times, energy_values = self.energy_profile(x, sr)
        feats = AudioFeatures(
            audio_path="",
            samples=x,
            sr=sr,
            duration=duration,
            onset_env=env,
            onset_fps=fps,
            energy_times=energy_times,
            energy_values=energy_values,
        )
        det = AcfBeatProvider().analyze(feats, {})
        return AnalysisResult(
            duration=duration,
            bpm=float(det.bpm),
            beats=list(det.beats),
            confidence=float(det.confidence),
            method=det.method,
            energy_times=energy_times,
            energy_values=energy_values,
            tempo_candidates=list(det.tempo_candidates),
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

    def onset_envelope(self, x: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
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

    def energy_profile(self, x: np.ndarray, sr: int) -> tuple[list[float], list[float]]:
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
