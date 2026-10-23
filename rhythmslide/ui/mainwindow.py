"""メインウィンドウ（計画書 M1〜M5 の統合）。

写真スキャン→解析→自動レイアウト→プレビュー再生→タイムライン編集→プレゼン
を一画面でつなぐ。音声クロックは QMediaPlayer.position() を描画タイマで読み、
プレビュー・タイムライン・プレゼンへ同じ時刻を配る。
"""
from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThreadPool, QTimer
from PySide6.QtGui import QAction, QKeyEvent, QKeySequence, QUndoStack
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtNetwork import QHostAddress
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDockWidget,
    QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QSpinBox, QSplitter,
    QVBoxLayout, QWidget,
)

from ..core import appconfig
from ..core.audio import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS, has_audio_stream
from ..core.beatcache import BeatCache
from ..core.beatmapper import build_timeline
from ..core.funscript import (
    find_funscript, load_funscript, make_preset, sanitize_presets,
)
from ..core.imagecache import ImageCache
from ..core.models import (
    PresentationSettings, Project, RENDER_FPS_CHOICES, Settings, TRANSITION_TYPES, UNIT_BEATS_CHOICES,
)
from ..core.projectstore import load_project, save_project, try_load_project
from ..core.renderlogic import slide_at, total_duration
from ..core.scanner import IMAGE_EXTENSIONS, scan_photo_root
from ..core.thumbnails import ThumbnailCache
from ..platform.sleepguard import SleepGuard
from .commands import (
    ChangeSettingsCommand, MoveSlideCommand, ReorderPhotoCommand,
    SetTransitionCommand, ToggleLockCommand,
)
from .materialpanel import MaterialPanel
from .preview import PreviewWidget
from .presentation import PresentationWindow
from .rcserver import VlcControlServer
from .timelineview import TimelineView
from .workers import AnalysisWorker, EnrichWorker, ScanWorker, ThumbnailWorker

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("RhythmSlide")
        self.resize(1200, 760)

        self.project = Project()
        self.slides = []
        self.undo_stack = QUndoStack(self)

        self.image_cache = ImageCache()
        self.thumb_cache = ThumbnailCache()
        self.beat_cache = BeatCache()
        self.pool = QThreadPool.globalInstance()
        # 前回のフォルダなどを記憶するアプリ設定（データ保存先に永続化）
        self._config = appconfig.load()

        # --- 音声再生 ---------------------------------------------------
        self.audio_out = QAudioOutput(self)
        self.audio_out.setVolume(0.8)
        self.player = QMediaPlayer(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.errorOccurred.connect(self._on_media_error)

        # --- ウィジェット ------------------------------------------------
        self.preview = PreviewWidget(self.image_cache, self)
        self.timeline = TimelineView(self)
        self.material = MaterialPanel(self.thumb_cache, self)

        # 終了時に保存した既定設定を起動時に反映（メニュー構築前なのでチェック状態も一致）
        self._apply_persisted_settings()
        # 記憶した FunScript プリセット選択を既定ルールへ反映（プロジェクト読込後はそちらが優先）
        self._apply_persisted_funscript_preset()

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self.preview)
        splitter.addWidget(self.timeline)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        dock = QDockWidget("素材", self)
        dock.setWidget(self.material)
        dock.setObjectName("material_dock")
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)

        self._build_menus()
        self._connect()

        # --- 描画/クロック タイマ ---------------------------------------
        self._render_timer = QTimer(self)
        self._render_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._render_timer.timeout.connect(self._on_tick)
        self._presentation: PresentationWindow | None = None
        self._sleep_guard: SleepGuard | None = None
        # F11 でプレビュー（画像表示部分）を全画面 ⇔ ウィンドウ表示へ切り替える状態
        self._preview_fullscreen = False
        self._fullscreen_host: "QWidget | None" = None
        self._closing = False

        # --- 外部制御（VLC 互換 HTTP）----------------------------------
        # 読み込んだ音声・動画の識別に使う連番とパス（status.json の currentplid 用）。
        self._rc_plid = 0
        self._rc_path = ""
        self._rc_server = VlcControlServer(_RcController(self), self)
        if self._config.get("rc_enabled"):
            self._start_rc_server()

        self.statusBar().showMessage("写真フォルダと音声・動画ファイルを選択してください")

    # ==================================================================
    # メニュー
    # ==================================================================
    def _build_menus(self) -> None:
        bar = self.menuBar()
        m_file = bar.addMenu("ファイル")
        self._act(m_file, "写真フォルダを選択…", self.choose_photo_folder, "Ctrl+O")
        self._act(m_file, "音声・動画ファイルを選択…", self.choose_audio, "Ctrl+A")
        self._act(m_file, "FunScript を読み込む…", self.choose_funscript)
        self._act(m_file, "FunScript を解除", self.clear_funscript)
        self._act(m_file, "FunScript 検索フォルダ…", self.manage_funscript_search_dirs)
        self._act(m_file, "FunScript 切替ルール…", self.configure_funscript_rules)
        m_file.addSeparator()
        self._act(m_file, "プロジェクト保存…", self.save_project, "Ctrl+S")
        self._act(m_file, "プロジェクトを開く…", self.open_project, "Ctrl+D")
        m_file.addSeparator()
        self._act(m_file, "終了", self.close, "Ctrl+Q")

        m_edit = bar.addMenu("編集")
        self._act(m_edit, "元に戻す", self.undo_stack.undo, QKeySequence.StandardKey.Undo)
        self._act(m_edit, "やり直し", self.undo_stack.redo, QKeySequence.StandardKey.Redo)
        m_edit.addSeparator()
        self.unit_menu = m_edit.addMenu("単位ビート")
        unit_labels = {
            "auto": "自動",
            0.5: "半拍（8分音符で切替）",
            1: "1拍（各ビートで切替）",
            2: "2拍",
            4: "4拍",
            8: "8拍",
            16: "16拍",
        }
        for u in UNIT_BEATS_CHOICES:
            act = self.unit_menu.addAction(unit_labels.get(u, str(u)))
            act.setCheckable(True)
            act.setChecked(str(u) == str(self.project.settings.unit_beats))
            act.triggered.connect(lambda checked, uu=u: self._change_unit(uu))
        self.trans_menu = m_edit.addMenu("既定のトランジション")
        for kind in TRANSITION_TYPES:
            act = self.trans_menu.addAction(kind)
            act.setCheckable(True)
            act.setChecked(kind == self.project.settings.transition)
            act.triggered.connect(lambda checked, k=kind: self._change_default_transition(k))
        self._act(m_edit, "遷移時間(ms)…", self._edit_transition_ms)
        self.fps_menu = m_edit.addMenu("描画FPS")
        self._fps_actions: dict[int, object] = {}
        for fps in RENDER_FPS_CHOICES:
            act = self.fps_menu.addAction(f"{fps} FPS")
            act.setCheckable(True)
            act.setChecked(fps == self.project.settings.render_fps)
            act.triggered.connect(lambda checked, f=fps: self._change_render_fps(f))
            self._fps_actions[fps] = act
        m_edit.addSeparator()
        self._random_order_action = m_edit.addAction("再生のたびに画像順をランダム化")
        self._random_order_action.setCheckable(True)
        self._random_order_action.setChecked(self.project.settings.random_order)
        self._random_order_action.triggered.connect(self._toggle_random_order)
        self._random_transition_action = m_edit.addAction("切り替えごとにトランジットをランダム化")
        self._random_transition_action.setCheckable(True)
        self._random_transition_action.setChecked(self.project.settings.random_transition)
        self._random_transition_action.triggered.connect(self._toggle_random_transition)
        self._dominant_bg_action = m_edit.addAction("余白を画像の支配色にする")
        self._dominant_bg_action.setCheckable(True)
        self._dominant_bg_action.setChecked(
            self.project.settings.presentation.background_mode == "dominant"
        )
        self._dominant_bg_action.triggered.connect(self._toggle_dominant_background)

        m_play = bar.addMenu("再生")
        self._act(m_play, "再生/一時停止", self.toggle_play, Qt.Key.Key_Space)
        self._act(m_play, "最初から再生", self.play_from_start, "Home")
        m_play.addSeparator()
        self._act(m_play, "プレゼン開始", self.start_presentation, "F5")
        m_play.addSeparator()
        self._act(m_play, "画像を全画面表示 / 戻す", self._toggle_preview_fullscreen, "F11")

        m_tools = bar.addMenu("ツール")
        self._act(m_tools, "外部制御（VLC 互換）…", self.configure_remote_control)

    def _act(self, menu, text, slot, shortcut=None) -> QAction:
        act = QAction(text, self)
        if shortcut is not None:
            if isinstance(shortcut, str):
                act.setShortcut(QKeySequence(shortcut))
            else:
                act.setShortcut(QKeySequence(shortcut))
        act.triggered.connect(slot)
        menu.addAction(act)
        return act

    def _connect(self) -> None:
        self.material.photoActivated.connect(self._preview_photo)
        self.timeline.slideMoved.connect(self._on_slide_moved)
        self.timeline.transitionRequested.connect(self._on_transition)
        self.timeline.lockToggled.connect(self._on_lock_toggle)
        self.timeline.seekRequested.connect(self.seek)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.positionChanged.connect(self._on_position_changed)

    # ==================================================================
    # 素材 / 音声 選択
    # ==================================================================
    def _start_dir(self, key: str, default: str = "") -> str:
        """前回その用途で使ったフォルダ（存在しなければ default）。"""
        d = self._config.get(key)
        if d and Path(d).is_dir():
            return d
        return default

    def _remember_dir(self, key: str, path: str) -> None:
        """選択結果からフォルダを割り出し、設定に保存して次回以降で使う。"""
        p = Path(path)
        d = p if p.is_dir() else p.parent
        if str(d):
            self._config[key] = str(d)
            appconfig.save(self._config)

    def choose_photo_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "写真フォルダを選択", self._start_dir("photo_dir", str(Path.home()))
        )
        if not folder:
            return
        self._remember_dir("photo_dir", folder)
        self.project.photo_root = folder
        worker = ScanWorker(folder)
        worker.signals.step.connect(self.statusBar().showMessage)
        worker.signals.error.connect(lambda m: QMessageBox.warning(self, "スキャン", m))
        worker.signals.finished.connect(self._on_scan_finished)
        self.pool.start(worker)

    def _on_scan_finished(self, result) -> None:
        self.project.replace_photos(result.photos)
        self.material.set_photos(self.project.photos)
        skipped = f"（スキップ {result.skipped}）" if result.skipped else ""
        self.statusBar().showMessage(f"{result.count} 枚を検出{skipped}")
        # 後続: EXIF 補完とサムネイル生成
        enrich = EnrichWorker(self.project.photos)
        enrich.signals.finished.connect(self._on_enrich_finished)
        self.pool.start(enrich)
        self._start_thumbnails(self.project.photos)

    def _on_enrich_finished(self, photos) -> None:
        self.project.replace_photos(photos)
        self.material.set_photos(self.project.photos)
        self.relayout()

    def _start_thumbnails(self, photos) -> None:
        worker = ThumbnailWorker(photos, self.thumb_cache)
        worker.signals.finished.connect(self.material.thumbnail_ready)
        worker.signals.progress.connect(
            lambda done, total: self.statusBar().showMessage(f"サムネイル生成 {done}/{total}")
        )
        self.pool.start(worker)

    def choose_audio(self) -> None:
        audio_pat = " ".join(f"*{e}" for e in sorted(AUDIO_EXTENSIONS))
        video_pat = " ".join(f"*{e}" for e in sorted(VIDEO_EXTENSIONS))
        flt = (
            f"音声・動画ファイル ({audio_pat} {video_pat});;"
            f"音声ファイル ({audio_pat});;"
            f"動画ファイル ({video_pat});;"
            "すべて (*)"
        )
        path, _ = QFileDialog.getOpenFileName(
            self, "音声・動画ファイルを選択", self._start_dir("audio_dir", str(Path.home())), flt
        )
        if not path:
            return
        self._remember_dir("audio_dir", path)
        self.set_audio(path)

    def set_audio(self, path: str, interactive: bool = True) -> None:
        # 動画ファイルの場合も音声トラックのみを使用する。音声トラックが無ければ弾く。
        # interactive=False は外部制御からの読み込み。モーダル表示を避けログに出す。
        if Path(path).suffix.lower() in VIDEO_EXTENSIONS and not has_audio_stream(path):
            if interactive:
                QMessageBox.warning(self, "音声", "この動画ファイルには音声トラックがありません。")
            else:
                log.warning("音声トラックがないため読み込みを拒否: %s", path)
            return
        self.project.audio = None
        # 読み込みのたびに連番を進め、status.json の currentplid 変化で
        # MultiFunPlayer が playlist.json を再取得するようにする。
        self._rc_path = str(path)
        self._rc_plid += 1
        self.player.setSource(_url(path))
        worker = AnalysisWorker(path, self.beat_cache)
        worker.signals.step.connect(self.statusBar().showMessage)
        if interactive:
            worker.signals.error.connect(lambda m: QMessageBox.warning(self, "解析", m))
        else:
            worker.signals.error.connect(lambda m: log.warning("解析エラー: %s", m))
        worker.signals.finished.connect(self._on_analysis_finished)
        self.pool.start(worker)

    def _on_analysis_finished(self, info) -> None:
        self.project.audio = info
        self._maybe_autoload_funscript(info.path)
        self.statusBar().showMessage(
            f"解析完了: {info.bpm:.0f} BPM / 信頼度 {info.confidence:.2f} / {info.duration:.0f}s"
        )
        self.relayout()

    # ==================================================================
    # FunScript（.funscript）による切替タイミング
    # ==================================================================
    def choose_funscript(self) -> None:
        if self.project.audio is None:
            QMessageBox.information(
                self, "FunScript", "先に音声・動画ファイルを選択してください。"
            )
            return
        start = self._start_dir("funscript_dir", str(Path(self.project.audio.path).parent))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "FunScript を読み込む",
            start,
            "FunScript (*.funscript);;すべてのファイル (*.*)",
        )
        if not path:
            return
        self._apply_funscript(path)

    def _apply_funscript(self, path: str) -> bool:
        """FunScript を読み込み、音声情報に切替時刻を反映する。"""
        audio = self.project.audio
        if audio is None:
            return False
        try:
            st = self.project.settings
            switches = load_funscript(
                path, st.funscript_mode, st.funscript_threshold, st.funscript_step
            )
        except ValueError as exc:
            QMessageBox.warning(self, "FunScript", str(exc))
            return False
        if not switches:
            QMessageBox.warning(self, "FunScript", "画像の切替点が見つかりませんでした。")
            return False
        audio.funscript_path = str(path)
        audio.funscript_switches = switches
        self._remember_dir("funscript_dir", path)
        self.statusBar().showMessage(f"FunScript 読込: 切替点 {len(switches)} 件")
        self.relayout()
        return True

    def _maybe_autoload_funscript(self, audio_path: str) -> None:
        """音声・動画と同じベースファイル名の .funscript を探して自動で読み込む。

        探索順: ①音声・動画と同じフォルダ（隣接）→ ②設定した検索フォルダ配下（再帰）。
        """
        cand = self._find_funscript_for(audio_path)
        if cand is not None:
            self._apply_funscript(str(cand))

    def _funscript_search_dirs(self) -> list[str]:
        """FunScript 検索フォルダのリスト（config.json に永続化）。"""
        raw = self._config.get("funscript_search_dirs")
        if isinstance(raw, list):
            return [str(d) for d in raw if str(d)]
        return []

    def _find_funscript_for(self, audio_path: str) -> Optional[Path]:
        """音声・動画と同じベースファイル名（拡張子 .funscript）のファイルを探す。"""
        return find_funscript(audio_path, self._funscript_search_dirs())

    def manage_funscript_search_dirs(self) -> None:
        """FunScript 検索フォルダの一覧を追加/削除して設定に保存する。"""
        dirs = self._funscript_search_dirs()
        dlg = QDialog(self)
        dlg.setWindowTitle("FunScript 検索フォルダ")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            "音声・動画ファイルを選択した際、このフォルダ配下を再帰的に検索し、\n"
            "同じベースファイル名の .funscript があれば自動的に読み込みます。"
        ))
        lst = QListWidget(dlg)
        lst.addItems(dirs)
        layout.addWidget(lst)
        btn_row = QHBoxLayout()
        btn_add = QPushButton("追加…", dlg)
        btn_remove = QPushButton("削除", dlg)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_remove)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        def add_dir() -> None:
            start = dirs[-1] if dirs else self._start_dir("funscript_dir", str(Path.home()))
            folder = QFileDialog.getExistingDirectory(dlg, "検索フォルダを追加", start)
            if folder and folder not in dirs:
                dirs.append(folder)
                lst.addItem(folder)

        def remove_dir() -> None:
            item = lst.currentItem()
            if item is None:
                return
            folder = item.text()
            if folder in dirs:
                dirs.remove(folder)
            lst.takeItem(lst.row(item))

        btn_add.clicked.connect(add_dir)
        btn_remove.clicked.connect(remove_dir)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        layout.addWidget(box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._config["funscript_search_dirs"] = dirs
        appconfig.save(self._config)
        if self.project.audio is not None and not self.project.audio.funscript_switches:
            cand = self._find_funscript_for(self.project.audio.path)
            if cand is not None:
                self._apply_funscript(str(cand))

    def clear_funscript(self) -> None:
        audio = self.project.audio
        if audio is None or not audio.funscript_switches:
            return
        audio.funscript_path = ""
        audio.funscript_switches = []
        self.statusBar().showMessage("FunScript を解除しました")
        self.relayout()

    def configure_funscript_rules(self) -> None:
        """FunScript の切替タイミングと画像循環順を設定するダイアログ。

        4項目（モード / 振幅しきい値 / 累積ステップ / 循環順）を 1 組の
        名前付きプリセットとして保存・呼び出しできます。プリセットは設定
        （config.json）とは別のファイル（funscript_presets.json）に保存され、
        プロジェクトや再起動をまたいで利用できます。
        変更したら設定に保存し、FunScript が読み込まれていれば同じファイルで
        切替時刻を再計算して再レイアウトする（読み込み中でなければ循環順だけ反映）。
        """
        st = self.project.settings
        presets = sanitize_presets(appconfig.load_funscript_presets())

        dlg = QDialog(self)
        dlg.setWindowTitle("FunScript 切替ルール")
        form = QFormLayout(dlg)

        # --- 4項目のウィジェット（先に作成し、プリセット適用から参照できるようにする）---
        mode_box = QComboBox(dlg)
        for key, label in (
            ("up", "上昇のみ（既定）"),
            ("peak", "局所極値（切り返しの頂点）"),
            ("cumulative", "累積移動距離"),
        ):
            mode_box.addItem(label, key)
        idx = mode_box.findData(st.funscript_mode)
        mode_box.setCurrentIndex(idx if idx >= 0 else 0)

        thr_box = QDoubleSpinBox(dlg)
        thr_box.setRange(0.0, 100.0)
        thr_box.setSingleStep(5.0)
        thr_box.setDecimals(1)
        thr_box.setSuffix(" (pos)")
        thr_box.setValue(float(st.funscript_threshold))

        step_box = QDoubleSpinBox(dlg)
        step_box.setRange(1.0, 1000.0)
        step_box.setSingleStep(10.0)
        step_box.setDecimals(1)
        step_box.setSuffix(" (pos)")
        step_box.setValue(float(st.funscript_step))

        order_box = QComboBox(dlg)
        for key, label in (
            ("forward", "順送り（既定）"),
            ("pingpong", "往復（ping-pong）"),
        ):
            order_box.addItem(label, key)
        oidx = order_box.findData(st.photo_order)
        order_box.setCurrentIndex(oidx if oidx >= 0 else 0)

        # --- プリセット選択・保存・削除 ---
        preset_box = QComboBox(dlg)
        # 選択中のプリセット名（"" はカスタム＝プリセット非選択）。applying は
        # プリセット適用中のフィールド変更を「手動編集」と誤認しないためのガード。
        chosen = {"name": ""}
        applying = {"v": False}

        def refresh_presets(select_name: str = "") -> None:
            """プリセット一覧を combo に再構築する（信号は止めて再帰適用を防ぐ）。"""
            preset_box.blockSignals(True)
            preset_box.clear()
            preset_box.addItem("（プリセットを選択）", None)
            for p in presets:
                preset_box.addItem(p["name"], p["name"])
            i = preset_box.findData(select_name) if select_name else 0
            preset_box.setCurrentIndex(i if i >= 0 else 0)
            preset_box.blockSignals(False)

        def apply_preset(index: int) -> None:
            """選択されたプリセットを 4項目のウィジェットに反映し、選択名を記憶する。"""
            if index <= 0:  # 0 は「（プリセットを選択）」プレースホルダ
                chosen["name"] = ""
                return
            name = preset_box.itemData(index)
            p = next((x for x in presets if x["name"] == name), None)
            if p is None:
                chosen["name"] = ""
                return
            applying["v"] = True
            try:
                mi = mode_box.findData(p["mode"])
                if mi >= 0:
                    mode_box.setCurrentIndex(mi)
                thr_box.setValue(p["threshold"])
                step_box.setValue(p["step"])
                oi = order_box.findData(p["order"])
                if oi >= 0:
                    order_box.setCurrentIndex(oi)
            finally:
                applying["v"] = False
            chosen["name"] = str(name)

        def mark_custom(*_args) -> None:
            """4項目を手動編集したら選択を「カスタム」へ戻す（プリセット適用中は無視）。"""
            if applying["v"]:
                return
            chosen["name"] = ""
            preset_box.blockSignals(True)
            preset_box.setCurrentIndex(0)
            preset_box.blockSignals(False)

        def save_preset() -> None:
            """現在の 4項目を名前付きプリセットとして保存（同名は上書き）。"""
            default_name = ""
            if preset_box.currentIndex() > 0:
                default_name = str(preset_box.currentText())
            name, ok = QInputDialog.getText(
                self, "プリセットを保存", "プリセット名:", text=default_name
            )
            if not ok:
                return
            name = name.strip()
            if not name:
                return
            p = make_preset(
                name, str(mode_box.currentData()), float(thr_box.value()),
                float(step_box.value()), str(order_box.currentData()),
            )
            presets[:] = [x for x in presets if x["name"] != name]
            presets.append(p)
            refresh_presets(name)
            chosen["name"] = name  # 保存したプリセットが選択状態になる

        def delete_preset() -> None:
            """選択中のプリセットを一覧から削除する。"""
            if preset_box.currentIndex() <= 0:
                return
            name = preset_box.itemData(preset_box.currentIndex())
            presets[:] = [x for x in presets if x["name"] != name]
            refresh_presets()
            chosen["name"] = ""  # 削除後は選択解除（カスタム扱い）

        preset_box.currentIndexChanged.connect(apply_preset)
        # 4項目の手動編集を検知して選択をカスタムへ戻す
        mode_box.currentIndexChanged.connect(mark_custom)
        thr_box.valueChanged.connect(mark_custom)
        step_box.valueChanged.connect(mark_custom)
        order_box.currentIndexChanged.connect(mark_custom)
        save_btn = QPushButton("保存", dlg)
        save_btn.clicked.connect(save_preset)
        del_btn = QPushButton("削除", dlg)
        del_btn.clicked.connect(delete_preset)
        preset_row = QHBoxLayout()
        preset_row.addWidget(preset_box, 1)
        preset_row.addWidget(save_btn)
        preset_row.addWidget(del_btn)

        # 行の並び（プリセットを最上部に）
        form.addRow("プリセット", preset_row)
        form.addRow("切替タイミング", mode_box)
        form.addRow("振幅しきい値", thr_box)
        form.addRow("累積ステップ", step_box)
        form.addRow("画像の循環順", order_box)

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        form.addRow(box)

        # 記憶したプリセット選択を復元（実在すれば 4項目へ適用して combo と一致させる）
        remembered = str(self._config.get("funscript_preset", ""))
        refresh_presets(remembered)
        if remembered and any(x["name"] == remembered for x in presets):
            apply_preset(preset_box.currentIndex())

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        st.funscript_mode = str(mode_box.currentData())
        st.funscript_threshold = float(thr_box.value())
        st.funscript_step = float(step_box.value())
        st.photo_order = str(order_box.currentData())

        # プリセット一覧を別ファイル（funscript_presets.json）に保存（プロジェクト非依存で再利用できる）
        appconfig.save_funscript_presets(presets)
        # 選択したプリセット名を config.json に記憶（"" はカスタム＝選択なし）
        self._config["funscript_preset"] = chosen["name"]
        appconfig.save(self._config)

        audio = self.project.audio
        if audio is not None and audio.funscript_path:
            # 同じ FunScript を新しいルールで読み直して切替時刻を再計算する
            self._apply_funscript(audio.funscript_path)
        else:
            self.relayout()
        self.statusBar().showMessage("FunScript 切替ルールを更新しました")

    def _on_media_error(self, error, message: str) -> None:
        # 動画コンテナなど QMediaPlayer が再生できない形式のフィードバック。
        if error != QMediaPlayer.Error.NoError:
            self.statusBar().showMessage(f"再生エラー: {message or 'このファイルは再生できません'}")

    # ==================================================================
    # レイアウト
    # ==================================================================
    def relayout(self) -> None:
        plan = build_timeline(self.project.photos, self.project.audio, self.project.settings)
        self.slides = plan.slides
        dur = total_duration(self.slides)
        audio = self.project.audio
        if audio is not None and audio.funscript_switches:
            beats = audio.funscript_switches
        else:
            beats = audio.beats if audio is not None else []
        self.preview.set_slides(self.slides)
        self.preview.set_background(self.project.settings.presentation.background)
        self.preview.set_background_mode(self.project.settings.presentation.background_mode)
        self.timeline.set_data(self.slides, beats, dur)
        if self._presentation is not None:
            self._presentation.set_slides(self.slides)
        self.statusBar().showMessage(
            f"スライド {len(self.slides)} 枚 / 総尺 {dur:.1f}s / 方式 {plan.method}"
        )

    # ==================================================================
    # 再生クロック
    # ==================================================================
    def toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self._render_timer.stop()
        else:
            # 最初（位置 0）から再生するときは、設定が有効なら写真順をシャッフル
            if self.player.position() == 0:
                self._reshuffle_photos()
            if not self.slides:
                self.relayout()
            self._start_playback()

    def play_from_start(self) -> None:
        self._reshuffle_photos()
        self.seek(0.0)
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._start_playback()

    def _start_playback(self) -> None:
        self.player.play()
        self._render_timer.start(self._render_interval_ms())

    def _render_interval_ms(self) -> int:
        """目標 FPS から描画タイマの間隔（ms）を返す（144FPS→約7ms、60→約17ms）。"""
        fps = getattr(self.project.settings, "render_fps", 60) or 60
        return max(1, round(1000.0 / fps))

    def _change_render_fps(self, fps: int) -> None:
        """描画 FPS を変え、再生中ならタイマを新間隔で張り直し、プレゼンにも反映。"""
        self.project.settings.render_fps = fps
        for act_fps, act in self._fps_actions.items():
            act.setChecked(act_fps == fps)
        interval = self._render_interval_ms()
        if self._render_timer.isActive():
            self._render_timer.start(interval)
        if self._presentation is not None:
            self._presentation.set_render_interval(interval)
        self.statusBar().showMessage(f"描画 FPS を {fps} にしました")

    def _toggle_random_order(self, checked: bool) -> None:
        self.project.settings.random_order = checked

    def _toggle_random_transition(self, checked: bool) -> None:
        # 切り替えごとにトランジットをランダム選択。ON にした時点で再レイアウトし、
        # タイムラインに実際の遷移を反映する（写真個別指定があればそれが優先）。
        self.project.settings.random_transition = checked
        self.relayout()

    def _toggle_dominant_background(self, checked: bool) -> None:
        ps = self.project.settings.presentation
        ps.background_mode = "dominant" if checked else "solid"
        self.preview.set_background_mode(ps.background_mode)
        if self._presentation is not None:
            self._presentation.renderer.set_background_mode(ps.background_mode)
        self.preview.update()

    def _reshuffle_photos(self) -> None:
        """random_order が有効なら写真順をシャッフルして再レイアウトする。"""
        if not self.project.settings.random_order or len(self.project.photos) < 2:
            return
        random.shuffle(self.project.photos)
        self.material.set_photos(self.project.photos)
        self.relayout()

    def seek(self, t: float) -> None:
        t = max(0.0, t)
        self.player.setPosition(int(t * 1000))
        self._push_time(t)

    def _on_position_changed(self, ms: int) -> None:
        # 再生中は positionChanged が来るが、描画はタイマ主導。ここでは何もしない。
        pass

    def _on_tick(self) -> None:
        t = self.player.position() / 1000.0
        self._push_time(t)
        self.preview.renderer.advance_animation(self._render_interval_ms())
        if self._presentation is not None:
            self._presentation.set_time(t)
        # ループ処理
        total = total_duration(self.slides)
        if total > 0 and t >= total - 0.02:
            if self.project.settings.presentation.loop:
                self.seek(0.0)
            else:
                self.player.pause()
                self._render_timer.stop()

    def _push_time(self, t: float) -> None:
        self.preview.set_time(t)
        self.timeline.set_playhead(t)

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            total = total_duration(self.slides)
            if self.project.settings.presentation.loop and total > 0:
                self.player.setPosition(0)
                self.player.play()
            else:
                self._render_timer.stop()

    # ==================================================================
    # タイムライン編集
    # ==================================================================
    def _on_slide_moved(self, photo_index: int, start: float, duration: float) -> None:
        idx = self.project.index_of_path(self._path_at(photo_index))
        if idx < 0:
            return
        self.undo_stack.push(MoveSlideCommand(self.project, idx, start, duration))
        self.relayout()

    def _on_transition(self, photo_index: int, transition: str) -> None:
        idx = self.project.index_of_path(self._path_at(photo_index))
        if idx < 0:
            return
        self.undo_stack.push(SetTransitionCommand(self.project, idx, transition))
        self.relayout()

    def _on_lock_toggle(self, photo_index: int) -> None:
        idx = self.project.index_of_path(self._path_at(photo_index))
        if idx < 0:
            return
        # 現在のスライド位置を探し、ロック時に手動配置として固定する
        start = dur = None
        for s in self.slides:
            if s.photo_index == idx:
                start = s.start
                dur = s.end - s.start
                break
        self.undo_stack.push(ToggleLockCommand(self.project, idx, start, dur))
        self.relayout()

    def _path_at(self, photo_index: int) -> str:
        if 0 <= photo_index < len(self.project.photos):
            return self.project.photos[photo_index].path
        return ""

    # ==================================================================
    # 設定変更
    # ==================================================================
    def _change_unit(self, unit) -> None:
        self.undo_stack.push(ChangeSettingsCommand(self.project.settings, "unit_beats", unit))
        for act in self.unit_menu.actions():
            act.setChecked(act.text() == str(unit))
        self.relayout()

    def _change_default_transition(self, kind: str) -> None:
        self.undo_stack.push(ChangeSettingsCommand(self.project.settings, "transition", kind))
        for act in self.trans_menu.actions():
            act.setChecked(act.text() == kind)
        self.relayout()

    def _edit_transition_ms(self) -> None:
        val, ok = QInputDialog.getInt(
            self, "遷移時間", "トランジション時間 (ms):",
            self.project.settings.transition_ms, 0, 3000, 50,
        )
        if ok:
            self.undo_stack.push(ChangeSettingsCommand(self.project.settings, "transition_ms", val))
            self.relayout()

    # ==================================================================
    # プレゼン
    # ==================================================================
    def start_presentation(self) -> None:
        self._reshuffle_photos()
        if not self.slides:
            self.relayout()
        if not self.slides:
            QMessageBox.information(self, "プレゼン", "表示するスライドがありません。")
            return
        ps = self.project.settings.presentation
        # プレゼン中はスリープ・画面オフを抑止
        if self._sleep_guard is None:
            self._sleep_guard = SleepGuard()
            self._sleep_guard.start()
        self._presentation = PresentationWindow(self.image_cache, ps)
        self._presentation.set_render_interval(self._render_interval_ms())
        self._presentation.set_slides(self.slides)
        self._presentation.closeRequested.connect(self._close_presentation)
        self._presentation.nextRequested.connect(lambda: self._step(1))
        self._presentation.prevRequested.connect(lambda: self._step(-1))
        self._presentation.togglePauseRequested.connect(self.toggle_play)
        self._presentation.show_on_screen(ps.monitor)

        start_t = 0.0
        if ps.start_photo:
            for s in self.slides:
                if s.path == ps.start_photo:
                    start_t = s.start
                    break
        self.seek(start_t)
        if self.player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            self._start_playback()

    def _close_presentation(self) -> None:
        if self._presentation is not None:
            self._presentation.deleteLater()
            self._presentation = None
        if self._sleep_guard is not None:
            self._sleep_guard.stop()
            self._sleep_guard = None

    # ==================================================================
    # 画像表示部分の全画面トグル（F11）
    # ==================================================================
    def _toggle_preview_fullscreen(self) -> None:
        """F11 でプレビュー（画像表示部分）を全画面 ⇔ ウィンドウ表示へ切り替える。"""
        if self._preview_fullscreen:
            self._exit_preview_fullscreen()
        else:
            self._enter_preview_fullscreen()

    def _enter_preview_fullscreen(self) -> None:
        """プレビューをボーダーレス全画面のホスト窓へ移し、メイン窓を隠す。"""
        if self._fullscreen_host is not None:
            return
        host = _FullscreenPreviewHost(
            self._toggle_preview_fullscreen, self._exit_preview_fullscreen,
            self.toggle_play, self.play_from_start,
        )
        layout = QVBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.preview)  # preview をホスト窓へ再親付け
        # メインウィンドウと同じスクリーンに全画面を出す
        screen = self.screen()
        if screen is not None:
            host.setScreen(screen)
            host.setGeometry(screen.geometry())
        self._fullscreen_host = host
        self._preview_fullscreen = True
        self.hide()
        host.showFullScreen()
        host.activateWindow()
        host.raise_()
        self.preview.update()

    def _exit_preview_fullscreen(self) -> None:
        """プレビューを元の中央 splitter へ戻し、ホスト窓を閉じてメイン窓を再表示する。"""
        host = self._fullscreen_host
        if host is None:
            return
        # 再入防止: 先に状態を戻してから窓を閉じる（closeEvent 経由で再度呼ばれても無視）
        self._fullscreen_host = None
        self._preview_fullscreen = False
        split = self.centralWidget()
        if isinstance(split, QSplitter):
            split.insertWidget(0, self.preview)  # preview を中央 splitter の 0 番目へ戻す
        host.close()
        host.deleteLater()
        if not self._closing:
            self.show()
            self.activateWindow()
        self.preview.update()

    def _step(self, direction: int) -> None:
        t = self.player.position() / 1000.0
        cur = slide_at(self.slides, t)
        if cur is None:
            return
        idx = self.slides.index(cur)
        nxt = max(0, min(len(self.slides) - 1, idx + direction))
        self.seek(self.slides[nxt].start)

    # ==================================================================
    # 保存 / 読み込み
    # ==================================================================
    def save_project(self) -> None:
        start = "project.rslide"
        last = self._config.get("project_dir")
        if last and Path(last).is_dir():
            start = str(Path(last) / "project.rslide")
        path, _ = QFileDialog.getSaveFileName(
            self, "プロジェクト保存", start, "RhythmSlide (*.rslide)"
        )
        if not path:
            return
        self._remember_dir("project_dir", path)
        try:
            save_project(self.project, path)
            self.statusBar().showMessage(f"保存しました: {path}")
        except OSError as exc:
            QMessageBox.warning(self, "保存", f"保存に失敗: {exc}")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "プロジェクトを開く", self._start_dir("project_dir", ""), "RhythmSlide (*.rslide)"
        )
        if not path:
            return
        self._remember_dir("project_dir", path)
        self._load_project_path(path)

    def _load_project_path(self, path: str) -> bool:
        proj, err = try_load_project(path)
        if err:
            QMessageBox.warning(self, "読み込み", err)
            return False
        self.project = proj
        self.undo_stack.clear()
        self._random_order_action.setChecked(self.project.settings.random_order)
        self._random_transition_action.setChecked(self.project.settings.random_transition)
        for fps, act in self._fps_actions.items():
            act.setChecked(fps == self.project.settings.render_fps)
        self._dominant_bg_action.setChecked(
            self.project.settings.presentation.background_mode == "dominant"
        )
        self.material.set_photos(self.project.photos)
        if self.project.audio:
            self.player.setSource(_url(self.project.audio.path))
        self.relayout()
        self.statusBar().showMessage(f"読み込みました: {path}")
        return True

    # ==================================================================
    def _preview_photo(self, path: str) -> None:
        for s in self.slides:
            if s.path == path:
                self.seek(s.start)
                break

    # ==================================================================
    # 外部制御（VLC 互換 HTTP インターフェース）
    # ==================================================================
    def configure_remote_control(self) -> None:
        """外部制御の On/Off・ポート・パスワードを設定して保存する。"""
        cfg = self._config
        dlg = QDialog(self)
        dlg.setWindowTitle("外部制御（VLC 互換）")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel(
            "MultiFunPlayer などの外部アプリから、VLC と同じ HTTP インターフェースで\n"
            "再生・停止・シーク・ファイル読み込みを行えます。"
        ))
        enable = QCheckBox("外部制御を有効にする", dlg)
        enable.setChecked(bool(cfg.get("rc_enabled", False)))
        layout.addWidget(enable)

        form = QFormLayout()
        port = QSpinBox(dlg)
        port.setRange(1, 65535)
        port.setValue(int(cfg.get("rc_port", 8080) or 8080))
        addr = QLineEdit(dlg)
        addr.setText(str(cfg.get("rc_address", "") or "127.0.0.1"))
        addr.setPlaceholderText("127.0.0.1 = この PC 内のみ / 0.0.0.0 = LAN 全体")
        pw = QLineEdit(dlg)
        pw.setText(str(cfg.get("rc_password", "") or ""))
        pw.setPlaceholderText("（空欄 = 認証なし）")
        form.addRow("ポート:", port)
        form.addRow("待ち受けアドレス:", addr)
        form.addRow("パスワード:", pw)
        layout.addLayout(form)
        layout.addWidget(QLabel(
            "待ち受けアドレスの既定は 127.0.0.1（この PC 内からのみ制御可）です。\n"
            "0.0.0.0 などに設定すると LAN 内の他端末からも制御できます。その場合は\n"
            "パスワードを設定し、LAN のセキュリティが確保されている前提でご利用ください。"
        ))

        status = QLabel(
            self._rc_status_text(bool(cfg.get("rc_enabled", False)), port.value(), addr.text()),
            dlg,
        )
        layout.addWidget(status)
        port.valueChanged.connect(
            lambda v: status.setText(self._rc_status_text(enable.isChecked(), v, addr.text())))
        enable.toggled.connect(
            lambda c: status.setText(self._rc_status_text(c, port.value(), addr.text())))
        addr.textChanged.connect(
            lambda t: status.setText(self._rc_status_text(enable.isChecked(), port.value(), t)))

        box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=dlg,
        )
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        layout.addWidget(box)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        address = addr.text().strip() or "127.0.0.1"
        if QHostAddress(address).isNull():
            QMessageBox.warning(
                self, "外部制御",
                f"待ち受けアドレスが不正です: {address}\n"
                "127.0.0.1 や 0.0.0.0 のような IP アドレスで入力してください。",
            )
            return
        cfg["rc_enabled"] = bool(enable.isChecked())
        cfg["rc_port"] = int(port.value())
        cfg["rc_address"] = address
        cfg["rc_password"] = pw.text()
        appconfig.save(cfg)
        self._apply_rc_config()
        self.statusBar().showMessage(
            self._rc_status_text(cfg["rc_enabled"], cfg["rc_port"], cfg["rc_address"]))

    def _rc_status_text(self, enabled: bool, port: int, address: str = "127.0.0.1") -> str:
        if not enabled:
            return "外部制御: 無効"
        addr = (address or "127.0.0.1").strip()
        if addr in ("0.0.0.0", "::", "*"):
            return (
                f"外部制御: ポート {port} で全インターフェース待受中"
                "（LAN 内の他端末からもアクセス可）"
            )
        return f"外部制御: http://{addr}:{port}/requests/status.json で待受中"

    def _apply_rc_config(self) -> None:
        if self._config.get("rc_enabled"):
            self._start_rc_server()
        else:
            self._stop_rc_server()

    def _start_rc_server(self) -> None:
        port = int(self._config.get("rc_port", 8080) or 8080)
        password = str(self._config.get("rc_password", "") or "")
        host = str(self._config.get("rc_address", "") or "127.0.0.1")
        self._rc_server.start(port, password, host)

    def _stop_rc_server(self) -> None:
        self._rc_server.stop()

    # --- VlcControlServer からの制御口（GUI スレッドで実行される） -----
    def _rc_play(self) -> None:
        if not self.slides:
            self.relayout()
        if self.player.position() == 0:
            self._reshuffle_photos()
        self._start_playback()

    def _rc_pause(self) -> None:
        self.player.pause()
        self._render_timer.stop()

    def _rc_stop(self) -> None:
        self.player.stop()
        self._render_timer.stop()
        self._push_time(0.0)

    def _rc_load(self, path: str) -> None:
        if not path:
            return
        self.set_audio(path, interactive=False)

    def _rc_current_path(self) -> str:
        if self._rc_path:
            return self._rc_path
        if self.project.audio is not None:
            return self.project.audio.path
        return ""

    def _rc_name(self) -> str:
        p = self._rc_current_path()
        return Path(p).name if p else ""

    def _rc_uri(self) -> str:
        p = self._rc_current_path()
        return _url(p).toString() if p else ""

    def _rc_status(self) -> dict:
        st = self.player.playbackState()
        if st == QMediaPlayer.PlaybackState.PlayingState:
            state = "playing"
        elif st == QMediaPlayer.PlaybackState.PausedState:
            state = "paused"
        else:
            state = "stopped"
        dur = self.player.duration()
        if dur is not None and dur >= 0:
            length = dur / 1000.0
        else:
            length = self.project.audio.duration if self.project.audio is not None else 0.0
        return {
            "apiversion": 4,
            "state": state,
            "currentplid": self._rc_plid,
            "time": self.player.position() / 1000.0,
            "length": float(length),
            "rate": float(self.player.playbackRate()),
            "is_paused": state == "paused",
            "volume": int(round(self.audio_out.volume() * 100)),
            "current_title": self._rc_name(),
            "title": 0,
        }

    def _rc_playlist(self) -> dict:
        uri = self._rc_uri()
        if self._rc_plid <= 0 or not uri:
            return {"id": 0, "name": "playlist", "type": "root", "items": []}
        return {
            "id": 0, "name": "playlist", "type": "root",
            "items": [{
                "id": self._rc_plid,
                "name": self._rc_name(),
                "type": "leaf",
                "current": "current",
                "uri": uri,
            }],
        }

    # ==================================================================
    # 既定設定の永続化（終了時に保存／起動時に反映）
    # ==================================================================
    def _apply_persisted_settings(self) -> None:
        """config.json に保存した既定設定を起動時の project.settings に反映する。

        反映対象: 単位ビート / 既定のトランジション / 遷移時間 / 描画FPS /
        再生のたびに画像順をランダム化 / 切り替えごとにトランジットをランダム化 /
        余白を画像の支配色にする。不正な値は現在の設定（既定）を維持する。
        """
        d = self._config.get("settings")
        if not isinstance(d, dict):
            return
        s = self.project.settings
        # 単位ビート: "auto" または 0.5/1/2/4/8/16（文字列の数値は int/float に正規化）
        unit = d.get("unit_beats", s.unit_beats)
        if isinstance(unit, str) and unit.lower() != "auto":
            try:
                val = float(unit)
                unit = int(val) if val.is_integer() else val
            except ValueError:
                unit = s.unit_beats
        if unit == "auto" or unit in UNIT_BEATS_CHOICES:
            s.unit_beats = unit
        # 既定のトランジション
        kind = str(d.get("transition", s.transition))
        if kind in TRANSITION_TYPES:
            s.transition = kind
        # 遷移時間(ms)
        try:
            s.transition_ms = max(0, min(3000, int(d.get("transition_ms", s.transition_ms))))
        except (TypeError, ValueError):
            pass
        # 描画FPS
        try:
            fps = int(d.get("render_fps", s.render_fps))
            if 1 <= fps <= 240:
                s.render_fps = fps
        except (TypeError, ValueError):
            pass
        # ランダム化トグル
        s.random_order = bool(d.get("random_order", s.random_order))
        s.random_transition = bool(d.get("random_transition", s.random_transition))
        # 余白を画像の支配色にする
        mode = str(d.get("background_mode", s.presentation.background_mode))
        s.presentation.background_mode = mode if mode in ("solid", "dominant") else "solid"
        # プレビューの背景モードにも反映（プレゼン窓は未生成なので起動時は不要）
        self.preview.set_background_mode(s.presentation.background_mode)

    def _apply_persisted_funscript_preset(self) -> None:
        """config.json に記憶した FunScript プリセット選択を既定ルールへ反映する。

        選択名が実在するプリセットに一致すれば、その 4項目（モード / しきい値 /
        ステップ / 循環順）を project.settings へ適用する。起動時（プロジェクト
        読込前）に呼ぶため、後から .rslide を読み込めばプロジェクト側のルールが優先される。
        """
        name = str(self._config.get("funscript_preset", ""))
        if not name:
            return
        presets = sanitize_presets(appconfig.load_funscript_presets())
        p = next((x for x in presets if x["name"] == name), None)
        if p is None:
            return
        s = self.project.settings
        s.funscript_mode = p["mode"]
        s.funscript_threshold = p["threshold"]
        s.funscript_step = p["step"]
        s.photo_order = p["order"]

    def _save_persisted_settings(self) -> None:
        """現在の既定設定を config.json に保存する（終了時に呼ぶ）。"""
        s = self.project.settings
        self._config["settings"] = {
            "unit_beats": s.unit_beats,
            "transition": s.transition,
            "transition_ms": int(s.transition_ms),
            "render_fps": int(s.render_fps),
            "random_order": bool(s.random_order),
            "random_transition": bool(s.random_transition),
            "background_mode": s.presentation.background_mode,
        }
        appconfig.save(self._config)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        if self._fullscreen_host is not None:
            self._fullscreen_host.close()  # 全画面ならプレビューを中央 splitter へ戻す
        self._save_persisted_settings()
        self._rc_server.stop()
        self._render_timer.stop()
        self.player.stop()
        if self._sleep_guard is not None:
            self._sleep_guard.stop()
            self._sleep_guard = None
        self.pool.waitForDone(2000)
        self.thumb_cache.close()
        super().closeEvent(event)


class _FullscreenPreviewHost(QWidget):
    """F11 でプレビューを全画面 ⇔ 通常表示へ戻すためのボーダーレス全画面ホスト窓。

    プレビュー本体を一時的にこの窓へ再親付けして全画面表示する。F11 / Esc、または
    窓自体を閉じた時に呼び出し元（MainWindow）のトグルを呼んで通常表示へ戻る。
    全画面中は MainWindow が隠れるためメニューのショートカットが発火しないので、
    Space（再生/一時停止）と Home（最初から再生）もここで橋渡しする。
    プレゼン窓と同様にマウスカーソルは隠す（BlankCursor）。
    """

    def __init__(
        self, on_toggle, on_close, on_play_pause, on_restart,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._on_close = on_close
        self._on_play_pause = on_play_pause
        self._on_restart = on_restart
        self.setWindowTitle("RhythmSlide 画像表示")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        # 全画面中はカーソルを隠す（プレビューは明示カーソルを持たないので継承する）
        self.setCursor(Qt.CursorShape.BlankCursor)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_F11, Qt.Key.Key_Escape):
            self._on_toggle()
        elif event.key() == Qt.Key.Key_Space:
            self._on_play_pause()
        elif event.key() == Qt.Key.Key_Home:
            self._on_restart()
        else:
            super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802
        # Alt+F4 などで窓が閉じられた時も通常表示へ戻す（_exit 側で再入防止済み）
        self._on_close()
        super().closeEvent(event)


def _url(path: str):
    from PySide6.QtCore import QUrl

    return QUrl.fromLocalFile(str(path))


class _RcController:
    """VlcControlServer が呼ぶ制御口。MainWindow の再生プリミティブへ橋渡しする。

    サーバは GUI スレッドで動くため、これらのメソッドも GUI スレッドで呼ばれる。
    """

    def __init__(self, win: "MainWindow") -> None:
        self.win = win

    def play(self) -> None:
        self.win._rc_play()

    def pause(self) -> None:
        self.win._rc_pause()

    def stop(self) -> None:
        self.win._rc_stop()

    def seek(self, seconds: float) -> None:
        self.win.seek(float(seconds))

    def set_rate(self, rate: float) -> None:
        self.win.player.setPlaybackRate(float(rate))

    def load(self, path: str) -> None:
        self.win._rc_load(path)

    def status(self) -> dict:
        return self.win._rc_status()

    def playlist(self) -> dict:
        return self.win._rc_playlist()
