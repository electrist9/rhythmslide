# -*- mode: python ; coding: utf-8 -*-
"""RhythmSlide の PyInstaller onedir ビルド仕様。

ビルド例:
    pyinstaller packaging/rhythmslide.spec

ffmpeg / ffprobe は bin/ へ同梱する。実行時は
rhythmslide.core.audio.find_ffmpeg() が sys._MEIPASS/bin を参照する。
ffmpeg.exe / ffprobe.exe を packaging/ と同じ階層（または FFMPEG_DIR 環境変数で
指定したフォルダ）に置いてからビルドすること。
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

# 翻訳（.qm）を _MEIPASS/locale/ へ同梱する。
# 実行時は rhythmslide.i18n.locale_dir() が sys._MEIPASS/locale を参照する。
LOCALE_DIR = os.path.join(WSROOT, "rhythmslide", "locale")
locale_datas = [(os.path.join(LOCALE_DIR, "*.qm"), "locale")]

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
    datas=locale_datas,
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

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="rhythmslide",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # GUI アプリ（コンソールなし）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="rhythmslide",
)
