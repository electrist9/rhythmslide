"""動画ワイプ表示のテスト（純粋な矩形計算 + offscreen GUI）。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from rhythmslide.core.models import Settings, WIPE_CORNERS
from rhythmslide.ui.videowipe import WIPE_MARGIN_PX, wipe_geometry

M = WIPE_MARGIN_PX


# ======================================================================
# 矩形計算（純関数・Qt 不要）
# ======================================================================
def test_corner_anchors():
    """四隅のアンカー: 矩形は余白 M を介して表示領域の隅に接する。"""
    W, H = 1600, 900
    x, y, w, h = wipe_geometry(W, H, 1920, 1080, "bottom_right")
    assert x + w == W - M and y + h == H - M
    x, y, w, h = wipe_geometry(W, H, 1920, 1080, "top_left")
    assert x == M and y == M
    x, y, w, h = wipe_geometry(W, H, 1920, 1080, "top_right")
    assert x + w == W - M and y == M
    x, y, w, h = wipe_geometry(W, H, 1920, 1080, "bottom_left")
    assert x == M and y + h == H - M


def test_aspect_ratio_and_box_half():
    """アスペクト比を維持し、バウンディングボックスは半分×半分（面積約 1/4）。"""
    W, H = 1600, 900
    x, y, w, h = wipe_geometry(W, H, 1920, 1080, "bottom_right")
    assert abs(w / h - 1920 / 1080) < 0.01
    box_w, box_h = (W - 2 * M) / 2, (H - 2 * M) / 2
    assert w <= box_w + 1 and h <= box_h + 1
    assert w == box_w + 1 or h == pytest.approx(box_h, abs=1)  # 縦または横がボックスに接する


def test_unknown_video_size_uses_box():
    """動画サイズが未知（0）ならボックスそのままの矩形になる。"""
    W, H = 800, 600
    x, y, w, h = wipe_geometry(W, H, 0, 0, "bottom_right")
    assert (w, h) == (int((W - 2 * M) / 2), int((H - 2 * M) / 2))


def test_invalid_corner_falls_back_bottom_right():
    good = wipe_geometry(1600, 900, 1920, 1080, "bottom_right")
    bad = wipe_geometry(1600, 900, 1920, 1080, "bogus")
    assert bad == good


def test_degenerate_parent_returns_zero():
    assert wipe_geometry(0, 900, 1920, 1080, "bottom_right") == (0, 0, 0, 0)
    assert wipe_geometry(1600, -5, 1920, 1080, "bottom_right") == (0, 0, 0, 0)


def test_tiny_parent_stays_positive():
    """極小領域でもサイズは 1 以上・表示領域内に収まる。"""
    x, y, w, h = wipe_geometry(20, 20, 1920, 1080, "top_left")
    assert w >= 1 and h >= 1
    assert x >= 0 and y >= 0 and x + w <= 20 and y + h <= 20


# ======================================================================
# 設定の永続化（.rslide 用の to_dict / from_dict）
# ======================================================================
def test_settings_defaults():
    s = Settings()
    assert s.video_wipe is False
    assert s.video_wipe_corner == "bottom_right"


def test_settings_roundtrip():
    s = Settings(video_wipe=True, video_wipe_corner="top_left")
    d = s.to_dict()
    assert d["video_wipe"] is True
    assert d["video_wipe_corner"] == "top_left"
    s2 = Settings.from_dict(d)
    assert s2.video_wipe is True
    assert s2.video_wipe_corner == "top_left"


def test_settings_from_dict_invalid_corner():
    """不正な位置指定は既定の右下へフォールバック。"""
    s = Settings.from_dict({"video_wipe_corner": "center"})
    assert s.video_wipe_corner == "bottom_right"
    assert set(WIPE_CORNERS) == {
        "bottom_right", "bottom_left", "top_right", "top_left",
    }


# ======================================================================
# GUI 結合（offscreen）
# ======================================================================
@pytest.fixture()
def main_window(qtbot, tmp_path, monkeypatch):
    """config を隔離した MainWindow（永続化の交差を避ける）。"""
    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    from rhythmslide.ui.mainwindow import MainWindow

    w = MainWindow()
    qtbot.addWidget(w)
    return w


def test_wipe_defaults(main_window):
    w = main_window
    assert w.project.settings.video_wipe is False
    assert w.project.settings.video_wipe_corner == "bottom_right"
    # プレビューの子として配置され、既定では非表示
    assert w._preview_wipe.parent() is w.preview
    assert not w._preview_wipe.isVisible()
    assert w._act_wipe.isCheckable() and not w._act_wipe.isChecked()


def test_toggle_updates_state(main_window):
    w = main_window
    w.toggle_video_wipe()
    assert w.project.settings.video_wipe is True
    assert w._act_wipe.isChecked()
    # メディアなし（hasVideo False）ならオーバーレイは非表示のまま
    assert not w._preview_wipe.isVisible()
    w.toggle_video_wipe()
    assert w.project.settings.video_wipe is False
    assert not w._act_wipe.isChecked()


def test_corner_menu_selection(main_window):
    w = main_window
    w._change_wipe_corner("top_left")
    assert w.project.settings.video_wipe_corner == "top_left"
    assert w._preview_wipe._corner == "top_left"
    checked = [a for a in w._wipe_corner_actions if a.isChecked()]
    assert len(checked) == 1 and checked[0].data() == "top_left"
    w._change_wipe_corner("bogus")  # 不正値は無視
    assert w.project.settings.video_wipe_corner == "top_left"


def test_wipe_state_persisted_on_close(main_window, tmp_path):
    """終了時に保存 → 次回起動時に ON / 位置が復元される（要件 1）。"""
    w = main_window
    w.toggle_video_wipe()
    w._change_wipe_corner("top_right")
    w.close()

    from rhythmslide.ui.mainwindow import MainWindow

    w2 = MainWindow()
    try:
        assert w2.project.settings.video_wipe is True
        assert w2.project.settings.video_wipe_corner == "top_right"
        assert w2._act_wipe.isChecked()
        assert w2._preview_wipe._corner == "top_right"
    finally:
        w2.close()


def test_fullscreen_host_v_key(main_window):
    """F11 全画面ホストで V キーがワイプ切替へ橋渡しされる（要件 4）。"""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    w = main_window
    calls = []
    w.toggle_video_wipe = lambda: calls.append(1)
    w._toggle_preview_fullscreen()
    assert w._fullscreen_host is not None
    w._fullscreen_host.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.NoModifier)
    )
    assert calls == [1]
    w._toggle_preview_fullscreen()
    # 復帰後もワイプはプレビューの子のまま（F11 の再親付けに追随）
    assert w._preview_wipe.parent() is w.preview


def test_presentation_v_key_signal(qtbot):
    """プレゼン窓の V キーが wipeToggleRequested を発火する（要件 4）。"""
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from rhythmslide.core.imagecache import ImageCache
    from rhythmslide.core.models import PresentationSettings
    from rhythmslide.ui.presentation import PresentationWindow

    win = PresentationWindow(ImageCache(), PresentationSettings())
    qtbot.addWidget(win)
    got = []
    win.wipeToggleRequested.connect(lambda: got.append(1))
    win.keyPressEvent(
        QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.NoModifier)
    )
    assert got == [1]


def test_presentation_wipe_fanout_removed_on_close(main_window, qtbot):
    """プレゼン窓のワイプは fan-out 登録され、窓を閉じると外れる。"""
    from rhythmslide.core.models import PresentationSettings
    from rhythmslide.ui.presentation import PresentationWindow
    from rhythmslide.ui.videowipe import WipeOverlay

    w = main_window
    win = PresentationWindow(w.image_cache, PresentationSettings())
    qtbot.addWidget(win)
    ov = WipeOverlay(win, "bottom_right")
    w._presentation = win
    w._presentation_wipe = ov
    w._wipe_overlays.append(ov)

    w._close_presentation()
    assert ov not in w._wipe_overlays
    assert w._presentation_wipe is None
    assert w._presentation is None
