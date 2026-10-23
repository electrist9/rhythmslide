"""再生エンジンが毎フレーム参照する純粋な描画判定（計画書 5.3 / M4）。

スライド決定とトランジション進行を「時刻 t の純粋関数」として切り出すことで、
GUI なしでユニットテストでき、±30ms の同期精度を clock 駆動で実現する。
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .models import Slide


@dataclass
class RenderState:
    #: 現在（または遷移元）のスライド
    current: Slide | None
    #: 遷移先スライド（遷移中でなければ None）
    incoming: Slide | None
    #: 適用するトランジション種別
    transition: str
    #: トランジション進行 0.0（開始）〜1.0（完了）。遷移なしは 0.0
    progress: float
    #: Ken Burns 進行 0.0〜1.0（現スライド内の経過）
    ken_progress: float


def slide_at(slides: list[Slide], t: float) -> Slide | None:
    """時刻 t に表示されるべきスライド（遷移の有無に関わらず「主」のスライド）。"""
    if not slides:
        return None
    # start 昇順を前提に、t 以下の最大の start を持つスライド
    starts = [s.start for s in slides]
    i = bisect_right(starts, t) - 1
    if i < 0:
        return slides[0] if t < slides[0].start else None
    cur = slides[i]
    if t >= cur.end and i + 1 < len(slides):
        # ギャップをまたいだ場合は次のスライドへ寄せる
        nxt = slides[i + 1]
        if t >= nxt.start:
            return nxt
    return cur


def render_state(slides: list[Slide], t: float) -> RenderState:
    """時刻 t の描画状態（遷移種別と進行度）を返す。"""
    if not slides:
        return RenderState(None, None, "none", 0.0, 0.0)

    starts = [s.start for s in slides]
    i = bisect_right(starts, t) - 1
    if i < 0:
        cur = slides[0]
        return RenderState(cur, None, "none", 0.0, _ken(cur, t))

    cur = slides[i]
    ken = _ken(cur, t)

    # 遷移は現スライド末尾 transition_seconds の間、次スライドと重なる
    if i + 1 < len(slides):
        nxt = slides[i + 1]
        ts = cur.transition_seconds
        if cur.transition != "none" and ts > 0 and t >= cur.end - ts:
            progress = (t - (cur.end - ts)) / ts
            progress = min(1.0, max(0.0, progress))
            return RenderState(cur, nxt, cur.transition, progress, ken)

    return RenderState(cur, None, "none", 0.0, ken)


def _ken(slide: Slide, t: float) -> float:
    if not slide.ken_burns or slide.duration <= 0:
        return 0.0
    return min(1.0, max(0.0, (t - slide.start) / slide.duration))


def total_duration(slides: list[Slide]) -> float:
    if not slides:
        return 0.0
    return max(s.end for s in slides)
