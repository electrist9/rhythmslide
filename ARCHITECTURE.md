# ARCHITECTURE.md — RhythmSlide 設計・実装構成

> 変更前に本ファイルの「規約とはまりどころ」を必ず読むこと。層の依存方向や不変条件を壊すと、便携版・テスト・多言語が同時に壊れる。

## 1. 層構成と依存規則

```
rhythmslide/
├── app.py            エントリ（QApplication 生成 → 言語適用 → MainWindow → exec）
├── __main__.py       python -m rhythmslide 用（app.main を呼ぶだけ）
├── i18n.py           多言語（Qt tr 前提のヘルパー。core には依存しない）
├── core/             Qt 非依存の純ロジック層（PySide6 を import してはいけない）
├── platform/         OS 依存（スリープ抑制）
├── ui/               PySide6（Widgets / Multimedia）層
└── locale/           rhythmslide_en.ts/.qm, rhythmslide_zh.ts/.qm（コミット対象）
```

- **core は Qt-free**（プロジェクト不変条件）。`core/` に PySide6 を import しない。テストが Qt なしで走る理由そのもの。
- `ui/` は `core/` のみに依存。`core` → `ui` の逆依存は禁止。
- `i18n.py` はトップレベル（core でも ui でもない）。`fmt()` は ui 層から `from ..i18n import fmt` で使う。

## 2. モジュール一覧（行数は 2026-10 時点の**非空行**。総行数はこれより多い）

| モジュール | 行数 | 役割 |
|---|---:|---|
| `ui/mainwindow.py` | 1204 | 全 UI の中心。メニュー/ツールバー、再生制御（QMediaPlayer + 描画タイマ）、F11 全画面ホスト（`_FullscreenPreviewHost`）、RC 制御（`_RcController`）、言語メニュー、プリセット管理、設定永続化 |
| `core/models.py` | 345 | `Project/PhotoItem/Slide/Settings` dataclass + `to_dict/from_dict`。`PROJECT_VERSION=1`、`TRANSITION_TYPES`(8 種)、`UNIT_BEATS_CHOICES=("auto",0.5,1,2,4,8,16)`、`RENDER_FPS_CHOICES=(30,60,144)` |
| `core/beatmapper.py` | 320 | レイアウト生成本体。ビートグリッド→スライド割当（unit 半拍既定）、過不足の間引き/循環、循環順（順/往復）、ランダム順、固定間隔 1.75s フォールバック、FunScript 切替点の採用 |
| `ui/timelineview.py` | 261 | タイムライン描画・編集（移動/伸縮/ロック）、**再生中の自動横スクロール追従** |
| `core/analyzer.py` | 249 | **純 numpy 解析**: onset（スペクトルフラックス）→ 自己相関テンポ → ビートグリッド → 信頼度 → RMS エネルギー。librosa 不使用 |
| `core/audio.py` | 233 | 音声読み込み。`find_ffmpeg()`（解決順: 環境変数 `RHYTHMSLIDE_FFMPEG` → `sys._MEIPASS/bin/` → `sys._MEIPASS/` → `shutil.which`）、`_decode_ffmpeg`（**パイプでなく一時ファイル経由**）、mutagen でメタデータ、soundfile で復号。`AUDIO_EXTENSIONS`/`VIDEO_EXTENSIONS` |
| `ui/renderer.py` | 205 | `SlideRenderer`。QWidget の paintEvent で「時刻 → 絵」を描画。contain フィット、背景色/支配色塗り、8 トランジション、Ken Burns |
| `core/funscript.py` | 197 | `.funscript` 読込 + 切替ルール（mode: up/peak/cumulative、threshold、step、order: forward/pingpong）。Qt-free |
| `ui/rcserver.py` | 193 | `VlcControlServer(QObject)`。**GUI スレッド上の QTcpServer**。`start(port, password="", host="127.0.0.1")`。純関数 `build_response()`/`handle_request()` |
| `core/exif.py` | 166 | EXIF 撮影日時・回転 |
| `core/scanner.py` | 164 | 写真再帰スキャン（junction ループ検出、不能フォルダ skip）、EXIF 順 |
| `ui/materialpanel.py` | 157 | 素材パネル（`PhotoListModel` の DisplayRole/ToolTipRole は**ユーザーデータなので翻訳しない**） |
| `core/imagecache.py` | 145 | 実行時 QImage LRU キャッシュ。キー `(path, max_w, max_h, crop)` |
| `core/thumbnails.py` | 132 | サムネイル永続化。**SQLite**（`thumbcache/thumbnails.db`、JPEG BLOB、WAL + `synchronous=NORMAL` + `check_same_thread=False` + `threading.RLock`）。`get()/ensure()` は JPEG **bytes** を返す |
| `core/paths.py` | 106 | データ保存先解決（§6） |
| `ui/commands.py` | 98 | タイムライン編集の Undo/Redo コマンド |
| `ui/presentation.py` | 93 | プレゼン窓（F5。独立フルスクリーン、ディスプレイ選択） |
| `ui/workers.py` | 82 | `QRunnable`（スキャン/解析）。UI へは `QCoreApplication.translate("MainWindow", ...)` でメッセージ |
| `core/appconfig.py` | 63 | `config.json` 読み書き（壊れても落とさない）+ `funscript_presets.json` 分離保存（旧 config 内キーからの一度きり移行） |
| `i18n.py` | 58 | `SUPPORTED_LANGS=("ja","en","zh")`、`LANGUAGE_LABELS`、`resolve_language(config)`、`install_translator(app, language)`、`locale_dir()`、`fmt()` |
| `core/renderlogic.py` | 56 | **時刻 t の純関数** `render_state(slides, t) -> RenderState`（§4） |
| `core/projectstore.py` | 45 | `.rslide` 保存/読込（§7）。`try_load_project()` は `(Project|None, code|None)` を返す |
| `app.py` | 43 | エントリ（§3） |
| `platform/sleepguard.py` | 38 | スリープ抑制（プレゼン中） |
| `ui/preview.py` | 35 | `PreviewWidget`。`set_slides/set_time/set_background*` のみ。「時刻→絵」に専念 |

## 3. 起動シーケンス（`app.py`）

1. `_configure_logging()`
2. `QApplication` 生成 → `setApplicationName/Version(__version__)/OrganizationName`
3. `ensure_data_dir()`（データ保存先確定・旧データ移行・環境変数ピン留め）
4. **`install_translator(app, resolve_language(appconfig.load()))`** ← `QApplication` 後・`MainWindow()` 前に必須。`QTranslator` の参照は `i18n._installed` で保持
5. `MainWindow()` 生成（`config.json` から前回フォルダ・RC 設定・プリセット選択・settings 7 項目を復元）
6. argv の `.rslide` を読み込み → `app.exec()`

## 4. 同期心臓部（不変条件）

- **音声クロック = `QMediaPlayer.position()`**。再生・seek・プレゼン・RC はすべてこの位置を基準に同期する。
- 描画タイマ間隔 = `_render_interval_ms() = max(1, round(1000.0 / settings.render_fps))`（30/60/144）。毎 tick: 音声位置 → `PreviewWidget.set_time` + タイムライン + プレゼン窓へ。
- `core/renderlogic.render_state(slides, t)` は**時刻の純関数**。遷移は「現スライド末尾 `transition_seconds` の間、次と重なる」で `progress = (t-(cur.end-ts))/ts` を 0..1 にクランプ。GUI なしでユニットテスト可能。
- トランジション時間 `Slide.transition_seconds` は**スライド長の 40% 上限**でレイアウト時にクランプ済み。描画側は再計算しない。
- 画像フィットは **contain**。`SlideRenderer` は 1.3 倍サイズでデコードして Ken Burns 用余裕を持つ（`renderer.py` L131 近辺）。
- 余白: `background_mode = "color"`（`background_color`）または `"dominant"`（画像の支配色。量子化ヒストグラムで算出）。

## 5. レイアウト生成（`core/beatmapper.py`）

入力: 写真一覧 + 解析結果（ビートグリッド/信頼度）+ FunScript（任意）+ Settings。
- 切替点は **ビートグリッド上**（既定 unit=0.5 = 半拍）。`"auto"` は写真枚数とビート数から自動。
- **FunScript があれば切替点そのもの**を使用（ルール変換は funscript 側で mode/threshold/step/order を適用）。
- 音声なし・信頼度低 → 固定間隔 **1.75 秒/枚**。
- 写真過不足: 間引き or 循環。`photo_order`: `forward`（循環）/ `pingpong`（往復）。
- `random_order`（既定 OFF）: 再生のたびに順をシャッフル。
- `random_transition`（既定 OFF）: **切り替えごとに** `TRANSITION_TYPES` から抽選。
- 生成結果は `Slide(path, start, end, transition, transition_seconds, ken_burns)` のリスト。以降の描画・編集はこのリストのみを見る。

## 6. データ保存先（`core/paths.py`）

- `ENV_VAR="RHYTHMSLIDE_DATA_DIR"`（指定時はそこへ集約）。
- `DATA_SUBDIR="rhythmslide/data"`。`workspace_root()` は frozen 時 `Path(sys.executable).resolve().parent`（**`_MEIPASS` ではない**）、開発時 `Path(__file__).resolve().parents[2]`。
- 候補はワークスペース/cwd のみ。**プロファイル下（APPDATA 等）は候補にしない**（ユーザー指定のハード制約）。書込可否は `.write_test` で実測。
- `ensure_data_dir()` は未上書き時だけ `LEGACY_DATA_SUBDIR=".rhythmslide_data"` からの移行（同名ファイルは上書きしない）を行い、ENV_VAR にピン留めする。
- **便携 onedir での実体**: exe 隣 = `dist\rhythmslide\` なので、データは `dist\rhythmslide\rhythmslide\data\`（ネストに注意。zip 同梱シードもこの位置に置く）。

## 7. 永続化フォーマット

- **`.rslide`（`PROJECT_EXTENSION`）**: 保存は**素の JSON**（UTF-8, `ensure_ascii=False, indent=2`、一時ファイル→`replace()` のアトミック書換）。読込は magic `b"RSLD1"`（旧 zlib 圧縮）を判別して解凍、Otherwise JSON。**拡張子は不変**。`models.PROJECT_VERSION=1`。
- **`config.json`**（`core/appconfig.py`）: フラット dict。キー: `photo_dir` / `audio_dir` / `funscript_dir` / `funscript_search_dirs`(list) / `project_dir` / `rc_enabled` / `rc_port` / `rc_address`(既定 `"127.0.0.1"`) / `rc_password` / `funscript_preset`(選択名) / `language` / `settings`(ネスト dict: `unit_beats, transition, transition_ms, render_fps, random_order, random_transition, background_mode`)。読込失敗時は空 dict で継続。
- **`funscript_presets.json`**: プリセット list。要素スキーマは `{name, mode, threshold, step, order}` のみ（config.json とは**分離**が決定済み）。
- **`beatcache/*.json`**: 解析結果キャッシュ（音声ファイル単位）。**`thumbcache/thumbnails.db`**: SQLite（§2）。

## 8. 外部制御（`ui/rcserver.py`、VLC 互換）

- `VlcControlServer(QObject)` は **GUI スレッドで QTcpServer を listen**（`QMediaPlayer` を安全に触るため別スレッド化しない。プロトコル処理は純関数 `build_response()/handle_request()` に分離してテスト可能）。
- `start(port, password="", host="127.0.0.1")`。不正 host は日本語警告後 127.0.0.1 へフォールバック。既定無効・既定ポート 8080。
- 認証なし or HTTP Basic（`rc_password` 時）。
- 実装済み: `GET /commands/status.xml|playlist.xml`、`POST /commands?command=play|pause|stop|toggle|next|previous|seek+5s|seek-5s|seek+10s|seek-10s|random on|off`。`seek <秒>` は相対指定のみ。
- **プロトコル文字列（`state` の playing/paused/stopped、playlist の `"name":"playlist"`）は VLC 固定値なので翻訳しない。**

## 9. 多言語（方式 A 確定）

- 仕組み: 全 UI 文言は `self.tr("日本語")`。`pyside6-lupdate` → `rhythmslide/locale/rhythmslide_{en,zh}.ts`（128 文字列、翻訳済み・unfinished 0）→ `pyside6-lrelease` → `.qm`（コミット済み）。`app.py` で `QTranslator` を `QApplication` 生成後・`MainWindow()` 前に install。
- **反映は言語保存＋アプリ再起動**（ホットスワップ不可と決定済み）。言語メニューはツールメニュー。既定 ja、未設定は `QLocale.system()` から推定。
- **PySide6 の `tr()` は Python `str` を返す（`.arg()` は存在しない）**。プレースホルダは `i18n.fmt(self.tr("…%1…"), value)` で `%1,%2,…` を順に置換（mainwindow 18 箇所・materialpanel 2 箇所が `fmt()` 変換済み。`ui/` 配下に `.arg(` はゼロ）。
- `locale_dir()`: frozen 時 `sys._MEIPASS/locale`（spec の `datas` で `_internal/locale/` に同梱）、開発時 `rhythmslide/locale`。
- 翻訳してはいけないもの: ユーザーデータ（プリセット名、ファイル名、`PhotoListModel` 表示）、RC プロトコル定数、`core/audio.py` の `.ts`（これは**動画拡張子**で翻訳ファイルと無関係）。
- 文言変更後の手順: lupdate（**`.py` を明示列挙**。ディレクトリ指定だと 0 件）→ `.ts` 埋める → lrelease → `.qm` をコミット。

## 10. パッケージング（便携 ZIP の作り方）

- **エントリは `packaging/entry.py`**（`from rhythmslide.app import main`）。`__main__.py` は相対 import のため spec エントリにできない（entry.py に日本語コメントで理由記載）。
- `packaging/rhythmslide.spec`（onedir、便携版の本命）: `pathex=[ROOT,WSROOT]`、hiddenimports `PySide6.QtMultimedia/QtMultimediaWidgets`、`datas` に `locale/*.qm → locale`、ffmpeg/ffprobe を `shutil.which()`（または `FFMPEG_DIR`）で `_internal/bin/` へ、excludes `tkinter,matplotlib,scipy,librosa,numba`、`EXE(exclude_binaries=True)` + `COLLECT(name="rhythmslide")` → 出力 `dist/rhythmslide/`。
- **便携 ZIP**: onedir フォルダをそのまま zip 化（`ZipFile.CreateFromDirectory(..., includeBaseDirectory=$true)` → トップレベル `rhythmslide/`）。zip 化前に `dist/rhythmslide/rhythmslide/data/` へ**サニタイズ済み** `config.json` + `funscript_presets.json` を置く（個人パス・パスワードの混入禁止）。
- onedir トップレベルは `rhythmslide.exe` + `_internal/` のみ（`bin/` はトップレベルに**ない**。`_internal/bin/`）。
- `packaging/installer.iss`（Inno Setup）は存在するが**実行検証は未**（→ TODO）。

## 11. スレッドと安全規則

- GUI 更新は GUI スレッドのみ。スキャン/解析は `QRunnable`（`workers.py`）→ シグナルで GUI へ。
- SQLite は `check_same_thread=False` + `threading.RLock` で守る（`thumbnails.py`）。
- RC サーバは GUI スレッド（§8）。
- FFmpeg 復号はパイプでなく**一時ファイル**（Windows のパイプ stdio 問題回避）。

## 12. 規約とはまりどころ（変更前必読）

1. `core/` に PySide6 を import しない。
2. `tr()` は `str`。`.arg()` 禁止、`fmt()` を使う。
3. RC プロトコル定数・ユーザーデータは翻訳しない。
4. `mainwindow.py` は 1200 行超。編集後は行番号がずれるので**編集前に該当範囲を再読**すること。
5. `i18n.py` の `LANGUAGE_LABELS` は複数行 dict。
6. `appconfig.load()` は BOM を除去しない（BOM 付き JSON を書き込まない）。
7. モーダルダイアログはヘッドレス検証でブロックする（スタブ化する）。
8. `Slide` に `crop` は無い（`PhotoItem.crop: CropRect` 側）。`renderer._crop()` は現状 None 固定＝**クロップはモデル/キャッシュ側のみ実装、描画未接続**（→ TODO）。
9. バージョンは `rhythmslide/__init__.py` の `__version__` が単一定義元（`pyproject.toml` は現在ズレていて未同期）。
