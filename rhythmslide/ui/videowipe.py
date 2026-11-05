"""動画ワイプ表示（映像のすみかぶりオーバーレイ）。

動画ファイルを読み込むと、同じ QMediaPlayer が映像もデコードできる（音声と
完全同期・追加コストなし）。Qt の FFmpeg バックエンドは既定でハードウェア
デコード（d3d11va 等）を自動選択するため、CPU 負荷は軽い。

1 つのメディアオブジェクトに接続できる映像出力は 1 つだけなので、
MainWindow が共有 QVideoSink を 1 つ接続し、そのフレームを
WipeOverlay 群（プレビュー / 全画面ホスト / プレゼン窓）へ fan-out する。

レイアウトは純関数 wipe_geometry() で計算する（Qt 不要・テスト可能）。
バウンディングボックスは表示領域の 1/√8 × 1/√8（面積約 1/8）で、動画の
アスペクト比を維持して収める。余白は既定 16px。
"""
from __future__ import annotations

import math

from PySide6.QtCore import QEvent, Qt
from PySide6.QtMultimediaWidgets import QVideoWidget

from ..core.models import WIPE_CORNERS

#: ワイプと表示領域の端との余白（px）
WIPE_MARGIN_PX = 16


def wipe_geometry(
    parent_w: int,
    parent_h: int,
    video_w: int,
    video_h: int,
    corner: str,
    margin: int = WIPE_MARGIN_PX,
) -> tuple[int, int, int, int]:
    """ワイプの矩形 (x, y, w, h) を返す純関数。

    バウンディングボックスは (parent_w - 2*margin)/√8 × (parent_h - 2*margin)/√8
    （面積は表示領域の約 1/8）。動画サイズが既知ならアスペクト比を維持して
    収め、未知（0）ならボックスそのままを使う。corner は WIPE_CORNERS のいずれか
    （不正値は bottom_right 扱い）。
    """
    if parent_w <= 0 or parent_h <= 0:
        return (0, 0, 0, 0)
    avail_w = max(1, parent_w - 2 * margin)
    avail_h = max(1, parent_h - 2 * margin)
    side = math.sqrt(8.0)  # 面積 1/8 = 一辺 1/√8（実機確認で 1/4 から縮小）
    box_w = avail_w / side
    box_h = avail_h / side
    if video_w > 0 and video_h > 0:
        scale = min(box_w / video_w, box_h / video_h)
        w = max(1, int(video_w * scale))
        h = max(1, int(video_h * scale))
    else:
        w, h = int(box_w), int(box_h)
    if corner == "top_left":
        x, y = margin, margin
    elif corner == "top_right":
        x, y = parent_w - margin - w, margin
    elif corner == "bottom_left":
        x, y = margin, parent_h - margin - h
    else:  # bottom_right（既定）
        x, y = parent_w - margin - w, parent_h - margin - h
    return (x, y, w, h)


class WipeOverlay(QVideoWidget):
    """すみへ置く映像ワイプ。親ウィジェットのサイズ変更に伴って再配置する。

    映像出力は持たない（共有 QVideoSink からの fan-out で MainWindow が
    videoSink().setVideoFrame() を呼ぶ）。マウスは素通しし、親の操作を妨げない。
    """

    def __init__(
        self,
        parent,
        corner: str = "bottom_right",
        margin: int = WIPE_MARGIN_PX,
    ) -> None:
        super().__init__(parent)
        self._corner = corner if corner in WIPE_CORNERS else "bottom_right"
        self._margin = margin
        self._video_w = 0
        self._video_h = 0
        # クリックを下のウィジェットへ渡す（ワイプ自体は操作しない）
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        parent.installEventFilter(self)
        self.setVisible(False)
        self._reposition()

    # ------------------------------------------------------------------
    def set_corner(self, corner: str) -> None:
        """ワイプの位置を変えて再配置する。"""
        if corner in WIPE_CORNERS:
            self._corner = corner
            self._reposition()

    def set_video_size(self, w: int, h: int) -> None:
        """動画のピクセルサイズを覚えてアスペクト比を反映する。"""
        w = int(w) if w and w > 0 else 0
        h = int(h) if h and h > 0 else 0
        if (w, h) != (self._video_w, self._video_h):
            self._video_w, self._video_h = w, h
            self._reposition()

    # ------------------------------------------------------------------
    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # 親のリサイズに合わせてワイプの矩形とサイズを更新する
        if obj is self.parent() and event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def _reposition(self) -> None:
        p = self.parent()
        if p is None:
            return
        x, y, w, h = wipe_geometry(
            p.width(), p.height(), self._video_w, self._video_h,
            self._corner, self._margin,
        )
        self.setGeometry(x, y, w, h)
        self.raise_()
