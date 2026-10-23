# -*- mode: python ; coding: utf-8 -*-
"""RhythmSlide の PyInstaller onefile（単一 exe）ビルド仕様。

ビルド例:
    pyinstaller packaging/rhythmslide_onefile.spec

onedir 版（rhythmslide.spec）と同じ内容だが、EXE に binaries/zipfiles/datas を
直接含め COLLECT を使わないことで単一 exe に固める。
ffmpeg / ffprobe は bin/ へ同梱し、実行時は find_ffmpeg() が sys._MEIPASS/bin を参照する。
"""
import os
import shutil
from PySide6 import __path__ as pyside_path

block_cipher = None

ROOT = os.path.abspath(os.path.dirname(SPEC))
WSROOT = os.path.abspath(os.path.join(ROOT, ".."))
# エントリはパッケージ内の __main__.py（相対 import あり）を直接渡さない。
# PyInstaller はこれをトップレベルスクリプト扱いし相対 import が壊れるため、
# 正規パッケージを絶対 import する薄い起動子 packaging/entry.py を使う。
APP_ENTRY = os.path.join(ROOT, "entry.py")

# 同梱する ffmpeg / ffprobe を探す
ffmpeg_dir = os.environ.get("FFMPEG_DIR", ROOT)
binaries = []
for exe in ("ffmpeg.exe", "ffprobe.exe"):
    src = shutil.which(exe) or os.path.join(ffmpeg_dir, exe)
    if os.path.isfile(src):
        binaries.append((src, "bin"))
    else:
        print(f"[spec] 警告: {exe} が見つかりません（{src}）。音声デコードの互換性が低下します。")

a = Analysis(
    [APP_ENTRY],
    pathex=[ROOT, WSROOT],
    binaries=binaries,
    datas=[],
    hiddenimports=[
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "scipy", "librosa", "numba"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile: COLLECT を使わず EXE に binaries/zipfiles/datas を直接含める
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="rhythmslide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,          # GUI アプリ（コンソールなし）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
