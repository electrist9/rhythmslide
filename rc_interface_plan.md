# 外部制御（VLC 互換 HTTP インターフェース）追加の調査・変更計画

対象アプリ: RhythmSlide（`rhythmslide`）
目的: 外部アプリ（想定: MultiFunPlayer）から RhythmSlide を制御できるインターフェースを付ける。
MultiFunPlayer の `VlcMediaSource` が使う VLC 互換の呼び出しで動作すればよい（VLC の全機能は不要）。

参照: MultiFunPlayer `VlcMediaSource.cs`
<https://github.com/Yoooi0/MultiFunPlayer/blob/master/Source/MultiFunPlayer/MediaSource/ViewModels/VlcMediaSource.cs>
（調査用に `F:\testws\.tmp\VlcMediaSource.cs` へ取得済み。以下の行番号はこの実物に基づく）

> 本計画は、ソース実物で裏取りした内容に更新済み。初版で推定していたコマンド名・単位・ファイルロード要否を修正している。

---

## 1. 結論（方式）

MultiFunPlayer の `VlcMediaSource` は **VLC の HTTP インターフェース（mod_http）** を叩いているだけです。
したがって RhythmSlide 側に **「VLC の status.json / playlist.json / コマンド API の互換サブセットを応答する小さな HTTP サーバ」** を内蔵すれば、VLC 本体なしで MultiFunPlayer から制御できます。

実装は **PySide6 の `QTcpServer` を GUI スレッドで動かす**方式を推奨します。`readyRead` でリクエストを同期的に解析し、そのまま `QMediaPlayer` を操作できるため、Qt のスレッド制約（`QMediaPlayer` は GUI スレッド専用）に自然に適合し、スレッド間シグナル中継が不要で安全です。

---

## 2. MultiFunPlayer が実際に使う呼び出し（ソースで確定）

接続・認証（[:41-67](F:/testws/.tmp/VlcMediaSource.cs#L41-L67)）:

- **接続**: `http://127.0.0.1:8080`（既定）。`GET /requests/status.json` を叩き、JSON の **`apiversion` が 3 または 4** であることを要求（それ以外だと接続失敗扱い）。
- **認証**: HTTP **Basic 認証**、ユーザ名は空で `:パスワード`（`Authorization: Basic base64(":"+Password)`）。

読み取り `ReadAsync`（[:103-208](F:/testws/.tmp/VlcMediaSource.cs#L103-L208)）:

- 200ms 周期で `GET /requests/status.json` をポーリング。
- **`currentplid`（int）が必須**。無い、または apiversion 4 で `state == "stopped"` なら状態リセット（位置などを読まない）。
- `state`(文字列: playing/paused/stopped) / `length`(**秒**) / `rate`(倍率) / `time`(**秒**) を読む。
- **`currentplid` が前回と変わると `GET /requests/playlist.json` を取得**し、`type=='leaf' && current=='current'` のノードの **`uri`** を読んで「いま読み込まれているファイル」を把握する（[:142-167](F:/testws/.tmp/VlcMediaSource.cs#L142-L167)）。

制御 `WriteAsync`（[:224-252](F:/testws/.tmp/VlcMediaSource.cs#L224-L252)）— 送信コマンドは **4 種類のみ**:

| MultiFunPlayer 内部メッセージ | 送信する VLC コマンド（`?command=`） | 行 |
|---|---|---|
| play/pause | `pl_forceresume` / `pl_forcepause` | [:237](F:/testws/.tmp/VlcMediaSource.cs#L237) |
| seek | `seek&val=<秒(int)>` | [:238](F:/testws/.tmp/VlcMediaSource.cs#L238) |
| change path（**ファイルロード**） | `in_play&input=<URLエンコードしたパス>` / 空なら `pl_stop` | [:239](F:/testws/.tmp/VlcMediaSource.cs#L239) |
| change speed | `change_rate&val=<F4 float>` | [:240](F:/testws/.tmp/VlcMediaSource.cs#L240) |

- **ファイルロードは使う**（`in_play&input`）。したがって RhythmSlide 側で実装必須。
- `volume` / `pl_next` / `pl_previous` / `add&uri` / `intf-change-name` は**使わない**（default で無視）。

---

## 3. 対応表（VLC コマンド → RhythmSlide の処理）

既存の再生プリミティブ（`ui/mainwindow.py` の `toggle_play` / `seek` / `_start_playback` / `set_audio`）に写像できます。

| VLC コマンド | RhythmSlide 側の処理 | 要否 |
|---|---|---|
| `pl_forceresume` | `_start_playback()`（`player.play()` + 描画タイマ開始） | 必須 |
| `pl_forcepause` | `player.pause()` + `_render_timer.stop()` | 必須 |
| `pl_stop` | `player.stop()`（位置 0 + 停止） | 必須 |
| `seek&val=<秒>` | `seek(秒)`（**絶対秒**。`setPosition(int(秒*1000))`） | 必須 |
| `change_rate&val=<float>` | `player.setPlaybackRate(f)` | 必須（Speed 同期） |
| `in_play&input=<path>` | `set_audio(<path>)`（URLデコード→音声/動画読込・解析・再生） | 必須 |
| その他（volume 等） | 200 を返すだけ（無視） | 不要 |

**`GET /requests/status.json` の応答**（要求時に `player` から生成。単位は**秒**）:

```json
{
  "apiversion": 4,
  "state": "playing",
  "currentplid": 1,
  "time": 12.5,
  "length": 180.0,
  "rate": 1.0,
  "is_paused": false,
  "volume": 80,
  "current_title": "<音声名>",
  "title": 0
}
```

- `apiversion` は **4** を返す（MultiFunPlayer が 3/4 以外を弾く）。
- **`currentplid` は必須**。読み込み中の項目 ID（例: 1）。**ファイルが変わったら値を更新**すると、MultiFunPlayer が `playlist.json` を再取得して新しいパスを知る。
- `time`/`length` は**秒**（`player.position()/1000`, `player.duration()/1000`。duration が -1 なら `project.audio.duration` で補う）。
- 未読込時は `state: "stopped"` を返す（MultiFunPlayer は状態リセットする）。

**`GET /requests/playlist.json` の応答**（`currentplid` 変化時に取得される）:

```json
{
  "id": 0, "name": "playlist", "type": "root",
  "items": [
    { "id": 1, "name": "<音声名>", "type": "leaf", "current": "current",
      "uri": "file:///F:/path/to/song.mp3" }
  ]
}
```

- MultiFunPlayer は `type=='leaf' && current=='current'` のノードの `uri` を読む。`file://` ならローカルパスとして扱う（[:162-167](F:/testws/.tmp/VlcMediaSource.cs#L162-L167)）。

---

## 4. 変更箇所（ファイル単位）

- **新規** `rhythmslide/app/rcserver.py` — `QTcpServer` ベースの VLC 互換 HTTP サーバ。HTTP GET 解析・URL query 解析・Basic 認証チェック・コマンド分发（`pl_forceresume`/`pl_forcepause`/`pl_stop`/`seek`/`change_rate`/`in_play`）・`status.json`/`playlist.json` 生成。
- **新規** `tests/test_rcserver.py` — HTTP パース、status/playlist JSON の形、コマンド→制御呼び出しの検証（`player` はスタブで）。
- **変更** `ui/mainwindow.py` — サーバ起動/停止、設定メニュー（有効化・ポート・パスワード）、コマンド実行ヘルパ（GUI スレッドで `player`/`audio_out`/`set_audio` を操作）、`currentplid` の採番、`config.json` へ `rc_enabled/rc_port/rc_password` 保存。
- **変更** `core/appconfig.py` — 既定キー `rc_*`（既存の folder memory と同じ仕組みで拡張）。
- **変更** `README.md` — 「外部制御（VLC 互換）」の節。
- **依存**: `PySide6.QtNetwork`（**同梱確認済み・追加 pip 不要**）。

---

## 5. セキュリティ / バインド

- 待ち受けアドレスは設定可能。既定は **loopback（127.0.0.1）** で、この PC 内のみ接続可。
- LAN 内の他端末から制御する場合は `0.0.0.0`（全インターフェース）や LAN IP を指定するオプトイン。その際はパスワード設定と LAN セキュリティ確保が前提（認証なしで `0.0.0.0` は LAN 内の誰でも操作可）。不正アドレスは 127.0.0.1 へフォールバック。
- ポート既定 **8080**（VLC/MultiFunPlayer 既定。VLC 本体と衝突しうるため設定可能に）。
- Basic 認証は config のパスワードで検証（未設定なら認証なし）。

---

## 6. 実装規模（見積り）

| 項目 | 規模 |
|---|---|
| `rcserver.py`（新規） | 約 170〜240 行（status.json + playlist.json + 6 コマンド + Basic 認証） |
| mainwindow 統合（変更） | 約 50〜80 行（サーバ起動/停止、メニュー、コマンド実行ヘルパ、`currentplid` 採番、config 保存） |
| appconfig（変更） | 約 5〜10 行 |
| tests（新規） | 約 90〜130 行 |
| README（変更） | 約 10〜20 行 |
| **合計** | **新規 ~260〜370 行 / 既存変更 ~65〜90 行**、工数目安 **0.5〜1 日**（MultiFunPlayer 実接続確認含む） |

---

## 7. リスク・注意点

- **スレッド**: `QTcpServer` は必ず GUI スレッドで生成。worker スレッドで `QMediaPlayer` を触らない（この方式なら構造的に回避）。
- **単位**: `seek&val` は**秒**（int）、`status.json` の `time`/`length` は**秒**、`change_rate` は float 倍率。
- **`currentplid` の採番**: 読み込みごとに値を変えないと MultiFunPlayer が `playlist.json` を再取得せず、読み込んだファイルを把握できない。`in_play` 実行時に更新する。
- **`in_play&input` のパス**: URL エンコードされた `file://` / プレーンパス。デコードして `set_audio` に渡す。動画は音声トラックのみ利用（既存ガードを踏襲）。
- **keep-alive**: MultiFunPlayer は HttpClient（keep-alive 既定）。簡易実装は `Connection: close` で返して動作確認（遅いが動く）。必要なら同一ソケットで複数リクエスト処理。

---

## 8. 要確認（実装前に決めたい点）

1. ~~ファイルロードの要否~~ → **確定: 使う（`in_play&input`）。実装必須**。
2. ポート/パスワードの既定値（既定 8080 / 認証なし、で問題ないか）。

---

## 9. 実装順序（方針合意後）

1. `rcserver.py` 実装（HTTP 解析 + status.json + playlist.json + 6 コマンド分发 + Basic 認証）
2. `mainwindow.py` 統合（サーバ起動/停止、設定メニュー、コマンド実行ヘルパ、`currentplid` 採番、config 保存）
3. `appconfig.py` 既定キー追加
4. `tests/test_rcserver.py` 追加
5. `README.md` 更新
6. MultiFunPlayer 実接続で最終確認
