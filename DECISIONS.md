# DECISIONS.md — 設計決定記録（ADR）

> 「なぜこの実装になっているか」の根拠。コードだけからは復元できない決定を保存する。
> 新しい決定で本ファイルの決定を覆す場合は、取り消し線ではなく**新 ADR を追加して「置き換え」**で明示する。
> 日付はセッション実施時期（2026-10）。ステータスはすべて「決定済み（main 統合済み）」だが、★は便携版・配布に直結する重要決定。

## D1. MP4 書き出しを行わず、リアルタイム表示のみ ★
- **背景**: 計画書 v1.3 は FFmpeg による MP4 レンダリング（M5）を含むスコープだった。
- **決定**: リアルタイム表示のみを実装。FFmpeg は**音声デコードと形式変換にのみ**使用（`core/audio.py`）。
- **理由**: ユーザーの用途がライブ再生（MultiFunPlayer 的な利用）に限定。書き出しはスコープ外と明示合意。
- **影響**: 書き出しパイプライン・エンコード設定は存在しない。将来追加する場合は本 ADR を置き換える。

## D2. librosa を使わず純 numpy でリズム解析
- **背景**: 計画書は Python 3.12 + librosa 想定。実環境は Python 3.15.0 で librosa/numba が入らない。
- **決定**: onset（スペクトルフラックス）→ 自己相関テンポ推定 → ビートグリッド → 信頼度 → RMS エネルギーを純 numpy で再実装（`core/analyzer.py`）。
- **影響**: BPM 推定精度は librosa 比で劣化しうる（低信頼度時は固定間隔フォールバックで吸収）。README「実装上の注意」に逸脱を記載済み。excludes で `librosa/numba/scipy/matplotlib` を明示排除。

## D3. QGraphicsView ではなく QWidget + paintEvent 自前レンダラ
- **背景**: 計画書は QGraphicsView 案。
- **決定**: `SlideRenderer`（`ui/renderer.py`）が paintEvent で直接描画。`PreviewWidget`/プレゼン窓が共用。
- **理由**: 毎フレーム「時刻→絵」の純描画にシーングラフは過剰。`render_state()` 純関数と組んでテスト可能。
- **影響**: トランジション・Ken Burns・contain・支配色塗りはすべてペインタ側実装。

## D4. 同期は音声クロック（`QMediaPlayer.position()`）のポーリング
- **決定**: 描画タイマ（`max(1, round(1000/fps))` ms）で音声位置をポーリングし、プレビュー・タイムライン・プレゼン・RC へ配る。FPS は 30/60/144 選択可（既定 60）。
- **理由**: 音声と映像の別クロック方式はズレる。単一の音声位置を正とする。±30ms 目標は達成。
- **影響**: Qt multimedia の ffmpegmediaplugin.dll により MP3/AAC/M4A/WAV/FLAC/OGG + 動画の音声トラックがネイティブ再生可。

## D5. 画像フィットは cover → **contain**（既定）
- **背景**: 当初 cover。ユーザーから「写真が切れる」変更要求。
- **決定**: contain（アスペクト比維持、余白は背景）。cover へのオプション復活は**未要求**。

## D6. Ken Burns・トランジションは**既定 OFF**（機能自体は維持）
- **決定**: 新規プロジェクトの既定は `transition="none"`、Ken Burns OFF。UI 選択項目と描画実装は残す。
- **理由**: ユーザーは「素の切り替え」を既定望む。既存プロジェクトの明示設定は尊重。

## D7. 既定の切替単位は半拍（unit=0.5）、固定間隔フォールバック 1.75 秒
- **背景**: 当初 unit=1・固定間隔 3.5 秒。ユーザーから「表示間隔を半分に」要求。
- **決定**: `UNIT_BEATS_CHOICES=("auto",0.5,1,2,4,8,16)` の既定 0.5。フォールバック 1.75 秒/枚。切替点は必ずビートグリッド上。

## D8. トランジション時間はスライド長の 40% 上限、`Slide.transition_seconds` にのみ保存
- **理由**: 描画側で秒数を再計算すると編集・FunScript・フォールバックで整合が壊れる。レイアウト生成時にクランプして保存し、`render_state()` は保存値だけを参照する。

## D9. データ保存先はアプリ格納フォルダ直下 `rhythmslide/data/` のみ ★
- **背景**: Qt 既定は `%APPDATA%`。ユーザーから「ワークスペース（便携版では exe 隣）以外に書くな」指示。さらに旧 `.rhythmslide_data` → `rhythmslide/data` へのリネーム要求。
- **決定**: `core/paths.py` が候補をワークスペース/cwd のみに限定（プロファイル下は候補外）。環境変数 `RHYTHMSLIDE_DATA_DIR` で上書き可。旧ディレクトリは自動移行（同名ファイルは上書きしない）。frozen 時は `sys.executable` 隣（**`_MEIPASS` ではない**）。
- **影響**: 便携版はフォルダコピーで設定・キャッシュごと移動できる。onedir ではデータが `rhythmslide/rhythmslide/data/` とネストする（zip 同梱シードの配置場所もここ）。

## D10. `.rslide` は拡張子維持、中身を素の JSON に（A案）
- **背景**: 当初は zlib 圧縮 + magic `RSLD1`。「JSON にしたい」要求に対し A案（拡張子維持・中身 JSON）/ B案（拡張子変更）から A案を選択。
- **決定**: 保存は素の JSON（UTF-8, indent=2、アトミック書換）。読込は magic 判別で旧圧縮も読む（**後方互換維持**）。`PROJECT_EXTENSION=".rslide"` 不変。
- **影響**: diff・手編集可能。テストはエラーコードの truthiness のみ検証（文言依存しない）。

## D11. FunScript 切替ルールは 4 軸 + 循環順、プリセットは別ファイル ★
- **決定**: mode（`up`/`peak`/`cumulative`）× threshold × step × order（`forward`/`pingpong`）。プリセットは `{name,mode,threshold,step,order}` の dict list を **`funscript_presets.json`** に保存（config.json から分離。旧 config 内キーからは一度きり移行）。プリセット**選択名**は `config.json.funscript_preset` に記憶し起動時適用。
- **背景**: 「プリセットを config.json と分けたい」「選択を再起動後も維持」の順次要求。プリセット名はユーザーデータなので翻訳しない。

## D12. 外部制御は VLC 互換 HTTP、既定 `127.0.0.1`・既定無効 ★
- **背景**: MultiFunPlayer 連携需要。当初は localhost 固定だったが「LAN 制御したい」要求で待ち受けアドレスを GUI 化（既定は `127.0.0.1` のまま）。
- **決定**: GUI スレッドで `QTcpServer`（`QMediaPlayer` 安全アクセスのため）。認証なし or Basic。プロトコル定数は翻訳対象外。
- **影響**: `0.0.0.0` 指定時は LAN 全体に開く（README に注意記載）。

## D13. F11 全画面は `FullScreen` フラグを使わない
- **決定**: `setWindowFlags(FramelessWindowHint)` + `showFullScreen()`。全画面中は `BlankCursor` でカーソル非表示、**Space（再生/停止）と Home（最初から再生）を有効維持**。
- **理由**: `Qt.WindowType.FullScreen` は WM と組み合わせるとキーイベント・復帰が不安定。Frameless 方式で確実。

## D14. 多言語は Qt `tr()` + `.ts`/`.qm` + `QTranslator`、反映は再起動（方式 A）★
- **背景**: 方式 A（Qt 標準）と方式 B（自前辞書）の選択肢から A を選択。ホットスワップは方式 B でないと難しいが、**再起動適用で合意**。
- **決定**: 原文日本語で `self.tr()`、`pyside6-lupdate/lrelease` で en/zh `.qm`（コミット済み、128 文字列）。既定 ja、未設定は `QLocale.system()` 推定。`QTranslator` は `QApplication` 後・`MainWindow()` 前に install し、モジュールレベル `_installed` で参照保持。
- **帰結（はまりどころ）**: PySide6 の `tr()` は `str` を返し **`.arg()` が存在しない** → `i18n.fmt()`（`%1,%2,…` 順置換）へ全 20 箇所を書き換え済み。`.arg()` の再導入禁止。

## D15. 便携配布は onedir + ZIP（onefile ではない）★
- **背景**: 「Potable ZIP 版を build」要求（以前の「ZIP 据え置き」方針は失効）。
- **決定**: `packaging/rhythmslide.spec`（onedir）をビルドし、フォルダごと zip 化（トップレベルに `rhythmslide/`、`ZipFile.CreateFromDirectory(..., $true)`）。onefile は起動毎の `%TEMP%` 解凍があるため不採用。
- **影響**: exe 隣にデータ保存（D9 と整合）。zip には ffmpeg/ffprobe・`.qm`・シード設定が同梱される。

## D16. 便携 ZIP 同梱の `config.json` はサニタイズ済み既定値 ★
- **背景**: 開発用 config.json に個人の絶対パス（写真・FunScript フォルダ）と RC パスワードが入っていた。
- **決定**: 配布には `{language:"ja", rc_enabled:false, rc_port:8080, rc_address:"127.0.0.1", rc_password:""}` のみ同梱。個人パス・プリセット選択・settings 上書きは入れない（アプリ組み込み既定に任せる）。`funscript_presets.json`（8 プリセット、パスフリー）はそのまま同梱。
- **理由**: プライバシー保護と初回起動体験。**再ビルド時も必ずサニタイズすること**（README に手順記載）。

## D17. Git 運用
- **決定**: ブランチは `main`（当初 `master` からリネーム）。機能は feature ブランチ → `--no-ff` マージ → ブランチ削除。コミットメッセージは日本語。タグは annotated（`v1.0.0`）。gitignore: `dist/`、`rhythmslide/data/`、`*.rslide`、`.venv/`、`PortableGit/`、一時ディレクトリ類。リモート `origin`（GitHub）へ push 済み。
- **環境制約**: git は PATH に無く `F:\testws\PortableGit\cmd\git.exe` を直接呼ぶ。

## D18. 画像切り替えは「再生のたびランダム順」と「切り替えごとランダム トランジット」の 2 機能に分離
- **背景**: 「毎回ランダム」要求が順とトランジットの両方を指していたため、`random_order` と `random_transition`（**スライドごとに抽選**）を別設定にした。両既定 OFF、config.json に永続化。

## D19. タイムラインは再生中に再生位置へ自動横スクロール追従
- **決定**: 再生中は再生位置が視界から外れた時のみスクロール（編集操作時は割り込まない）。

## D20. サムネイルキャッシュは SQLite（JPEG BLOB）
- **背景**: `thumbnail_sqlite_plan.md` による計画 → 実装。ファイル 1 枚 1 ファイル方式から DB 化。
- **決定**: WAL + `synchronous=NORMAL` + `check_same_thread=False` + `RLock`。`get()/ensure()` は JPEG bytes を返し、QPixmap 化は呼び出し側。
