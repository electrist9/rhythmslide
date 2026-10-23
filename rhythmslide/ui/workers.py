"""重い処理を UI スレッドから切り離すワーカ（計画書 N4 / F1 / F3）。

QThreadPool + QRunnable。スキャン→（後続で）EXIF 補完、音声解析、サムネイル生成を
バックグラウンドで走らせ、シグナルで UI へ結果を返す。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRunnable, Signal

from ..core.audio import probe_audio
from ..core.beatcache import BeatCache
from ..core.exif import read_taken_at
from ..core.models import AudioInfo, PhotoItem
from ..core.scanner import ScanResult, resorted, scan_photo_root
from ..core.thumbnails import ThumbnailCache

log = logging.getLogger(__name__)


class WorkerSignals(QObject):
    finished = Signal(object)   # 種別に応じた結果
    progress = Signal(int, int)  # 完了数, 総数
    error = Signal(str)
    step = Signal(str)          # 進捗メッセージ（日本語）


class _BaseWorker(QRunnable):
    def __init__(self) -> None:
        super().__init__()
        self.signals = WorkerSignals()
        self.setAutoDelete(True)

    def run(self) -> None:  # QRunnable 実装
        try:
            self._run()
        except Exception as exc:  # noqa: BLE001
            log.exception("ワーカ異常")
            self.signals.error.emit(str(exc))

    def _run(self) -> None:  # pragma: no cover
        raise NotImplementedError


class ScanWorker(_BaseWorker):
    """写真ルートを高速スキャン（EXIF は後続 EnrichWorker で補完）。"""

    def __init__(self, root: str, follow_links: bool = True) -> None:
        super().__init__()
        self.root = root
        self.follow_links = follow_links

    def _run(self) -> None:
        self.signals.step.emit("写真をスキャン中…")
        result: ScanResult = scan_photo_root(
            self.root, read_exif=False, follow_links=self.follow_links
        )
        self.signals.finished.emit(result)


class EnrichWorker(_BaseWorker):
    """スキャン済み写真の撮影日時を補完し、並び直し済み一覧を返す。"""

    def __init__(self, photos: list[PhotoItem]) -> None:
        super().__init__()
        self.photos = photos

    def _run(self) -> None:
        total = len(self.photos)
        done = 0
        for p in self.photos:
            if p.taken_at is None:
                dt = read_taken_at(p.path)
                if dt:
                    p.taken_at = dt
            done += 1
            if done % 200 == 0:
                self.signals.progress.emit(done, total)
        self.signals.finished.emit(resorted(self.photos))


class AnalysisWorker(_BaseWorker):
    """音声解析（キャッシュ利用）で AudioInfo を返す。"""

    def __init__(self, audio_path: str, cache: BeatCache | None = None) -> None:
        super().__init__()
        self.audio_path = audio_path
        self.cache = cache or BeatCache()

    def _run(self) -> None:
        self.signals.step.emit("リズムを解析中…")
        info: AudioInfo = self.cache.analyze(self.audio_path)
        self.signals.finished.emit(info)


class ThumbnailWorker(_BaseWorker):
    """指定写真のサムネイルを生成し、完了ごとに path を返す。"""

    def __init__(self, photos: list[PhotoItem], cache: ThumbnailCache) -> None:
        super().__init__()
        self.photos = photos
        self.cache = cache

    def _run(self) -> None:
        total = len(self.photos)
        for i, p in enumerate(self.photos):
            self.cache.ensure(p.path)
            self.signals.finished.emit(p.path)
            if i % 50 == 0:
                self.signals.progress.emit(i, total)
        self.signals.progress.emit(total, total)
