"""ビート検出プロバイダのサンプル: FunScript（譜面）の切替時刻をビートに。

このファイルは内蔵プロバイダ（id="funscript"）の実装であると同時に、
「音声をデコードしないプロバイダ」の開発者向けサンプルを兼ねている。
（音声を使うプロバイダのサンプルは ``acf.py`` 参照）

自分のプロバイダを作るには、用途に応じて ``acf.py`` かこのファイルを
``<データ保存先>/beatproviders/my_beats.py`` にコピーして書き換える。
ファイルの読み込み規則は ``acf.py`` のモジュール冒頭コメントと同じ。

このプロバイダが示すポイント:

- ``requires_audio = False``: 音声デコードをスキップする。オーケストレーター
  は ``audio_path`` と ``duration``（ffprobe 等で取得した長さ）だけが入った
  軽量な AudioFeatures を渡す。外部ファイルだけでビートを作る場合に使う。
- パラメータ（path / mode / threshold / step / search_dirs）はプロジェクト
  JSON に保存され、次回以降も復元される。
- ``sanitize_params`` で保存値を検証する。プロジェクト JSON は手編集も
  されるため、型・範囲・選択肢のチェックはプロバイダ側で行う。
- ファイルが見つからない・読めない場合は例外を投げず、空のビート列
  （method="fixed", confidence=0.0）を返す。オーケストレーター
  （``analyze_audio``）は空の結果を検知して標準 ACF へフォールバックし、
  その場合はキャッシュに保存しない（後で譜面ファイルが増えても
  古いフォールバック結果がヒットしないようにするため）。
- ビート時刻は必ず ``[0, duration]`` の範囲に収める（下流の Beatmapper が
  範囲外のビートを想定していないため）。
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from rhythmslide.core.beatproviders import (
    AudioFeatures,
    BeatDetection,
    register_beat_provider,
)
from rhythmslide.core.funscript import FUNSCRIPT_MODES, find_funscript, load_funscript

log = logging.getLogger(__name__)


def _as_float(value: Any, default: float) -> float:
    """外部値を安全に float 化する（NaN / inf / 非数値は default へ）。"""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return out


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
        # 保存値は辞書とは限らない（手編集・旧バージョン）ので型から守る
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
        # analyze の入口でも正規化しておく（呼び出し側が生 params を
        # 渡す経路があるため、二重防御で安全側にする）
        p = self.sanitize_params(params)
        path = p["path"]
        if not path and feats.audio_path:
            # 明示指定がなければ音声と同じ場所・検索フォルダから同名を探す
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
            # 音声の長さを超えるビートは下流で扱えないのでここで落とす
            switches = [t for t in switches if 0.0 <= t <= feats.duration]
        bpm = 0.0
        if len(switches) >= 2:
            # 切替間隔の中央値から「目安の BPM」を作る（表示用）
            diffs = np.diff(np.asarray(switches, dtype=np.float64))
            median = float(np.median(diffs)) if diffs.size else 0.0
            if median > 1e-9:
                bpm = 60.0 / median
        # 譜面そのものが正なので信頼度は 1.0（Beatmapper の閾値は必ず通る）
        return BeatDetection(bpm=bpm, beats=[float(t) for t in switches], confidence=1.0, method="beat")
