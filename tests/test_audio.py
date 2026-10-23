"""音声トラック検査と動画拡張子のテスト。"""
from __future__ import annotations

import subprocess

import pytest

from rhythmslide.core.audio import (
    AUDIO_EXTENSIONS,
    VIDEO_EXTENSIONS,
    find_ffmpeg,
    has_audio_stream,
)


def test_video_extensions_defined():
    assert {".mp4", ".mov", ".mkv", ".webm"} <= VIDEO_EXTENSIONS
    # 音声と動画の拡張子は重複しない
    assert not (set(AUDIO_EXTENSIONS) & set(VIDEO_EXTENSIONS))


def test_has_audio_stream_true_for_wav(click_wav):
    path, _bpm, _dur = click_wav
    assert has_audio_stream(path) is True


def test_has_audio_stream_false_for_video_without_audio(tmp_path):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg が無いためスキップ")
    out = tmp_path / "video_noaudio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-an", "-y", str(out),
        ],
        check=True,
    )
    assert has_audio_stream(out) is False


def test_has_audio_stream_true_for_video_with_audio(tmp_path):
    if find_ffmpeg() is None:
        pytest.skip("ffmpeg が無いためスキップ")
    out = tmp_path / "video_audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-shortest", "-y", str(out),
        ],
        check=True,
    )
    assert has_audio_stream(out) is True
