"""GUI スモークテスト（offscreen）。計画書 M0〜M5 の結合確認。"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image, ImageDraw

from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from rhythmslide.core.analyzer import AudioAnalyzer
from rhythmslide.core.beatcache import audio_info_from_result
from rhythmslide.core.beatmapper import build_timeline
from rhythmslide.core.imagecache import ImageCache
from rhythmslide.core.models import PresentationSettings, Settings
from rhythmslide.ui.mainwindow import MainWindow
from rhythmslide.ui.presentation import PresentationWindow
from rhythmslide.ui.renderer import SlideRenderer


def _make_media(tmp_path):
    import math
    import numpy as np
    import soundfile as sf
    sr, dur, bpm = 22050, 6.0, 120.0
    n = int(sr * dur)
    y = np.zeros(n, dtype=np.float32)
    for b in range(int(dur * bpm / 60) + 1):
        s = int(b * 60 / bpm * sr)
        L = int(0.02 * sr)
        if s + L <= n:
            t = np.arange(L) / sr
            y[s:s + L] += 0.6 * np.sin(2 * math.pi * 1000 * t) * np.exp(-t * 60)
    wav = tmp_path / "click.wav"
    sf.write(str(wav), y, sr)
    imgs = tmp_path / "photos"
    imgs.mkdir()
    for i in range(6):
        im = Image.new("RGB", (320, 240), (40 + i * 30, 90, 200 - i * 20))
        ImageDraw.Draw(im).text((10, 10), f"{i}", fill=(255, 255, 255))
        im.save(imgs / f"p{i}.jpg")
    return str(wav), str(imgs)


def test_mainwindow_constructs(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    assert w.preview is not None
    assert w.timeline is not None
    assert w.material is not None
    w.relayout()  # 空でも例外なし


def test_presentation_window_constructs(qtbot):
    cache = ImageCache()
    win = PresentationWindow(cache, PresentationSettings())
    qtbot.addWidget(win)
    win.set_slides([])
    win.set_time(0.0)


def test_funscript_preset_selection_persisted(qtbot, tmp_path, monkeypatch):
    """記憶した FunScript プリセット選択が起動時の既定ルールへ反映される。"""
    from rhythmslide.core import appconfig
    from rhythmslide.core.funscript import make_preset

    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    appconfig.save_funscript_presets(
        [make_preset("往復プリ", "peak", 15.0, 100.0, "pingpong")]
    )
    cfg = appconfig.load()
    cfg["funscript_preset"] = "往復プリ"
    appconfig.save(cfg)

    w = MainWindow()
    qtbot.addWidget(w)
    s = w.project.settings
    assert s.funscript_mode == "peak"
    assert s.funscript_threshold == 15.0
    assert s.funscript_step == 100.0
    assert s.photo_order == "pingpong"


def test_funscript_preset_missing_keeps_default(qtbot, tmp_path, monkeypatch):
    """記憶名が実在しないプリセットを指す場合、既定ルールを維持する。"""
    from rhythmslide.core import appconfig

    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    cfg = appconfig.load()
    cfg["funscript_preset"] = "存在しないプリセット"
    appconfig.save(cfg)

    w = MainWindow()
    qtbot.addWidget(w)
    s = w.project.settings
    assert s.funscript_mode == "up"
    assert s.photo_order == "forward"


def test_preview_fullscreen_toggle(qtbot):
    """F11 トグル: プレビューが全画面ホスト ⇔ 中央 splitter を行き来する。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QSplitter

    w = MainWindow()
    qtbot.addWidget(w)
    assert w._preview_fullscreen is False
    assert w._fullscreen_host is None
    assert w.centralWidget().widget(0) is w.preview

    # 全画面ホストへ橋渡しされるコールバックを差し替えて計測する
    calls = {"pp": 0, "home": 0}
    w.toggle_play = lambda: calls.__setitem__("pp", calls["pp"] + 1)
    w.play_from_start = lambda: calls.__setitem__("home", calls["home"] + 1)

    # 全画面へ
    w._toggle_preview_fullscreen()
    assert w._preview_fullscreen is True
    host = w._fullscreen_host
    assert host is not None
    assert w.preview.parent() is host          # preview はホスト窓へ再親付け
    assert w.centralWidget().widget(0) is not w.preview
    assert host.cursor().shape() == Qt.CursorShape.BlankCursor  # 全画面中はカーソルを隠す

    # 全画面中でも Space / Home が再生操作へ橋渡しされる
    qtbot.keyClick(host, Qt.Key.Key_Space)
    qtbot.keyClick(host, Qt.Key.Key_Home)
    assert calls["pp"] == 1
    assert calls["home"] == 1

    # 通常表示へ（再度 F11）
    w._toggle_preview_fullscreen()
    assert w._preview_fullscreen is False
    assert w._fullscreen_host is None
    split = w.centralWidget()
    assert isinstance(split, QSplitter)
    assert split.widget(0) is w.preview        # preview が splitter の 0 番目へ復帰


def test_renderer_paints_nonblack(tmp_path, qtbot):
    wav, imgs = _make_media(tmp_path)
    ar = AudioAnalyzer().analyze_file(wav)
    audio = audio_info_from_result(wav, ar)
    from rhythmslide.core.scanner import scan_photo_root
    res = scan_photo_root(imgs, read_exif=False)
    plan = build_timeline(res.photos, audio, Settings(unit_beats=4, transition="crossfade"))
    assert plan.slides

    cache = ImageCache()
    renderer = SlideRenderer(cache)
    img = QImage(320, 240, QImage.Format.Format_RGB32)
    img.fill(0)
    p = QPainter(img)
    renderer.paint(p, 320, 240, 1.0, plan.slides)
    p.end()
    lit = sum(
        1 for yy in range(0, 240, 8) for xx in range(0, 320, 8)
        if sum(img.pixelColor(xx, yy).getRgb()[:3]) > 30
    )
    assert lit > 100


def test_timeline_auto_follow(qtbot):
    """再生中に再生位置が視界の外へ出たら横スクロールで追従する。"""
    from rhythmslide.core.models import Slide
    from rhythmslide.ui.timelineview import TimelineView

    view = TimelineView()
    qtbot.addWidget(view)
    view.resize(300, 120)
    view.show()
    # 全尺 100s × 40px/s でキャンバス幅 4000px → 横スクロールが必要になる
    view.set_data([Slide(photo_index=0, path="a.jpg", start=0.0, end=100.0)], [], 100.0)
    qtbot.wait(50)
    sb = view.horizontalScrollBar()
    assert sb.maximum() > 0
    assert sb.value() == 0

    view.set_playhead(80.0)  # 右端の外 → 前方へ追従
    forward = sb.value()
    assert forward > 0

    view.set_playhead(5.0)   # 左端の外（巻き戻し）→ 後方へ追従
    assert 0 <= sb.value() < forward

    # 追従オフならスクロールバーは動かない
    view.set_playhead(0.0)
    base = sb.value()
    view.auto_follow = False
    view.set_playhead(90.0)
    assert sb.value() == base


def _free_port() -> int:
    """一時的に確保して解放し、空きポート番号を返す。"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_rc_server_binds_localhost_by_default(qtbot):
    """既定は 127.0.0.1 で待ち受け、不正アドレスは 127.0.0.1 へフォールバックする。"""
    from rhythmslide.ui.rcserver import VlcControlServer

    srv = VlcControlServer(object())
    assert srv.start(_free_port()) is True          # host 既定 = 127.0.0.1
    assert srv.is_running() is True
    srv.stop()
    assert srv.is_running() is False

    # 不正な待ち受けアドレスは安全側（127.0.0.1）へフォールバックして起動する
    assert srv.start(_free_port(), "", "不正なアドレス") is True
    assert srv.is_running() is True
    srv.stop()


def test_rc_listen_address_persisted_and_applied(qtbot, tmp_path, monkeypatch):
    """rc_address が config に保存され、起動時のサーバ起動と状態表示に反映される。"""
    from rhythmslide.core import appconfig

    monkeypatch.setenv("RHYTHMSLIDE_DATA_DIR", str(tmp_path))
    port = _free_port()
    cfg = appconfig.load()
    cfg["rc_enabled"] = True
    cfg["rc_port"] = port
    cfg["rc_address"] = "127.0.0.1"
    appconfig.save(cfg)

    w = MainWindow()
    qtbot.addWidget(w)
    assert w._rc_server.is_running() is True
    assert w._config.get("rc_address") == "127.0.0.1"

    # 状態テキストにアドレスとポートが反映される
    txt = w._rc_status_text(True, port, "127.0.0.1")
    assert "127.0.0.1" in txt and str(port) in txt
    # 全インターフェース（LAN 全体）は専用の注記を出す
    assert "全インターフェース" in w._rc_status_text(True, port, "0.0.0.0")
    w._rc_server.stop()
