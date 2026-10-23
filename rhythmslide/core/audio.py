"""音声デコードとメタデータ（計画書 F1 / 3.2）。

解析用にモノラル float32 へ変換する。経路は 2 つ:

1. ``soundfile``（libsndfile 同梱）: WAV / FLAC / OGG / MP3 など
2. FFmpeg（同梱 or システム）: QMediaPlayer / libsndfile が扱えない形式（M4A/AAC など）

FFmpeg はパイプではなく一時ファイルへ書き出す（サンドボックス環境でも安全なため）。
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: 解析サンプルレート（計画書 5.1）
ANALYSIS_SR = 22050

AUDIO_EXTENSIONS: frozenset[str] = frozenset({".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".wma"})

#: 音声トラックを含むことのある動画拡張子（選択時は音声トラックのみ使用）
VIDEO_EXTENSIONS: frozenset[str] = frozenset(
    {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".flv", ".ts", ".mts"}
)

_FFMPEG_NAMES = ("ffmpeg.exe", "ffmpeg")


@dataclass
class AudioProbe:
    path: str
    duration: float = 0.0
    title: str = ""
    artist: str = ""
    album: str = ""


def find_ffmpeg() -> str | None:
    """同梱 FFmpeg（PyInstaller 同梱 → 環境変数 → PATH）を探す。"""
    env = os.environ.get("RHYTHMSLIDE_FFMPEG")
    if env and Path(env).exists():
        return str(env)
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        for name in _FFMPEG_NAMES:
            cand = Path(bundled) / "bin" / name
            if cand.exists():
                return str(cand)
        for name in _FFMPEG_NAMES:
            cand = Path(bundled) / name
            if cand.exists():
                return str(cand)
    found = shutil.which("ffmpeg")
    return found


def probe_audio(path: Path | str) -> AudioProbe:
    """長さ・タグを取得（mutagen → soundfile → ffprobe の順）。"""
    p = Path(path)
    probe = AudioProbe(path=str(p))
    try:
        import mutagen

        tags = mutagen.File(str(p))
        if tags is not None:
            info = getattr(tags, "info", None)
            if info is not None and getattr(info, "length", None):
                probe.duration = float(info.length)
            t = getattr(tags, "tags", None) or {}

            def first(key: str) -> str:
                try:
                    value = t.get(key)
                except Exception:
                    return ""
                if isinstance(value, (list, tuple)) and value:
                    return str(value[0])
                return str(value) if value else ""

            probe.title = first("TIT2") or first("title")
            probe.artist = first("TPE1") or first("artist")
            probe.album = first("TALB") or first("album")
    except Exception as exc:  # mutagen が読めない形式もある
        log.debug("mutagen 失敗 %s: %s", p, exc)

    if not probe.duration:
        probe.duration = _duration_soundfile(p)
    if not probe.duration:
        probe.duration = _duration_ffprobe(p)
    return probe


def _duration_soundfile(p: Path) -> float:
    try:
        import soundfile as sf

        info = sf.info(str(p))
        if info.samplerate:
            return float(info.frames) / float(info.samplerate)
    except Exception:
        return 0.0
    return 0.0


def _duration_ffprobe(p: Path) -> float:
    ffprobe = _find_ffprobe()
    if not ffprobe:
        return 0.0
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(p),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_no_window_flags(),
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _find_ffprobe() -> str | None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    probe = Path(ffmpeg).with_name("ffprobe" + Path(ffmpeg).suffix)
    if probe.exists():
        return str(probe)
    return shutil.which("ffprobe")


def has_audio_stream(path: Path | str) -> bool:
    """音声トラックの有無を ffprobe で確認する。

    ffprobe が無い／実行に失敗した場合は誤検知を避けるため True を返す
    （実際の解析・再生側で失敗すればその時点でエラーになる）。
    """
    ffprobe = _find_ffprobe()
    if not ffprobe:
        return True
    try:
        out = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_no_window_flags(),
        )
    except Exception as exc:  # ffprobe 実行自体の失敗
        log.debug("ffprobe 音声検査失敗 %s: %s", path, exc)
        return True
    # 音声ストリームが無ければ stdout は空になる
    return bool(out.stdout.strip())


def _no_window_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


def decode_mono(path: Path | str, sr: int = ANALYSIS_SR) -> tuple[bytes, int]:
    """音声ファイルを解析用モノラル PCM float32 に変換して (bytes, sr) を返す。"""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))

    pcm = _decode_soundfile(p, sr)
    if pcm is not None:
        return pcm

    pcm = _decode_ffmpeg(p, sr)
    if pcm is not None:
        return pcm

    raise RuntimeError(f"音声をデコードできませんでした: {p}")


def _decode_soundfile(p: Path, sr: int) -> tuple[bytes, int] | None:
    try:
        import numpy as np
        import soundfile as sf

        data, native_sr = sf.read(str(p), always_2d=False, dtype="float32")
        if data.ndim > 1:
            data = data.mean(axis=1)
        if int(native_sr) != int(sr):
            data = _resample_linear(np.asarray(data, dtype="float32"), int(native_sr), int(sr))
        return np.ascontiguousarray(data, dtype="<f4").tobytes(), int(sr)
    except Exception as exc:
        log.debug("soundfile デコード失敗 %s: %s", p, exc)
        return None


def _decode_ffmpeg(p: Path, sr: int) -> tuple[bytes, int] | None:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".raw")
        os.close(fd)
        cmd = [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(p),
            "-ac",
            "1",
            "-ar",
            str(int(sr)),
            "-f",
            "f32le",
            "-y",
            tmp,
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=True,
            creationflags=_no_window_flags(),
        )
        with open(tmp, "rb") as fh:
            data = fh.read()
        if not data:
            return None
        return data, int(sr)
    except Exception as exc:
        log.debug("ffmpeg デコード失敗 %s: %s", p, exc)
        return None
    finally:
        if tmp:
            try:
                os.remove(tmp)
            except OSError:
                pass


def _resample_linear(data, src_sr: int, dst_sr: int):
    import numpy as np

    if src_sr == dst_sr or data.size == 0:
        return data
    n_out = int(round(data.size * dst_sr / src_sr))
    pos = np.linspace(0.0, data.size - 1, n_out, dtype=np.float64)
    i0 = np.floor(pos).astype(np.int64)
    i1 = np.minimum(i0 + 1, data.size - 1)
    frac = (pos - i0).astype(np.float32)
    return (data[i0] * (1.0 - frac) + data[i1] * frac).astype(np.float32)


def pcm_to_array(pcm: bytes, sr: int):
    import numpy as np

    return np.frombuffer(pcm, dtype="<f4")
