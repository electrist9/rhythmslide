# PROJECT_STATE.md — RhythmSlide プロジェクト現状

> 別セッションから初参加した開発者（LLM 含む）が、このファイルだけで開発継続を開始できることを目的とする。
> 最終更新: 2026-10-31（v1.0.0 リリース後、便携 ZIP 再ビルド・README 追記完了時点）

## 1. プロジェクト概要

- **RhythmSlide**: 音楽のリズム（BPM・ビート）に同期して写真スライドショーを **リアルタイム表示** する Windows デスクトップアプリ。
- **MP4 等の書き出しは行わない**（仕様上の重要方針。計画書 v1.3 に対しリアルタイム表示主体へスコープ変更済み）。
- 計画書: `music_slideshow_app_plan.md`（v1.3）。追加サブ計画: `rc_interface_plan.md`（VLC 互換外部制御）、`thumbnail_sqlite_plan.md`（サムネイル DB 化）。いずれも**実装済み**。
- 全 UI 文言・コードコメントは**日本語**（ユーザー指定のプロジェクト規約）。

## 2. リリース / Git 状態

| 項目 | 値 |
|---|---|
| バージョン | **1.0.0**（唯一の定義元: `rhythmslide/__init__.py` の `__version__`） |
| ブランチ | `main`（HEAD = `7f91631`「README にポータブル版（zip）の起動方法と zip 化手順を追記」） |
| タグ | `v1.0.0`（annotated、`8f7c401`「バージョンを 1.0.0 に更新」を指す） |
| リモート | `origin` = `https://github.com/electrist9/rhythmslide.git`（`main` は origin と同期済み） |
| 作業ツリー | クリーン（`dist/`・`rhythmslide/data/`・`*.rslide`・`.venv/`・`PortableGit/` は gitignore 済み）。本 4 文書（PROJECT_STATE/ARCHITECTURE/DECISIONS/TODO）も git 管理済み |

履歴（要約）: `d428740` 初回コミット（全ソース） → `71c6c82`/`8a1f935` `.rslide` 素の JSON 化 → `f86c9ae`/`f58a176` 多言語対応 → `8f7c401` v1.0.0 → `7f91631` README 便携版手順。機能ブランチはすべて `main` へマージ済み・削除済み。

**注意**: `pyproject.toml` の `version` は `0.1.0` のまま（`__init__.py` と不一致・未修正）。→ `TODO.md` 参照。

## 3. 実装済み機能（すべて `main` に統合済み・ユーザー確認済み）

- 写真フォルダの再帰スキャン + EXIF 撮影日時順（junction ループ検出、アクセス不能フォルダはスキップ）
- 音声/動画ファイル選択（動画は音声トラックのみ使用）。リズム解析（BPM・ビート・信頼度・エネルギー）と **JSON キャッシュ**（2 回目以降ほぼ即時）
- 自動レイアウト: 既定 **半拍（unit=0.5）** で切替。写真過不足は間引き/循環。低信頼度・音声なしは固定間隔 **1.75 秒/枚** にフォールバック
- 画像フィットは **contain**（アスペクト比維持）。余白は背景色、または「**余白を画像の支配色にする**」で支配色塗り
- トランジション 8 種（none/crossfade/wipe_left/wipe_right/slide_left/slide_right/fade/zoom）。**既定 none**。遷移時間はスライド長の 40% 上限
- Ken Burns 機能は存在するが**既定 OFF**（UI・描画コードは維持）
- 「再生のたびに画像順をランダム化」「**切り替えごとに**トランジットをランダム化」（既定 OFF）
- 描画 FPS 選択 **30/60/144**（既定 60）
- プレビュー再生（音声クロック同期）、**F11** で画像表示部をボーダーレス全画面⇔窓（全画面中も Space/Home 有効・カーソル非表示）、**F5** でプレゼン窓（独立フルスクリーン、外部ディスプレイ選択、スリープ抑制）
- タイムライン編集（移動・伸縮・トランジション・ロック・Undo/Redo）、再生中は**再生位置へ自動横スクロール追従**
- プロジェクト保存 `.rslide` = **素の JSON（UTF-8, indent=2）**。旧 zlib 圧縮（magic `RSLD1`）も読込互換
- **FunScript 対応**: `.funscript` 読込で切替点そのものを使用。切替ルール 3 モード（上昇のみ/局所極値/累積移動距離）+ 振幅しきい値 + 累積ステップ + 循環順（順/往復）。同名 `.funscript` 自動検出（隣接 → 検索フォルダ複数・再帰）。**名前付きプリセット**を `funscript_presets.json` に保存、選択は `config.json` に記憶され起動時適用
- **外部制御（VLC 互換 HTTP）**: 既定無効。ポート既定 8080、待ち受け既定 `127.0.0.1`（`0.0.0.0` で LAN 制御可）、パスワードで Basic 認証。MultiFunPlayer 対応
- **多言語**: 日本語（既定）/ English / 中文。Qt `tr()` + `.ts`/`.qm` + `QTranslator`。**反映は再起動**。未設定時は OS ロケールから推定
- 設定永続化: 編集メニュー 7 項目 + 前回フォルダ + RC 設定 + 言語 + プリセット選択を `config.json` に保存・起動時適用

## 4. 成果物（`dist/`、gitignore 対象）

| 成果物 | 状態 |
|---|---|
| `dist/RhythmSlide-1.0.0-win64.zip`（252,673,442 B ≒ 241 MB、271 エントリ） | **現行の便携 ZIP**。v1.0.0 + i18n + listen-address + JSON 化を反映。内部検証済み（トップレベル `rhythmslide/`、`rhythmslide.exe`、`_internal/bin/ffmpeg.exe`+`ffprobe.exe`、`_internal/locale/*.qm`、`rhythmslide/data/{config,funscript_presets}.json`）。同梱 `config.json` は**サニタイズ済み**（言語 ja・RC 無効・127.0.0.1） |
| `dist/rhythmslide/` | onedir 出力フォルダ（便携 ZIP の材料。2026-10-24 ビルド） |
| `dist/RhythmSlide-0.1.0-win64.zip` | **旧版（残置）**。削除可 |
| `dist/rhythmslide.exe`（約 251 MB） | **onefile 実験の残骸**。便携版には不要。削除可 |

便携版の使い方・zip 化手順は `README.md`「ポータブル版（zip）の起動方法」「パッケージング」節に記載済み。

## 5. 開発環境と**環境固有の落とし穴**（重要）

- 仮想環境: `F:\testws\.venv`（**Python 3.15.0**）。計画書は 3.12 + librosa 想定だったが、**librosa/numba が 3.15 に入らないため解析は純 numpy 再実装**（README「実装上の注意」参照）。この逸脱は仕様化済み。
- 依存: PySide6 / numpy / Pillow / mutagen / soundfile（`requirements.txt`）。テスト: pytest / pytest-qt（`requirements-dev.txt`）。PyInstaller **6.22.3**（`.venv\Scripts\pyinstaller.exe`）。
- **git が PATH にない**。同梱 PortableGit を使う:
  `& "F:\testws\PortableGit\cmd\git.exe" -C F:\testws <cmd> 2>&1 | Out-String`
  （git の stderr 出力で PowerShell が NativeCommandError 噪声を出すが無害）
- **pwsh でビルド/テストすると exit code 1 の偽失敗が出ることがある**: PyInstaller/git が stderr に出す INFO を `2>&1` で合成すると PowerShell が非ゼロ終了にする。ログ末尾（例: `Build complete!`）で成否を判定すること。
- **pytest はこの実行環境（サンドボックス）では tmp_path 作成で PermissionError になる**（環境制約でありコード不具合ではない）。TMP 上書きを繰り返さないこと。ロジック検証はワークスペース内一時ディレクトリ（`F:\testws\ptmp_*`）を使うインライン Python で行う。`tempfile.mkdtemp()` は既定で拒否される。
- ヘッドレス Qt: `$env:QT_QPA_PLATFORM="offscreen"`。**モーダルダイアログ（QMessageBox 等）はスクリプトを永久にブロックする**のでスタブ化すること。offscreen 時の `qt.multimedia.ffmpeg: Using Qt multimedia with FFmpeg version 9.0.1`、`QFontDatabase: Cannot find font directory` は無害警告。
- PowerShell `Set-Content -Encoding utf8` は **UTF-8 BOM** を書く → `appconfig.load()`（BOM 非除去）が JSONDecodeError。テストでの書き出しは `appconfig.save()` を使うこと。
- ffmpeg/ffprobe はシステム PATH（Gyan FFmpeg 9.0 full build）を spec が `shutil.which()` で拾って同梱する仕組み。`packaging/ffmpeg.exe` を置く必要も `FFMPEG_DIR` も不要（置くなら `FFMPEG_DIR` 環境変数で上書き可）。

## 6. 常用コマンド

```powershell
# 開発実行
F:\testws\.venv\Scripts\python.exe -m rhythmslide            # .rslide を引数可

# テスト（通常の端末で。サンドボックスでは §5 の制約に注意）
$env:QT_QPA_PLATFORM = "offscreen"
F:\testws\.venv\Scripts\python.exe -m pytest -p no:cacheprovider

# onedir ビルド（便携 ZIP の材料。約 13 分）
& "F:\testws\.venv\Scripts\pyinstaller.exe" --noconfirm "F:\testws\packaging\rhythmslide.spec"

# 便携 ZIP 化（トップレベルに rhythmslide/ を含める）
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory(
    "F:\testws\dist\rhythmslide", "F:\testws\dist\RhythmSlide-1.0.0-win64.zip",
    [System.IO.Compression.CompressionLevel]::Optimal, $true)

# 翻訳更新（UI 文言変更後）
& "F:\testws\.venv\Scripts\pyside6-lupdate.exe" <明示した .py の列挙> -ts rhythmslide/locale/rhythmslide_en.ts rhythmslide/locale/rhythmslide_zh.ts
& "F:\testws\.venv\Scripts\pyside6-lrelease.exe" rhythmslide/locale/rhythmslide_en.ts -qm rhythmslide/locale/rhythmslide_en.qm
```

（`pyside6-lupdate` にディレクトリを渡すと「Found 0 source text(s)」になる。**`.py` を明示列挙**すること。）

## 7. データ保存場所（規約: ユーザープロファイル下に書かない）

- 既定は**アプリ格納フォルダ直下 `rhythmslide/data/`** のみ（開発実行時はワークスペース直下、便携版では exe 隣）。`%APPDATA%`/`%LOCALAPPDATA%` には**絶対に書かない**（ユーザー指定）。
- 上書き: 環境変数 `RHYTHMSLIDE_DATA_DIR`。旧 `.rhythmslide_data` からは自動移行（同名ファイルは上書きしない）。
- 中身: `config.json` / `funscript_presets.json` / `beatcache/*.json` / `thumbcache/thumbnails.db`（SQLite）。
- **プライバシー注意**: 開発用 `rhythmslide/data/config.json` には個人の絶対パス・RC パスワードが入り得る。**便携 ZIP にそのまま同梱してはいけない**（配布時はサニタイズ済み既定値を同梱する。手順は README）。

## 8. 次のセッションでの開始手順（推奨）

1. `PROJECT_STATE.md`（本ファイル）→ `ARCHITECTURE.md`（設計・規約）→ `DECISIONS.md`（方針の理由）→ `TODO.md`（未了・負債）の順に読む。
2. `git log --oneline -5` と `git status` で状態確認（§5 の git コマンドを使う）。
3. コード変更時は `DECISIONS.md` の決定に反しないこと。反する場合は DECISIONS に追記して上書きする。
4. UI 文言を変えたら翻訳更新（§6）を忘れずに。`.qm` はコミット対象。
