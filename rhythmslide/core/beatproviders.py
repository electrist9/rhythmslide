"""差し込み可能なビート検出（beat provider）。

音声から作る「特徴量」（AudioFeatures）は共通で、そこからビート列
（BeatDetection）を組み立てる論理だけをプロバイダとして差し替える。

- ``BeatProvider``: プロバイダの契約（id / label / version / analyze）
- ``register_beat_provider`` / ``get_beat_provider`` / ``list_beat_providers``: 登録表
- 内蔵プロバイダ: ``acf``（標準の自己相関解析）/ ``funscript``
- ``analyze_audio``: 特徴量抽出 → プロバイダ解析 → AnalysisResult
- ``load_beat_provider_plugins``: データ保存先の ``beatproviders/`` に置かれた
  外部 .py を読み込む（失敗してもアプリは止めない）

id はプロジェクト JSON / キャッシュに保存される言語非依存のキー、label は
メニュー表示用の文字列（日本語）である。
"""
from __future__ import annotations

import importlib.util
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

import numpy as np

from .analyzer import (
    MAX_BPM,
    MIN_BPM,
    _MIN_ACF_PEAK,
    AnalysisResult,
    AudioAnalyzer,
)
from .audio import ANALYSIS_SR, decode_mono, probe_audio
from .funscript import FUNSCRIPT_MODES, find_funscript, load_funscript
from .paths import data_dir

log = logging.getLogger(__name__)


# ======================================================================
# 契約
# ======================================================================
@dataclass
class AudioFeatures:
    """プロバイダへ渡す音声特徴量。解析は共通で、判定論理のみ差し替える。"""

    audio_path: str = ""
    samples: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    sr: int = ANALYSIS_SR
    duration: float = 0.0
    onset_env: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.float32))
    onset_fps: float = 0.0
    energy_times: list[float] = field(default_factory=list)
    energy_values: list[float] = field(default_factory=list)


@dataclass
class BeatDetection:
    """プロバイダの出力。AnalysisResult と同じ形（duration 除く）で下流へ渡す。"""

    bpm: float = 0.0
    beats: list[float] = field(default_factory=list)
    confidence: float = 0.0
    #: "beat" = ビートグリッド / "fixed" = 固定間隔
    method: str = "beat"
    tempo_candidates: list[tuple[float, float]] = field(default_factory=list)


class BeatProvider(Protocol):
    """ビート検出プロバイダの契約。"""

    #: 永続化キー（言語非依存・一意）
    id: str
    #: メニュー表示名（日本語可）
    label: str
    #: 実装の版。キャッシュキーに含め、ロジック変更で古い結果を無効化する
    version: str
    #: True のとき音声デコード済みの特徴量を渡す（False でも duration は埋める）
    requires_audio: bool

    def default_params(self) -> dict[str, Any]:
        """このプロバイダの既定パラメータ（コピーを返す）。"""
        ...

    def sanitize_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        """保存値・外部値を検証し、安全なパラメータへ正規化する。"""
        ...

    def analyze(self, feats: AudioFeatures, params: dict[str, Any]) -> BeatDetection:
        """特徴量とパラメータからビート列を組み立てる。"""
        ...


# ======================================================================
# 登録表
# ======================================================================
_REGISTRY: dict[str, BeatProvider] = {}

#: 既定のプロバイダ（ACF 標準解析）。未知の id を指定した場合のフォールバック先。
DEFAULT_PROVIDER_ID = "acf"


def register_beat_provider(provider):
    """プロバイダを登録する（デコレータとしても使える）。同じ id は上書き。

    クラスを渡されたら引数なしでインスタンス化して登録する。
    """
    if isinstance(provider, type):
        provider = provider()
    pid = str(getattr(provider, "id", "") or "")
    if not pid:
        raise ValueError("beat provider requires a non-empty id")
    _REGISTRY[pid] = provider
    return provider


def get_beat_provider(provider_id: str) -> Optional[BeatProvider]:
    return _REGISTRY.get(str(provider_id or ""))


def list_beat_providers() -> list[BeatProvider]:
    return list(_REGISTRY.values())


def _as_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


# ======================================================================
# 内蔵プロバイダ: ACF（標準）
# ======================================================================
def fold_bpm(bpm: float, scores: np.ndarray, lags: np.ndarray, fps: float) -> float:
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


def estimate_tempo(
    env: np.ndarray, fps: float
) -> tuple[float, float, list[tuple[float, float]]]:
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
    bpm = fold_bpm(bpm, scores, lags, fps)
    return bpm, confidence, candidates


def beat_grid(env: np.ndarray, bpm: float, fps: float, duration: float) -> list[float]:
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


@register_beat_provider
class AcfBeatProvider:
    """標準のリズム解析（オンセット包絡の自己相関 + ビートグリッド）。"""

    id = "acf"
    label = "ACF（標準）"
    version = "1"
    requires_audio = True

    def default_params(self) -> dict[str, Any]:
        return {}

    def sanitize_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        return {}

    def analyze(self, feats: AudioFeatures, params: dict[str, Any]) -> BeatDetection:
        bpm, confidence, candidates = estimate_tempo(feats.onset_env, feats.onset_fps)
        beats = beat_grid(feats.onset_env, bpm, feats.onset_fps, feats.duration) if bpm > 0 else []
        method = "beat"
        if len(beats) < 4 or confidence <= 0.0:
            method = "fixed"
        return BeatDetection(
            bpm=float(bpm),
            beats=[float(b) for b in beats],
            confidence=float(confidence),
            method=method,
            tempo_candidates=candidates,
        )


# ======================================================================
# 内蔵プロバイダ: FunScript
# ======================================================================
@register_beat_provider
class FunScriptBeatProvider:
    """FunScript（譜面）の切替時刻をビート列として扱う。

    音声はデコードせず、path が空なら音声ファイルと同じ場所・検索フォルダから
    同名の .funscript を自動検出する。読めない場合は空のビート列（fixed）で
    失敗を返す（例外は投げず、UI 側で「切替点 0 件」として扱えるようにする）。
    """

    id = "funscript"
    label = "FunScript"
    version = "1"
    requires_audio = False

    def default_params(self) -> dict[str, Any]:
        return {"path": "", "mode": "up", "threshold": 0.0, "step": 100.0, "search_dirs": []}

    def sanitize_params(self, raw: dict[str, Any]) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        mode = str(raw.get("mode", "up"))
        if mode not in FUNSCRIPT_MODES:
            mode = "up"
        threshold = _as_float(raw.get("threshold", 0.0), 0.0)
        if threshold < 0.0:
            threshold = 0.0
        step = _as_float(raw.get("step", 100.0), 100.0)
        if step <= 0.0:
            step = 100.0
        dirs = raw.get("search_dirs")
        search_dirs = [str(d) for d in dirs if str(d).strip()] if isinstance(dirs, (list, tuple)) else []
        return {
            "path": str(raw.get("path", "") or ""),
            "mode": mode,
            "threshold": threshold,
            "step": step,
            "search_dirs": search_dirs,
        }

    def analyze(self, feats: AudioFeatures, params: dict[str, Any]) -> BeatDetection:
        p = self.sanitize_params(params)
        path = p["path"]
        if not path and feats.audio_path:
            found = find_funscript(feats.audio_path, p["search_dirs"])
            path = str(found) if found is not None else ""
        if not path:
            return BeatDetection(beats=[], method="fixed", confidence=0.0)
        try:
            switches = load_funscript(path, p["mode"], p["threshold"], p["step"])
        except ValueError as exc:
            log.warning("FunScript の読み込みに失敗しました（%s）: %s", path, exc)
            return BeatDetection(beats=[], method="fixed", confidence=0.0)
        if feats.duration > 0:
            switches = [t for t in switches if 0.0 <= t <= feats.duration]
        bpm = 0.0
        if len(switches) >= 2:
            diffs = np.diff(np.asarray(switches, dtype=np.float64))
            median = float(np.median(diffs)) if diffs.size else 0.0
            if median > 1e-9:
                bpm = 60.0 / median
        return BeatDetection(bpm=bpm, beats=[float(t) for t in switches], confidence=1.0, method="beat")


# ======================================================================
# オーケストレーター
# ======================================================================
def extract_features(path: Path | str, analyzer: Optional[AudioAnalyzer] = None) -> AudioFeatures:
    """音声ファイルから共通の特徴量（オンセット包絡・エネルギー）を作る。"""
    ar = analyzer or AudioAnalyzer()
    pcm, sr = decode_mono(path, ar.sr)
    samples = np.frombuffer(pcm, dtype="<f4")
    x = np.asarray(samples, dtype=np.float32).ravel()
    sr = int(sr or ar.sr)
    duration = float(x.size) / sr if sr else 0.0
    env, fps = ar.onset_envelope(x, sr)
    energy_times, energy_values = ar.energy_profile(x, sr)
    return AudioFeatures(
        audio_path=str(path),
        samples=x,
        sr=sr,
        duration=duration,
        onset_env=env,
        onset_fps=fps,
        energy_times=energy_times,
        energy_values=energy_values,
    )


def _probe_features(path: Path | str) -> AudioFeatures:
    """音声をデコードせず、長さだけを取得した特徴量（FunScript などで使う）。"""
    duration = 0.0
    try:
        duration = float(probe_audio(path).duration or 0.0)
    except Exception as exc:  # probe 失敗でも解析は進める
        log.debug("音声長の取得に失敗 %s: %s", path, exc)
    return AudioFeatures(audio_path=str(path), duration=duration)


def analyze_audio(
    path: Path | str,
    provider_id: str = DEFAULT_PROVIDER_ID,
    params: Optional[dict[str, Any]] = None,
) -> AnalysisResult:
    """特徴量抽出 → プロバイダ解析 → AnalysisResult。

    未知の provider_id / 不正な params は警告して既定（acf）へフォールバックし、
    プロジェクトが開けなくなることを防ぐ。
    プロバイダがビートを 1 つも作れなかった場合（FunScript でファイルが
    見つからない等）は、標準の ACF 解析へフォールバックしてビートを作る。
    その際 ``AnalysisResult.provider`` には実際にビートを作った id が入る。
    """
    pid = str(provider_id or DEFAULT_PROVIDER_ID)
    provider = get_beat_provider(pid)
    if provider is None:
        log.warning("不明なビートプロバイダ '%s': 標準 '%s' を使用", pid, DEFAULT_PROVIDER_ID)
        pid = DEFAULT_PROVIDER_ID
        provider = get_beat_provider(DEFAULT_PROVIDER_ID)
    if provider is None:  # 理論上あり得ない（acf は常時登録）が防御
        raise RuntimeError("beat provider registry is empty")

    raw: dict[str, Any] = {}
    if isinstance(params, dict):
        raw = params
    elif params is not None:
        log.warning("ビートプロバイダパラメータが辞書ではありません（%s）: 既定値を使用", pid)

    resolved = provider.sanitize_params(raw)
    feats = extract_features(path) if provider.requires_audio else _probe_features(path)
    det = provider.analyze(feats, resolved)
    actual_pid = pid
    if not det.beats and pid != DEFAULT_PROVIDER_ID:
        # プロバイダがビートを作れなければ標準 ACF で作る（FunScript 未検出など）
        fallback = get_beat_provider(DEFAULT_PROVIDER_ID)
        if fallback is not None:
            try:
                if not provider.requires_audio:
                    feats = extract_features(path)
                det = fallback.analyze(feats, fallback.sanitize_params({}))
                actual_pid = DEFAULT_PROVIDER_ID
                log.warning("プロバイダ '%s' はビートを作れませんでした: 標準 ACF を使用", pid)
            except Exception as exc:  # 音声がない等のケースは元の空結果で続ける
                log.warning("標準 ACF へのフォールバックに失敗 (%s): 空の結果を使用", exc)
    return AnalysisResult(
        duration=float(feats.duration),
        bpm=float(det.bpm),
        beats=[float(b) for b in det.beats],
        confidence=float(det.confidence),
        method=str(det.method or "beat"),
        energy_times=list(feats.energy_times),
        energy_values=list(feats.energy_values),
        tempo_candidates=list(det.tempo_candidates),
        provider=actual_pid,
    )


# ======================================================================
# プラグイン読み込み
# ======================================================================
def load_beat_provider_plugins(directory: Optional[Path | str] = None) -> list[str]:
    """データ保存先の ``beatproviders/`` にある .py を読み込み登録する。

    ディレクトリを作成していること自体が「外部コードを実行する」同意だとみなす。
    1 ファイルの失敗は警告して継続する（アプリを落とさない）。
    読み込みに成功したファイル名（stem）の一覧を返す。
    """
    base = Path(directory) if directory else data_dir() / "beatproviders"
    if not base.is_dir():
        return []
    loaded: list[str] = []
    for py in sorted(base.glob("*.py")):
        if py.name.startswith("_"):
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                f"rhythmslide_beatprovider_{py.stem}", py
            )
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            loaded.append(py.stem)
            log.info("ビートプロバイダプラグインを読み込みました: %s", py.name)
        except Exception as exc:
            log.warning("ビートプロバイダプラグインの読み込みに失敗 %s: %s", py.name, exc)
    return loaded
