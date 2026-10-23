"""スリープ抑制（計画書 F11 / 10 章リスク）。

プレゼンテーション中は SetThreadExecutionState でディスプレイ/システムの
スリープを防ぐ。終了時に必ず解除する。非 Windows では何もしない。
"""
from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001
_ES_DISPLAY_REQUIRED = 0x00000002


class SleepGuard:
    """プレゼン中のスリープを抑止するコンテキストマネージャ。"""

    def __init__(self) -> None:
        self._active = False

    def __enter__(self) -> "SleepGuard":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        if self._active or sys.platform != "win32":
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(
                _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_DISPLAY_REQUIRED
            )
            self._active = True
        except Exception as exc:  # noqa: BLE001
            log.debug("スリープ抑制に失敗: %s", exc)

    def stop(self) -> None:
        if not self._active:
            return
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        except Exception:  # noqa: BLE001
            pass
        self._active = False
