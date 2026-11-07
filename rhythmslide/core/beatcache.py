"""リズム解析結果のキャッシュ（計画書 5.1 / M2）。

音声ファイルの「内容指纹」でキャッシュキーを作り、解析（数秒）を再利用で 0 秒にする。
指纹は size + mtime_ns + 先頭 1MB のハッシュ + キャッシュ版数
+ ビートプロバイダの id・版数・パラメータで構成する。
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .analyzer import AnalysisResult
from .beatproviders import DEFAULT_PROVIDER_ID, analyze_audio, get_beat_provider
from .models import AudioInfo, isoformat
from .paths import beat_cache_dir, ensure_dir

log = logging.getLogger(__name__)

#: 解析アルゴリズムを変えたらキャッシュを無効化するための版数
CACHE_VERSION = "rhythmslide-analyzer-2"
_HEAD_BYTES = 1 << 20  # 先頭 1MB


def cache_key(
    path: Path | str,
    provider_id: str = DEFAULT_PROVIDER_ID,
    provider_version: str = "",
    params: Optional[dict[str, Any]] = None,
) -> str:
    """音声ファイル + 解析条件の指纹（sha1 16 進）。

    同じ音声でもプロバイダ（id / 版数 / パラメータ）が変われば別キーになる。
    """
    p = Path(path)
    h = hashlib.sha1()
    h.update(CACHE_VERSION.encode("utf-8"))
    h.update(f"|{provider_id}|{provider_version}|".encode("utf-8"))
    try:
        h.update(
            json.dumps(
                params if isinstance(params, dict) else {},
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode("utf-8")
        )
    except (TypeError, ValueError):
        h.update(b"<badparams>")
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

    def analyze(
        self,
        path: Path | str,
        provider_id: str = DEFAULT_PROVIDER_ID,
        params: Optional[dict[str, Any]] = None,
        force: bool = False,
    ) -> AudioInfo:
        """キャッシュにあれば再利用、無ければ解析して保存する。

        ``force=True`` はキャッシュを読み飛ばして再解析する（ビート検出の
        切り替え時などに使用する）。
        FunScript のファイル内容はキャッシュの指紋に入らないため、更新時は force が必要。
        """
        p = Path(path)
        pid = str(provider_id or DEFAULT_PROVIDER_ID)
        provider = get_beat_provider(pid)
        if provider is None:
            # analyze_audio と同じ方針で既定プロバイダへフォールバック（キーも一致させる）
            log.warning("不明なビートプロバイダ '%s': 標準 '%s' を使用", pid, DEFAULT_PROVIDER_ID)
            pid = DEFAULT_PROVIDER_ID
            provider = get_beat_provider(pid)
        if provider is None:  # 理論上あり得ない（acf は常時登録）が防御
            raise RuntimeError("beat provider registry is empty")
        resolved = provider.sanitize_params(params if isinstance(params, dict) else {})

        key = cache_key(p, pid, provider.version, resolved)
        if not force:
            cached = self.load(key)
            if cached is not None:
                cached.path = str(p)  # パスは変わっても指紋が同じなら再利用
                return cached

        result = analyze_audio(p, pid, resolved)
        actual_pid = str(result.provider or pid)
        if actual_pid != pid:
            # ビートは別プロバイダ（標準 ACF）が生成したフォールバック結果。
            # 同じキーでキャッシュすると、後で FunScript ファイルが増えても
            # 古い結果がヒットしてしまうため保存しない（毎回作り直す）。
            return audio_info_from_result(
                p, result, key, provider_id=actual_pid, provider_params={}
            )
        info = audio_info_from_result(p, result, key, provider_id=pid, provider_params=resolved)
        self.save(info)
        return info


def audio_info_from_result(
    path: Path | str,
    result: AnalysisResult,
    key: str = "",
    provider_id: str = DEFAULT_PROVIDER_ID,
    provider_params: Optional[dict[str, Any]] = None,
) -> AudioInfo:
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
        provider=str(provider_id or DEFAULT_PROVIDER_ID),
        provider_params=dict(provider_params or {}),
    )
