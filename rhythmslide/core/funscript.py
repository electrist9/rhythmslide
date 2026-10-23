"""FunScript ファイル（.funscript）からの画像切替タイミング取得.

形式（例）::

    {"actions": [{"at": 0, "pos": 0}, {"at": 250, "pos": 0},
                 {"at": 500, "pos": 100}, ...], "version": "1.0", ...}

- ``at``: 先頭からの時間（ミリ秒）。
- ``pos``: その時点の位置（0〜range）。

切替タイミングの導出ルール（``mode``）:

- ``"up"``（既定・従来動作）:
    現在の ``pos`` から 1 つ前の ``pos`` を引いた値が **0 より大きい**（正）とき、
    その ``at`` の時刻で画像を切り替える。``threshold`` 未満の微小な上昇は無視。
- ``"peak"``（局所極値）:
    ``pos`` が切り返す頂点（局所最大・局所最小）で切り替える。上昇頂点と下降頂点の
    両方が切替点になり、``up`` より切替が増える。頂点の高さ（前後どちらか小さい側の
    振幅）が ``threshold`` 未満なら無視。
- ``"cumulative"``（累積移動距離）:
    ``|Δpos|`` を積算し、合計が ``step`` に達するたびに切り替える。``threshold`` 未満の
    ステップ（揺れ・ノイズ）は積算に含めない。ストロークの深さが揺れても一定の移動量
    ごとに規則的に切り替わる。

いずれのモードでも先頭 action には「1 つ前」が存在しないため切替点にはしない
（最初の画像は 0 秒から表示される）。重複する ``at`` は 1 回にまとめる。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Optional

#: 切替タイミングの導出モード（UI の選択肢と一致させる）
FUNSCRIPT_MODES: tuple[str, ...] = ("up", "peak", "cumulative")

#: 画像の循環順（UI の選択肢と一致させる）
PHOTO_ORDERS: tuple[str, ...] = ("forward", "pingpong")


def make_preset(
    name: str,
    mode: str,
    threshold: float,
    step: float,
    order: str,
) -> dict[str, Any]:
    """1組の切替ルールを名前付きプリセット（dict）として作る。"""
    return {
        "name": str(name).strip(),
        "mode": mode,
        "threshold": float(threshold),
        "step": float(step),
        "order": order,
    }


def sanitize_presets(raw: Any) -> list[dict[str, Any]]:
    """保存済みプリセット配列を検証・正規化する（設定ファイル後方互換）。

    - 各要素は dict、``name`` は空でない・重複は最初の1つだけ残す。
    - ``mode``/``order`` は既知値でなければ既定へ、``threshold`` は 0 以上、
      ``step`` は正の値に正規化する（不正値は既定）。
    """
    out: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return out
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name or name in seen:
            continue
        mode = str(item.get("mode", "up"))
        if mode not in FUNSCRIPT_MODES:
            mode = "up"
        try:
            thr = float(item.get("threshold", 0.0))
        except (TypeError, ValueError):
            thr = 0.0
        if thr < 0.0:
            thr = 0.0
        try:
            stp = float(item.get("step", 100.0))
        except (TypeError, ValueError):
            stp = 100.0
        if stp <= 0.0:
            stp = 100.0
        order = str(item.get("order", "forward"))
        if order not in PHOTO_ORDERS:
            order = "forward"
        seen.add(name)
        out.append(
            {"name": name, "mode": mode, "threshold": thr, "step": stp, "order": order}
        )
    return out


def _parse_points(actions: list[dict[str, Any]]) -> list[tuple[float, float]]:
    """action 配列から ``(at[ms], pos)`` を ``at`` 昇順に並べた列を作る。

    読み取り不能な要素（dict でない / ``at``・``pos`` が数値でない）は無視する。
    """
    pts: list[tuple[float, float]] = []
    for a in actions:
        if not isinstance(a, dict):
            continue
        try:
            at = float(a["at"])
            pos = float(a["pos"])
        except (KeyError, TypeError, ValueError):
            continue
        pts.append((at, pos))
    pts.sort(key=lambda x: x[0])
    return pts


def _append_switch(out: list[float], t: float) -> None:
    """重複する ``at`` をまとめつつ昇順で切替時刻を追加する。"""
    if not out or t > out[-1] + 1e-9:
        out.append(t)


def _switches_up(pts: list[tuple[float, float]], thr: float) -> list[float]:
    """pos が上昇（差分 > thr）した瞬間で切替（既定・従来動作）。"""
    out: list[float] = []
    for i in range(1, len(pts)):
        if pts[i][1] - pts[i - 1][1] > thr:
            _append_switch(out, pts[i][0] / 1000.0)
    return out


def _switches_peak(pts: list[tuple[float, float]], thr: float) -> list[float]:
    """pos の局所極値（切り返しの頂点）で切替。上昇頂点・下降頂点の両方。"""
    out: list[float] = []
    for i in range(1, len(pts) - 1):
        prev, cur, nxt = pts[i - 1][1], pts[i][1], pts[i + 1][1]
        rise = cur - prev
        fall = cur - nxt
        # 上昇の頂点（局所最大）: 直前より高く、直後以上。prominence = min(rise, fall)
        if cur >= prev and cur >= nxt and min(rise, fall) > thr:
            _append_switch(out, pts[i][0] / 1000.0)
        # 下降の頂点（局所最小）: 直前より低く、直後以下。prominence = min(-rise, -fall)
        elif cur <= prev and cur <= nxt and min(-rise, -fall) > thr:
            _append_switch(out, pts[i][0] / 1000.0)
    return out


def _switches_cumulative(
    pts: list[tuple[float, float]], thr: float, step: float
) -> list[float]:
    """|Δpos| を積算し、合計が step に達するたびに切替。"""
    out: list[float] = []
    if step <= 0.0:
        return out
    acc = 0.0
    for i in range(1, len(pts)):
        d = abs(pts[i][1] - pts[i - 1][1])
        if d <= thr:
            continue  # 揺れ・ノイズは積算しない
        acc += d
        while acc >= step:
            acc -= step
            _append_switch(out, pts[i][0] / 1000.0)
    return out


def switch_times(
    actions: list[dict[str, Any]],
    mode: str = "up",
    threshold: float = 0.0,
    step: float = 100.0,
) -> list[float]:
    """actions 配列から画像切替時刻（秒）を昇順で返す（純粋関数・テスト可能）。

    各 action は ``{"at": <ms>, "pos": <num>}``。``at`` 昇順に並べ、``mode`` に応じた
    規則で切替点を選ぶ。

    - ``mode``: ``"up"`` / ``"peak"`` / ``"cumulative"``（FUNSCRIPT_MODES）。
    - ``threshold``: pos 差分の最小振幅。これ未満の動きは切替に数えない（0 で全動作）。
    - ``step``: ``cumulative`` で 1 切替ごとに積算する移動距離（pos 単位）。
    """
    pts = _parse_points(actions)
    if len(pts) < 2:
        return []
    thr = float(threshold) if threshold and threshold > 0 else 0.0
    if mode == "peak":
        return _switches_peak(pts, thr)
    if mode == "cumulative":
        return _switches_cumulative(pts, thr, float(step))
    return _switches_up(pts, thr)


def load_funscript(
    path: str | Path,
    mode: str = "up",
    threshold: float = 0.0,
    step: float = 100.0,
) -> list[float]:
    """FunScript ファイルを読み込み、切替時刻（秒）を返す。

    不正なファイルや形式の場合は ``ValueError`` を送出する。
    ``mode`` / ``threshold`` / ``step`` は :func:`switch_times` に従う。
    """
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"FunScript を読み込めません: {exc}") from exc
    try:
        data = json.loads(text)
    except ValueError as exc:  # json.JSONDecodeError を含む
        raise ValueError(f"FunScript の JSON が不正です: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("FunScript のトップレベルがオブジェクトではありません")
    actions = data.get("actions")
    if not isinstance(actions, list):
        raise ValueError("FunScript に actions 配列がありません")
    return switch_times(actions, mode, threshold, step)


def find_funscript(
    audio_path: str | Path,
    search_dirs: Iterable[str | Path] = (),
) -> Optional[Path]:
    """音声・動画と同じベースファイル名（拡張子 .funscript）のファイルを探す。

    探索順:
      1. 音声・動画と同じフォルダ（隣接）。
      2. ``search_dirs`` の各フォルダ配下を再帰的に探索（``rglob``）。

    ベースファイル名は大小文字を区別しない。最初に見つかったものを返す。
    見つからなければ ``None``。
    """
    stem = Path(audio_path).stem
    if not stem:
        return None
    # 1) 隣接
    cand = Path(audio_path).with_suffix(".funscript")
    if cand.is_file():
        return cand
    # 2) 指定フォルダ配下を再帰探索
    want = stem.lower()
    for d in search_dirs:
        base = Path(d)
        if not base.is_dir():
            continue
        try:
            for f in base.rglob("*.funscript"):
                if f.is_file() and f.stem.lower() == want:
                    return f
        except OSError:
            continue
    return None
