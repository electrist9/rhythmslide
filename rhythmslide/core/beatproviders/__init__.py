"""差し込み可能なビート検出（beat provider）。

音声から作る「特徴量」（AudioFeatures）は共通で、そこからビート列
（BeatDetection）を組み立てる論理だけをプロバイダとして差し替える。

パッケージ構成:

- ``__init__.py``（本ファイル）: プロバイダ契約、登録表、オーケストレーター
  （``analyze_audio``）、プラグイン読み込み
- ``acf.py``: 内蔵プロバイダ「acf」（標準の自己相関解析）。
  プロバイダ開発者向けサンプルも兼ねる
- ``funscript.py``: 内蔵プロバイダ「funscript」（譜面の切替時刻を利用）。
  プロバイダ開発者向けサンプルも兼ねる

主な公開要素:

- ``BeatProvider``: プロバイダの契約（id / label / version / analyze）
- ``register_beat_provider`` / ``get_beat_provider`` / ``list_beat_providers``: 登録表
- ``analyze_audio``: 特徴量抽出 → プロバイダ解析 → AnalysisResult
- ``load_beat_provider_plugins``: データ保存先の ``beatproviders/`` に置かれた
  外部 .py を読み込む（失敗してもアプリは止めない）

id はプロジェクト JSON / キャッシュに保存される言語非依存のキー、label は
メニュー表示用の文字列（日本語）である。

本パッケージの読み込み時に末尾の import で内蔵プロバイダ
（acf / funscript）が登録される。
"""
from __future__ import annotations

import importlib.util
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

import numpy as np

from ..analyzer import AnalysisResult, AudioAnalyzer
from ..audio import ANALYSIS_SR, decode_mono, probe_audio
from ..paths import data_dir

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

    開発の足がかりには、内蔵プロバイダ（本パッケージの ``acf.py`` /
    ``funscript.py``）をコピーして書き換えるのが手軽。

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


# ======================================================================
# 内蔵プロバイダ（これ自体が開発者向けサンプル）
# ======================================================================
# 各モジュールの読み込み時に @register_beat_provider で登録される。
# 末尾に置いてあるのは、上の契約・登録表が定義されてから import する必要があるため。
from .acf import (  # noqa: E402
    AcfBeatProvider,
    beat_grid,
    estimate_tempo,
    fold_bpm,
)
from .funscript import FunScriptBeatProvider  # noqa: E402

__all__ = [
    "AcfBeatProvider",
    "AudioFeatures",
    "BeatDetection",
    "BeatProvider",
    "DEFAULT_PROVIDER_ID",
    "FunScriptBeatProvider",
    "analyze_audio",
    "beat_grid",
    "estimate_tempo",
    "extract_features",
    "fold_bpm",
    "get_beat_provider",
    "list_beat_providers",
    "load_beat_provider_plugins",
    "register_beat_provider",
]
