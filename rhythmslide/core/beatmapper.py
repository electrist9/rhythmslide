"""ビート同期の自動配置（計画書 5.2 / M3）。

写真枚数 N とビートスロット数 S の関係で挙動を変える:

- N == S : 1 枚 = 1 スロット
- N < S  : スロットの方が余る → 写真を循環させて全スロットを毎スロット切り替えで満たす
           （既定の単位=1拍では、4つ打ちなら小節ごとに4回、写真が周回して切り替わる）
- N > S  : 写真の方が余る → 撮影日の分散を保ちながら間引く（日付多様性シンニング）

手動ロック（lock / override_start）は自動配置より優先し、タイムラインを
ロック済み区間で分割したうえで、各空き区間に残りの写真を配置する。
信頼度が下限未満、または method=="fixed" のときは固定間隔グリッドにフォールバックする。
"""
from __future__ import annotations

import logging
import random
from dataclasses import dataclass

from .models import AudioInfo, PhotoItem, Settings, Slide, TRANSITION_TYPES

log = logging.getLogger(__name__)

UNIT_CHOICES: tuple[float, ...] = (0.5, 1, 2, 4, 8, 16)

#: random_transition 時に切り替えごとに選ぶトランジット（"none" は除く＝毎回必ず遷移が付く）
RANDOM_TRANSITION_POOL: tuple[str, ...] = tuple(t for t in TRANSITION_TYPES if t != "none")


@dataclass
class TimelinePlan:
    slides: list[Slide]
    #: 実際に使った配置方式（診断・UI 表示用）
    method: str = "beat"
    unit_beats: int = 4
    #: 間引かれた写真インデックス（素材パネルで薄く表示するため）
    dropped: list[int] | None = None


# ---------------------------------------------------------------------------
# メイン入口
# ---------------------------------------------------------------------------
def build_timeline(
    photos: list[PhotoItem],
    audio: AudioInfo | None,
    settings: Settings,
) -> TimelinePlan:
    """写真一覧と解析済み音声からスライド列を作る（純粋関数・テスト可能）。"""
    if not photos:
        return TimelinePlan(slides=[])
    if audio is None or audio.duration <= 0:
        # 音声なし: 固定間隔で全写真をトラック全域に 1 枚ずつ配置
        dur = _fallback_duration(photos, settings)
        return _fixed_slides(photos, dur, settings)

    duration = float(audio.duration)
    beats = [float(b) for b in audio.beats if 0.0 <= float(b) <= duration]
    fs = [
        float(t) for t in getattr(audio, "funscript_switches", []) if 0.0 <= float(t) <= duration
    ]

    if fs:
        # FunScript があれば、その切替時刻をグリッドとして最優先で使う
        # （信頼度や BPM に依存せず、切替点そのものを境界とする＝単位は 1）。
        grid: list[float] = fs
        prefix = "funscript"
        funit: float | None = 1
    elif (
        audio.method == "beat"
        and audio.confidence >= settings.min_confidence
        and len(beats) >= 4
    ):
        grid = beats
        prefix = "beat"
        funit = None
    else:
        # リズム信頼度不足など: 固定間隔で全写真をトラック全域に 1 枚ずつ配置
        return _fixed_slides(photos, duration, settings)

    # --- ロック済み写真を先に固定し、タイムラインを区間に分割する ---
    locked = _locked_placements(photos, duration, settings)
    segments = _free_segments(locked, duration)

    slides: list[Slide] = []
    dropped: list[int] = []
    method = prefix

    # 各ロック済み写真をスライド化
    for start, end, idx in locked:
        slides.append(_make_slide(photos[idx], idx, start, end, settings))

    # 空き区間へ未ロック写真を順に配分
    unlocked = [i for i, p in enumerate(photos) if not p.is_locked()]
    unlocked = _order_by_time(unlocked, segments, photos)
    cursor = 0
    for seg_start, seg_end in segments:
        span = seg_end - seg_start
        if span <= 0.01:
            continue
        take = _photos_for_segment(unlocked, cursor, segments, seg_start, seg_end, photos)
        seg_grid = [b for b in grid if seg_start - 1e-6 <= b <= seg_end + 1e-6]
        sub = _place_photos(
            [photos[i] for i in take],
            take,
            seg_start,
            seg_end,
            seg_grid,
            audio,
            settings,
            force_unit=funit,
            method_prefix=prefix,
        )
        slides.extend(sub.slides)
        dropped.extend(sub.dropped or [])
        if sub.method != prefix:
            method = sub.method
        cursor += len(take)

    slides.sort(key=lambda s: s.start)
    unit = funit if funit is not None else _resolve_unit(grid, len(photos), settings)
    return TimelinePlan(slides=slides, method=method, unit_beats=unit, dropped=dropped)


# ---------------------------------------------------------------------------
# 配置の中核（ロックなしの単一区間）
# ---------------------------------------------------------------------------
def _fixed_slides(
    photos: list[PhotoItem],
    duration: float,
    settings: Settings,
) -> TimelinePlan:
    """リズム不使用のフォールバック: 全写真をトラック全域に等間隔で 1 枚ずつ配置。

    音声なしでは duration = 枚数 × fixed_interval_s なので 1 枚 = fixed_interval_s。
    循環はせず、必ず写真 1 枚 = スライド 1 枚になる。
    """
    n = len(photos)
    if n == 0:
        return TimelinePlan(slides=[], method="fixed", unit_beats=1)
    interval = duration / n
    slides: list[Slide] = []
    for i in range(n):
        start = i * interval
        end = (i + 1) * interval if i + 1 < n else duration
        slides.append(_make_slide(photos[i], i, start, end, settings))
    return TimelinePlan(slides=slides, method="fixed", unit_beats=1)


def _place_photos(
    photos: list[PhotoItem],
    photo_indices: list[int],
    seg_start: float,
    seg_end: float,
    beats: list[float],
    audio: AudioInfo | None,
    settings: Settings,
    force_unit: int | None = None,
    method_prefix: str = "beat",
) -> TimelinePlan:
    """[seg_start, seg_end] に photos をビートグリッドへ配置する。

    各スロット境界は実ビート時刻に揃う。写真枚数 N とスロット数 S に対し、
    N>S は間引き、N<S は写真の循環で全スロットを毎スロット切り替えで満たす。
    """
    n = len(photos)
    if n == 0:
        return TimelinePlan(slides=[])

    unit = force_unit if force_unit is not None else _resolve_unit(beats, n, settings)
    bounds = _slot_bounds(beats, unit, seg_start, seg_end)
    s = len(bounds) - 1  # スロット数

    if s <= 0:
        # グリッドが作れない → 区内で等間隔
        interval = (seg_end - seg_start) / n
        bounds = [seg_start + i * interval for i in range(n + 1)]
        s = n

    method = method_prefix
    dropped: list[int] = []
    # placements[k] = (photos 内のローカル index, プロジェクト上のグローバル index)
    if n > s:
        # 写真過多 → 日付多様性で間引く
        keep = _thin_by_diversity(list(range(n)), s)
        placements = [(i, photo_indices[i]) for i in keep]
        dropped = [photo_indices[i] for i in range(n) if i not in set(keep)]
        method = method_prefix + "+thin"
    elif n < s:
        # スロット過多 → 写真を循環させて毎スロット（既定は毎ビート）切り替えで満たす。
        # photo_order="pingpong" なら順送り→逆順の往復（boomerang）で循環させる。
        order = getattr(settings, "photo_order", "forward")
        placements = [
            (_cycle_index(k, n, order), photo_indices[_cycle_index(k, n, order)])
            for k in range(s)
        ]
        method = method_prefix + ("+cycle+pingpong" if order == "pingpong" else "+cycle")
    else:
        placements = [(i, photo_indices[i]) for i in range(n)]

    slides: list[Slide] = []
    for k, (local, gi) in enumerate(placements):
        start = bounds[k]
        end = bounds[k + 1] if k + 1 < len(bounds) else seg_end
        if end <= start:
            end = min(seg_end, start + 0.05)
        slides.append(_make_slide(photos[local], gi, start, end, settings))
    return TimelinePlan(slides=slides, method=method, unit_beats=unit, dropped=dropped)


# ---------------------------------------------------------------------------
# 循環順のインデックス
# ---------------------------------------------------------------------------
def _cycle_index(k: int, n: int, order: str = "forward") -> int:
    """スロット k に対する写真のローカル index（0..n-1）を返す。

    - ``"forward"``: 順送り（``k % n``）。
    - ``"pingpong"``: 往復（boomerang）。0,1,..,n-1,n-2,..,1,0,1,.. と三角波で巡る。
      周期は ``2*(n-1)``（n>=2）。n<=1 は常に 0。
    """
    if n <= 1:
        return 0
    if order == "pingpong":
        period = 2 * (n - 1)
        m = k % period
        return m if m < n else period - m
    return k % n


# ---------------------------------------------------------------------------
# スロット境界の生成
# ---------------------------------------------------------------------------
def _resolve_unit(beats: list[float], n: int, settings: Settings) -> float:
    u = settings.unit_beats
    if isinstance(u, (int, float)) and not isinstance(u, bool) and u in UNIT_CHOICES:
        return u
    return _choose_unit(beats, n)


def _choose_unit(beats: list[float], n: int) -> float:
    """スロット数が写真枚数に最も近くなる単位を選ぶ。"""
    if not beats:
        return 1
    best = 1
    best_score = None
    for u in UNIT_CHOICES:
        s = max(1, int(round(len(beats) / u)))
        score = abs(s - n)
        if best_score is None or score < best_score:
            best_score = score
            best = u
    return best


def _grid_times(beats: list[float], unit: float) -> list[float]:
    """unit 拍間隔のグリッド時刻列を作る。

    unit >= 1: 実ビートを unit 個おきに採用（従来動作）。
    unit < 1 : 隣接ビートを 1/unit 等分し、裏拍（例: 0.5=8分音符）も境界に含める。
    """
    if not beats:
        return []
    if unit >= 1:
        step = max(1, int(round(unit)))
        return beats[::step]
    sub = max(2, int(round(1.0 / unit)))  # 0.5 -> 2 分割
    out: list[float] = []
    for i in range(len(beats) - 1):
        b0, b1 = beats[i], beats[i + 1]
        for j in range(sub):
            out.append(b0 + (b1 - b0) * (j / sub))
    out.append(beats[-1])
    return out


def _slot_bounds(beats: list[float], unit: float, seg_start: float, seg_end: float) -> list[float]:
    """unit 拍ごとに切ったスロット境界（先頭=seg_start, 末尾=seg_end を保証）。"""
    inner = _grid_times(beats, unit)
    inner = [b for b in inner if seg_start < b < seg_end]
    bounds = [seg_start] + inner + [seg_end]
    # 単調増加・重複除去
    cleaned: list[float] = []
    for b in bounds:
        if not cleaned or b > cleaned[-1] + 1e-6:
            cleaned.append(float(b))
    if len(cleaned) < 2:
        cleaned = [seg_start, seg_end]
    return cleaned


# ---------------------------------------------------------------------------
# 間引き（日付多様性）
# ---------------------------------------------------------------------------
def _thin_by_diversity(indices: list[int], keep: int) -> list[int]:
    """撮影日順に並んだ写真から keep 枚を等間隔に選び、時間的分散を保つ。"""
    n = len(indices)
    if keep >= n:
        return indices
    if keep <= 1:
        return [indices[0]]
    return [indices[round(k * (n - 1) / (keep - 1))] for k in range(keep)]


# ---------------------------------------------------------------------------
# ロック処理
# ---------------------------------------------------------------------------
def _locked_placements(
    photos: list[PhotoItem], duration: float, settings: Settings
) -> list[tuple[float, float, int]]:
    """ロック済み写真の (start, end, photo_index) を開始時刻順に返す。"""
    items: list[tuple[float, float, int]] = []
    default_len = settings.fixed_interval_s
    for i, p in enumerate(photos):
        if not p.is_locked() or p.override_start is None:
            continue
        start = max(0.0, float(p.override_start))
        length = p.override_duration if p.override_duration is not None else default_len
        end = min(duration, start + max(0.2, float(length)))
        items.append((start, end, i))
    items.sort(key=lambda x: x[0])
    # 重なりは後ろを押し出す
    fixed: list[tuple[float, float, int]] = []
    prev_end = 0.0
    for start, end, i in items:
        start = max(start, prev_end)
        end = max(end, start + 0.2)
        if start >= duration:
            break
        end = min(end, duration)
        fixed.append((start, end, i))
        prev_end = end
    return fixed


def _free_segments(locked: list[tuple[float, float, int]], duration: float) -> list[tuple[float, float]]:
    segs: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end, _ in locked:
        if start > cursor + 0.01:
            segs.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration - 0.01:
        segs.append((cursor, duration))
    return segs


def _order_by_time(unlocked: list[int], segments: list[tuple[float, float]], photos: list[PhotoItem]) -> list[int]:
    # 写真リストは撮影日順が前提。セグメント順にそのまま流し込むため順序は保つ。
    return unlocked


def _photos_for_segment(
    unlocked: list[int],
    cursor: int,
    segments: list[tuple[float, float]],
    seg_start: float,
    seg_end: float,
    photos: list[PhotoItem],
) -> list[int]:
    """空き区間の長さ按比例で未ロック写真を割り当てる。"""
    total_span = sum(e - s for s, e in segments) or 1.0
    span = seg_end - seg_start
    remaining = len(unlocked) - cursor
    remaining_segs = len(segments) - segments.index((seg_start, seg_end))
    if remaining <= 0:
        return []
    if remaining_segs <= 1:
        return unlocked[cursor:]
    share = int(round(remaining * span / total_span))
    share = max(0, min(share, remaining - (remaining_segs - 1)))
    return unlocked[cursor : cursor + share]


# ---------------------------------------------------------------------------
# スライド生成
# ---------------------------------------------------------------------------
def _make_slide(
    photo: PhotoItem, photo_index: int, start: float, end: float, settings: Settings
) -> Slide:
    animated = bool(photo.animated) if photo.animated is not None else False
    if photo.transition:
        # 写真個別の指定が最優先（ユーザーが明示的に選んだものは壊さない）
        transition = photo.transition
    elif settings.random_transition and RANDOM_TRANSITION_POOL:
        # 切り替えごとにランダムなトランジット（"none" 以外の効果から選択）
        transition = random.choice(RANDOM_TRANSITION_POOL)
    else:
        transition = settings.transition
    ken = settings.ken_burns and not animated
    return Slide(
        photo_index=photo_index,
        path=photo.path,
        start=float(start),
        end=float(end),
        transition=transition,
        transition_ms=int(settings.transition_ms),
        ken_burns=ken,
        motion=photo_index % 4,
        locked=photo.is_locked(),
        animated=animated,
    )


def _fallback_duration(photos: list[PhotoItem], settings: Settings) -> float:
    return max(1.0, len(photos) * settings.fixed_interval_s)
