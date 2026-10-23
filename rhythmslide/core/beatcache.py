"""リズム解析結果のキャッシュ（計画書 5.1 / M2）。

音声ファイルの「内容指纹」でキャッシュキーを作り、解析（数秒）を再利用で 0 秒にする。
指纹は size + mtime_ns + 先頭 1MB のハッシュ + アナライザ版数で構成する。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from .analyzer import AnalysisResult, AudioAnalyzer
from .models import AudioInfo, isoformat
from .paths import beat_cache_dir, ensure_dir

log = logging.getLogger(__name__)

#: 解析アルゴリズムを変えたらキャッシュを無効化するための版数
CACHE_VERSION = "rhythmslide-analyzer-1"
_HEAD_BYTES = 1 << 20  # 先頭 1MB


def cache_key(path: Path | str) -> str:
    """音声ファイルの指纹（sha1 16 進）。"""
    p = Path(path)
    h = hashlib.sha1()
    h.update(CACHE_VERSION.encode("utf-8"))
    try:
        st = p.stat()
        h.update(str(st.st_size).encode())
        h.update(str(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))).encode())
    except OSError:
        h.update(b"<nostat>")
    try:
        with p.open("rb") as fh:
            h.update(fh.read(_HEAD_BYTES))
    except OSError:
        h.update(b"<noread>")
    return h.hexdigest()


class BeatCache:
    """解析済み AudioInfo を JSON で保存・再利用する。"""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else beat_cache_dir()

    def _path_for(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def load(self, key: str) -> AudioInfo | None:
        file = self._path_for(key)
        if not file.exists():
            return None
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("ビートキャッシュ読み込み失敗 %s: %s", file, exc)
            return None
        info = AudioInfo.from_dict(data)
        if info is None or not info.beats:
            return None
        return info

    def save(self, info: AudioInfo) -> None:
        if not info.cache_key:
            return
        ensure_dir(self.directory)
        file = self._path_for(info.cache_key)
        try:
            file.write_text(
                json.dumps(info.to_dict(), ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("ビートキャッシュ保存失敗 %s: %s", file, exc)

    def analyze(self, path: Path | str, analyzer: AudioAnalyzer | None = None) -> AudioInfo:
        """キャッシュにあれば再利用、無ければ解析して保存する。"""
        p = Path(path)
        key = cache_key(p)
        cached = self.load(key)
        if cached is not None:
            cached.path = str(p)  # パスは変わっても指纹が同じなら再利用
            return cached

        analyzer = analyzer or AudioAnalyzer()
        result = analyzer.analyze_file(p)
        info = audio_info_from_result(p, result, key)
        self.save(info)
        return info


def audio_info_from_result(path: Path | str, result: AnalysisResult, key: str = "") -> AudioInfo:
    return AudioInfo(
        path=str(path),
        duration=result.duration,
        bpm=result.bpm,
        confidence=result.confidence,
        beats=list(result.beats),
        cache_key=key,
        analyzed_at=isoformat(datetime.now()),
        method=result.method,
        energy_times=list(result.energy_times),
        energy_values=list(result.energy_values),
    )
