# TODO.md — RhythmSlide 未了・技術的負債・拡張候補

> 2026-10-31 時点。**ユーザーからの未対応依頼はゼロ**（v1.0.0 リリース・便携 ZIP 配布・README まで完了）。
> 以下は「未検証」「負債」「未合意の候補」であり、着手前に必ず `DECISIONS.md` を参照すること。

## A. 未検証・確認事項（優先度順）

### A1. `pyproject.toml` の version が 0.1.0 のまま（1.0.0 にズレ）
- 単一定義元は `rhythmslide/__init__.py` の `__version__ = "1.0.0"`（D15 系）。`pyproject.toml` L7 は古い。
- 対応案: `0.1.0` → `1.0.0` に更新してコミット。または setuptools の動的バージョン（`dynamic = ["version"]`）で単一化。**未着手・未合意**。

### A2. Inno Setup インストーラ（`packaging/installer.iss`）は一度も実行検証していない
- 便携 ZIP が正式配布物。インストーラ版の要求は来ていないが、iss が現行 spec（locale datas・bin/ffmpeg・onedir）と整合したままか未確認。
- 検証する場合: `iscc packaging\installer.iss` → 生成 setup でインストール → 起動・言語・RC を確認。

### A3. `packaging/rhythmslide_onefile.spec` は最近の再ビルドで検証していない
- i18n `datas`（locale_datas）は追記済みだが、onefile 版のビルド・`sys._MEIPASS/locale` 解決・`_MEIPASS/bin` の ffmpeg 解決は便携版（onedir）側でしか実証済みでない。onefile 配布要求が出た時に最初にビルド検証すること。

### A4. pytest 全スイートを通常環境で実行していない
- 開発サンドボックスでは pytest-qt の tmp_path 作成が PermissionError になる（環境制約。コード不具合ではないと確認済み）。テストコード自体は存在し、主要ロジックはインライン検証済み。
- 通常端末で: `$env:QT_QPA_PLATFORM="offscreen"; .venv\Scripts\python.exe -m pytest -p no:cacheprovider`

### A5. GUI の実機マニュアル QA をこの環境では行っていない
- offscreen 検証とスモークのみ。フルスクリーン（F11）・プレゼン窓（F5）・マルチディスプレイ・RC の LAN 実機（MultiFunPlayer 実接続）・実写真での支配色塗りはユーザー環境での確認に依存。バグ報告があれば `ui/mainwindow.py` の該当ハンドラから当たること。

### A6. exe 未署名 → SmartScreen 警告
- 便携 ZIP の `rhythmslide.exe` は未署名（README に「詳細情報→実行」案内記載済み）。コード署名証明書は未所持。要求があれば導入。

### A7. `dist/` の残骸整理（gitignore 対象なので repo には無影響）
- 削除可: `dist/RhythmSlide-0.1.0-win64.zip`（旧版）、`dist/rhythmslide.exe`（onefile 実験の残骸、便携版と無関係）。
- 保持: `dist/RhythmSlide-1.0.0-win64.zip`（配布物）、`dist/rhythmslide/`（onedir 材料）。

## B. 実装の穴（意図的に未接続の部分）

### B1. クロップ（`CropRect`）が描画に未接続
- `PhotoItem.crop` と `ImageCache` の crop 引数は実装済みだが、`ui/renderer.py::_crop()` は現状 **None 固定**（コメント付き）。計画書の自動トリミング（計画書 F11。キー F11 とは無関係）はモデルのみ。
- 接続するなら: `Slide` に crop を移すか `PhotoItem` 参照を持たせ、`render_state`/`SlideRenderer` 経由で渡す。`.rslide` の JSON 形式変更要（`PROJECT_VERSION` 判断）。

### B2. `appconfig.load()` は BOM を除去しない
- 本アプリ自身の保存は BOM なしなので実害なし。外部編集・PowerShell `Set-Content -Encoding utf8` 由来の BOM 付き config は読込失敗→空 dict 扱い（警告ログのみ）。頑健化するなら `encoding="utf-8-sig"`。低優先。

## C. 拡張候補（**未合意。ユーザー依頼なしに着手しないこと**）

- cover フィットオプション（D5 で contain 既定に変更済み。cover 復活要求は来ていない）
- 複数音声のプレイリスト／曲間レイアウト
- BPM 推定精度の改善（D2 の純 numpy 実装の限界。低信頼度フォールバックで回避済み）
- MP4 書き出し（**D1 で明示的にスコープ外**。やるなら D1 置き換えの ADR 必須）
- GitHub Actions 等の CI（pytest + PyInstaller ビルド）

## D. 次セッションの環境セットアップ（最短手順）

1. ワークスペース: `F:\testws`（venv 既存: `.venv`、Python 3.15.0。git は `PortableGit\cmd\git.exe`）。
2. 依存が入っていれば再インストール不要。追加導入時は `requirements.txt` / `requirements-dev.txt` を venv へ。
3. 実行・テスト・ビルド・翻訳更新のコマンドは `PROJECT_STATE.md` §6 をコピーして使う（pwsh の exit code 偽失敗と offscreen 制約に注意、§5）。
4. コミット前に `git status`（PortableGit 経由）。`dist/`・`rhythmslide/data/`・`*.rslide` はコミットしない。
5. 便携 ZIP を再生成する場合は `README.md`「パッケージング」節 + `DECISIONS.md` D15/D16（**config.json のサニタイズ必須**）。

## E. 直近の完了物（文脈用・これ以上の作業なし）

- v1.0.0（tag `v1.0.0` @ `8f7c401`）、便携 ZIP `RhythmSlide-1.0.0-win64.zip`（2026-10-24 ビルド・中身検証済み）、README 便携版手順（`7f91631`）。
- 本 4 文書（`PROJECT_STATE.md` / `ARCHITECTURE.md` / `DECISIONS.md` / `TODO.md`）は 2026-10-31 作成。**git 管理済み**（docs ブランチ → `main` へ `--no-ff` マージ）。
