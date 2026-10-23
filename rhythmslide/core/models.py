"""データモデル（計画書 7 章）。

プロジェクトファイルの JSON 表現と、再生エンジンが使う実行時スライド表現を定義する。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Optional

PROJECT_VERSION = 1

#: 使用可能なトランジション種別（計画書 5.4 / F6）
TRANSITION_TYPES: tuple[str, ...] = (
    "none",
    "crossfade",
    "wipe_left",
    "wipe_right",
    "slide_left",
    "slide_right",
    "fade",
    "zoom",
)

#: 表示単位（ビート数）。1 は各ビートで切り替え（4つ打ちなら小節ごとに4回）。
#: "auto" は写真枚数とビート数から自動決定（計画書 5.2）
UNIT_BEATS_CHOICES: tuple[Any, ...] = ("auto", 0.5, 1, 2, 4, 8, 16)

#: 描画タイマの目標 FPS。高いほどトランジットが滑らかになるが CPU 負荷が増える。
RENDER_FPS_CHOICES: tuple[int, ...] = (30, 60, 144)


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def isoformat(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


@dataclass
class CropRect:
    """正規化されたクロップ領域（0.0〜1.0）。F11 の自動トリミング結果もここに保存する。"""

    x: float = 0.0
    y: float = 0.0
    w: float = 1.0
    h: float = 1.0

    def to_dict(self) -> dict[str, float]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional["CropRect"]:
        if not data:
            return None
        return cls(
            x=float(data.get("x", 0.0)),
            y=float(data.get("y", 0.0)),
            w=float(data.get("w", 1.0)),
            h=float(data.get("h", 1.0)),
        )


@dataclass
class PhotoItem:
    """1 枚の写真と、それに対する手動編集（計画書 F6）。"""

    path: str
    taken_at: Optional[str] = None
    #: BeatMapper が割り当てたスロット番号（手動配置時はスナップ先）
    slot: Optional[int] = None
    #: True の場合、自動配置でも動かさない（計画書 5.2「ユーザー上書き」）
    lock: bool = False
    #: 秒指定の手動開始時刻（None は自動）
    override_start: Optional[float] = None
    #: 秒指定の手動表示長（None は自動）
    override_duration: Optional[float] = None
    #: 写真個別のトランジション種別（None はプロジェクト設定に従う）
    transition: Optional[str] = None
    crop: Optional[CropRect] = None
    #: 素材パネルのフィルタ用（photo_root からの相対フォルダ）
    folder: Optional[str] = None
    #: アニメ GIF かどうか（True/False/None=未調査）
    animated: Optional[bool] = None

    # --- 便利メソッド ---------------------------------------------------
    @property
    def taken_at_dt(self) -> Optional[datetime]:
        return parse_iso(self.taken_at)

    def is_locked(self) -> bool:
        return self.lock or self.override_start is not None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"path": self.path}
        if self.taken_at is not None:
            data["taken_at"] = self.taken_at
        if self.slot is not None:
            data["slot"] = self.slot
        if self.lock:
            data["lock"] = True
        if self.override_start is not None:
            data["override_start"] = round(float(self.override_start), 3)
        if self.override_duration is not None:
            data["override_duration"] = round(float(self.override_duration), 3)
        if self.transition:
            data["transition"] = self.transition
        if self.crop is not None:
            data["crop"] = self.crop.to_dict()
        if self.folder is not None:
            data["folder"] = self.folder
        if self.animated is not None:
            data["animated"] = bool(self.animated)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PhotoItem":
        return cls(
            path=str(data["path"]),
            taken_at=data.get("taken_at"),
            slot=data.get("slot"),
            lock=bool(data.get("lock", False)),
            override_start=_opt_float(data.get("override_start")),
            override_duration=_opt_float(data.get("override_duration")),
            transition=data.get("transition"),
            crop=CropRect.from_dict(data.get("crop")),
            folder=data.get("folder"),
            animated=data.get("animated"),
        )


def _opt_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class PresentationSettings:
    """プレゼンテーションモードの設定（計画書 6 章）。"""

    monitor: str = "auto"  # auto / index / 画面名
    fullscreen: bool = True
    background: str = "#000000"
    #: 余白（レターボックス）の塗り: "solid" = 上記背景色で一律 / "dominant" = 画像の支配色
    background_mode: str = "solid"
    loop: bool = False
    #: 任意の写真（パス）から再生開始
    start_photo: Optional[str] = None
    #: プレゼン出力解像度（None = モニター解像度に合わせる）
    output_width: Optional[int] = None
    output_height: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "monitor": self.monitor,
            "fullscreen": self.fullscreen,
            "background": self.background,
            "background_mode": self.background_mode,
            "loop": self.loop,
            "start_photo": self.start_photo,
            "output_width": self.output_width,
            "output_height": self.output_height,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "PresentationSettings":
        data = data or {}
        mode = str(data.get("background_mode", "solid"))
        if mode not in ("solid", "dominant"):
            mode = "solid"
        return cls(
            monitor=str(data.get("monitor", "auto")),
            fullscreen=bool(data.get("fullscreen", True)),
            background=str(data.get("background", "#000000")),
            background_mode=mode,
            loop=bool(data.get("loop", False)),
            start_photo=data.get("start_photo"),
            output_width=_opt_int(data.get("output_width")),
            output_height=_opt_int(data.get("output_height")),
        )


def _opt_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Settings:
    """自動配置と描画の設定（計画書 7 章 settings）。"""

    unit_beats: Any = 0.5  # "auto" または 0.5/1/2/4/8/16（0.5=半拍=8分音符で切り替え）
    # 既定は素のスライドショー: 表示エフェクト（Ken Burns）なし・切り替え遷移なし。
    # 編集メニューから個別に有効化できる（計画書 5.2/5.3 の機能は維持）。
    transition: str = "none"
    transition_ms: int = 400
    #: 描画タイマの目標 FPS（30/60/144）。高いほどトランジットが滑らか。
    render_fps: int = 60
    ken_burns: bool = False
    #: リズム検出が不確実なときの固定間隔（秒/枚）
    fixed_interval_s: float = 1.75
    #: 信頼度の下限（計画書 5.2: 0.5 未満でフォールバック）
    min_confidence: float = 0.5
    #: 再生（プレゼン）を開始するたびに写真の表示順をランダムにする
    random_order: bool = False
    #: 画像の切り替えごとにトランジットをランダムに選ぶ（写真個別指定が優先）
    random_transition: bool = False
    #: FunScript の切替タイミング導出モード（"up"=上昇のみ / "peak"=局所極値 / "cumulative"=累積距離）
    funscript_mode: str = "up"
    #: pos 差分の最小振幅。これ未満の微小な動きは切替に数えない（0 で全動作）
    funscript_threshold: float = 0.0
    #: cumulative モードで 1 切替ごとに積算する移動距離（pos 単位）
    funscript_step: float = 100.0
    #: 写真の循環順（"forward"=順送り / "pingpong"=往復）
    photo_order: str = "forward"
    presentation: PresentationSettings = field(default_factory=PresentationSettings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "unit_beats": self.unit_beats,
            "transition": self.transition,
            "transition_ms": int(self.transition_ms),
            "render_fps": int(self.render_fps),
            "ken_burns": bool(self.ken_burns),
            "fixed_interval_s": float(self.fixed_interval_s),
            "min_confidence": float(self.min_confidence),
            "random_order": bool(self.random_order),
            "random_transition": bool(self.random_transition),
            "funscript_mode": self.funscript_mode,
            "funscript_threshold": float(self.funscript_threshold),
            "funscript_step": float(self.funscript_step),
            "photo_order": self.photo_order,
            "presentation": self.presentation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> "Settings":
        data = data or {}
        unit = data.get("unit_beats", 0.5)
        if isinstance(unit, str) and unit.lower() != "auto":
            try:
                val = float(unit)
                unit = int(val) if val.is_integer() else val
            except ValueError:
                unit = 0.5
        return cls(
            unit_beats=unit,
            transition=str(data.get("transition", "none")),
            transition_ms=int(data.get("transition_ms", 400)),
            render_fps=_fps(data.get("render_fps"), 60),
            ken_burns=bool(data.get("ken_burns", False)),
            fixed_interval_s=float(data.get("fixed_interval_s", 1.75)),
            min_confidence=float(data.get("min_confidence", 0.5)),
            random_order=bool(data.get("random_order", False)),
            random_transition=bool(data.get("random_transition", False)),
            funscript_mode=_choice(data.get("funscript_mode"), ("up", "peak", "cumulative"), "up"),
            funscript_threshold=_nonneg_float(data.get("funscript_threshold"), 0.0),
            funscript_step=_positive_float(data.get("funscript_step"), 100.0),
            photo_order=_choice(data.get("photo_order"), ("forward", "pingpong"), "forward"),
            presentation=PresentationSettings.from_dict(data.get("presentation")),
        )


def _choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    """文字列を allowed のいずれかに正規化する（不正なら default）。"""
    s = str(value) if value is not None else default
    return s if s in allowed else default


def _fps(value: Any, default: int = 60) -> int:
    """目標 FPS を 1〜240 の整数に正規化する（不正・範囲外なら default）。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        return default
    return v if 1 <= v <= 240 else default


def _nonneg_float(value: Any, default: float) -> float:
    """0 以上の有限な float に正規化する（不正・負なら default）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f >= 0.0 else default


def _positive_float(value: Any, default: float) -> float:
    """正の有限な float に正規化する（不正・0 以下なら default）。"""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f > 0.0 else default


@dataclass
class AudioInfo:
    """解析済み音声トラック（計画書 7 章 audio）。"""

    path: str
    duration: float = 0.0
    bpm: float = 0.0
    confidence: float = 0.0
    beats: list[float] = field(default_factory=list)
    cache_key: str = ""
    analyzed_at: Optional[str] = None
    #: "beat" = ビートグリッド使用 / "fixed" = 固定間隔フォールバック
    method: str = "beat"
    #: 強サビ検出・写真密度に使うエネルギープロファイル（100ms 窓）
    energy_times: list[float] = field(default_factory=list)
    energy_values: list[float] = field(default_factory=list)
    #: FunScript（.funscript）由来の画像切替時刻（秒）。空なら音声解析のビートを使う。
    funscript_path: str = ""
    funscript_switches: list[float] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "duration": round(float(self.duration), 3),
            "bpm": round(float(self.bpm), 3),
            "confidence": round(float(self.confidence), 4),
            "beats": [round(float(b), 4) for b in self.beats],
            "cache_key": self.cache_key,
            "analyzed_at": self.analyzed_at,
            "method": self.method,
            "energy_times": [round(float(t), 3) for t in self.energy_times],
            "energy_values": [round(float(v), 5) for v in self.energy_values],
            "funscript_path": self.funscript_path,
            "funscript_switches": [round(float(t), 4) for t in self.funscript_switches],
        }

    @classmethod
    def from_dict(cls, data: Optional[dict[str, Any]]) -> Optional["AudioInfo"]:
        if not data:
            return None
        return cls(
            path=str(data.get("path", "")),
            duration=float(data.get("duration", 0.0)),
            bpm=float(data.get("bpm", 0.0)),
            confidence=float(data.get("confidence", 0.0)),
            beats=[float(b) for b in data.get("beats", [])],
            cache_key=str(data.get("cache_key", "")),
            analyzed_at=data.get("analyzed_at"),
            method=str(data.get("method", "beat")),
            energy_times=[float(t) for t in data.get("energy_times", [])],
            energy_values=[float(v) for v in data.get("energy_values", [])],
            funscript_path=str(data.get("funscript_path", "")),
            funscript_switches=[float(t) for t in data.get("funscript_switches", [])],
        )


@dataclass
class Project:
    """プロジェクト全体（計画書 7 章）。"""

    version: int = PROJECT_VERSION
    photo_root: str = ""
    audio: Optional[AudioInfo] = None
    settings: Settings = field(default_factory=Settings)
    photos: list[PhotoItem] = field(default_factory=list)

    # --- JSON ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "photo_root": self.photo_root,
            "audio": self.audio.to_dict() if self.audio else None,
            "settings": self.settings.to_dict(),
            "photos": [p.to_dict() for p in self.photos],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        return cls(
            version=int(data.get("version", PROJECT_VERSION)),
            photo_root=str(data.get("photo_root", "")),
            audio=AudioInfo.from_dict(data.get("audio")),
            settings=Settings.from_dict(data.get("settings")),
            photos=[PhotoItem.from_dict(p) for p in data.get("photos", [])],
        )

    # --- 便利メソッド --------------------------------------------------
    def photo_paths(self) -> list[str]:
        return [p.path for p in self.photos]

    def index_of_path(self, path: str) -> int:
        for i, p in enumerate(self.photos):
            if p.path == path:
                return i
        return -1

    def replace_photos(self, photos: Iterable[PhotoItem]) -> None:
        self.photos = list(photos)


# --- 実行時スライド（BeatMapper の出力 / 再生エンジンの入力） -------------

@dataclass
class Slide:
    """タイムライン上の 1 枚分の表示区間。"""

    photo_index: int
    path: str
    start: float
    end: float
    transition: str = "crossfade"
    transition_ms: int = 400
    ken_burns: bool = False
    #: Ken Burns の方向（決定論的に決める。0..3）
    motion: int = 0
    #: 手動ロック由来のスライド
    locked: bool = False
    #: アニメ GIF（表示区間中ループ再生、Ken Burns 無効）
    animated: bool = False

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def transition_seconds(self) -> float:
        # 遷移は現スライドの末尾に重なる。短すぎるスライドで丸ごと遷移に
        # ならないよう、表示長の 40% を上限にする（既定 400ms でも短いスロットで
        # 極端に縮まないよう 0.2 から緩和）。
        return min(self.transition_ms / 1000.0, self.duration * 0.4)
