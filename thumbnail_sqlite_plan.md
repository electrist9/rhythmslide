# サムネイルキャッシュの SQLite 化 調査報告

## 1. 目的と結論（先に）

現状は「1 元画像 → 1 サムネイル JPEG ファイル」を `thumbcache/` ディレクトリに大量に置く方式。
これを **1 個の SQLite DB ファイル**（BLOB 格納）へ集約する変更の調査。

**結論：実装は可能。ただし本質的な変更点は 2 つ。**

1. **API の戻り値が変わる**：`get()`/`ensure()` は「JPEG ファイルのパス」を返すが、
   SQLite 化後はファイルが存在しないので **BLOB（bytes）を返す設計**に変える必要がある。
   消費側（素材パネル）は `QPixmap(str(path))` → `QPixmap.loadFromData(bytes)` に変更。
2. **スレッド亲和性（affinity）の問題**：同一 `ThumbnailCache` インスタンスが
   **GUI スレッドと QThreadPool のワーカスレッドで同時に使われる**。
   `sqlite3.Connection` は既定でスレッドを跨げないため、
   **`check_same_thread=False` ＋ロック**、または**スレッド別コネクション**が必須。

影響範囲は小さく、書き換えは主に [`thumbnails.py`](rhythmslide/core/thumbnails.py)、
消費側は [`materialpanel.py`](rhythmslide/ui/materialpanel.py) の 1 箇所、
パス定義は [`paths.py`](rhythmslide/core/paths.py)、テストは [`test_thumbnails.py`](tests/test_thumbnails.py)。

---

## 2. 現状の仕組み（[`thumbnails.py`](rhythmslide/core/thumbnails.py)）

- キャッシュキー [`thumb_key()`](rhythmslide/core/thumbnails.py#L21) =
  `sha1( 絶対パス(lower) + st_mtime_ns + st_size + "|"+辺長 )` の hexdigest。
  → 元画像の更新（mtime/size 変化）で自動的に別キーになり、古い 1 枚は残る（ガベージは消えない）。
- 保存先 [`thumbnail_cache_dir()`](rhythmslide/core/paths.py#L45) = `data_dir()/thumbcache`
  （ワークスペース直下 `.rhythmslide_data/thumbcache`。APPDATA 等は使わない方針は維持）。
- 辺長は既定 `THUMB_SIZE = 256`。実運用では**単一サイズのみ**（MainWindow は既定のみ使用）。
- 保存形式は JPEG（quality=82）。壊れた画像はグレーのプレースホルダ JPEG。
- 原子的書き込み：`_render()` は `.tmp` に書いて `tmp.replace(dest)`（[`thumbnails.py:67-69`](rhythmslide/core/thumbnails.py#L67)）。

### 公開 API と戻り値
| メソッド | 戻り値 | 意味 |
|---|---|---|
| `file_for(path)` | `Path` | 想定 JPEG パス（内部用のみ・外部未使用） |
| `get(path)` | `Path \| None` | **キャッシュ済みならパス**（生成はしない） |
| `ensure(path)` | `Path \| None` | 生成してパス（失敗時 None） |
| `_render_rgb(path)` | `(bytes,w,h) \| None` | **未使用のデッドコード**（どこからも呼ばれていない） |

---

## 3. 呼び出し経路と制約（重要）

### 3.1 消費側は 2 スレッドで同時に触る
- **GUI スレッド**：[`materialpanel._icon()`](rhythmslide/ui/materialpanel.py#L50) が
  `self.cache.get(p.path)` → `QPixmap(str(thumb))`（[materialpanel.py:54-56](rhythmslide/ui/materialpanel.py#L54)）。
- **ワーカスレッド**：[`ThumbnailWorker._run()`](rhythmslide/ui/workers.py#L105) が
  `self.cache.ensure(p.path)`。これは `QThreadPool.globalInstance()` 経由で**プールスレッド**で走る
  （[workers.py:29-33](rhythmslide/ui/workers.py#L29)、[mainwindow.py:67](rhythmslide/ui/mainwindow.py#L67)）。
- 同一インスタンス [`self.thumb_cache = ThumbnailCache()`](rhythmslide/ui/mainwindow.py#L65) を両スレッドが共有。

→ **SQLite の単一コネクションをそのまま共有すると `sqlite3.ProgrammingError`（スレッド跨ぎ）になる。**
   生成中（ワーカ）と表示（GUI）が同時に走るため、これは必ず対処が要る。

### 3.2 戻り値の実消費箇所（blast radius）
- `get()` の戻り値 → **materialpanel の 1 箇所のみ**（`QPixmap(str(thumb))`）。
- `ensure()` の戻り値 → **ワーカは戻り値を捨てている**（`p.path` を emit するだけ）。テストのみ使用。
- `file_for()` → 外部未使用（内部のみ）。

→ 戻り値を bytes に変えても、UI 側の修正は **materialpanel の 1 行**で済む。

---

## 4. SQLite 化の設計案

### 4.1 スキーマ（1 DB ファイル）
```sql
CREATE TABLE IF NOT EXISTS thumbnails (
  key        TEXT PRIMARY KEY,   -- thumb_key(path, size) の hexdigest
  path       TEXT NOT NULL,      -- 元画像パス（デバッグ・移行用）
  size       INTEGER NOT NULL,   -- 要求辺長（複数サイズ対応ならキーの一部）
  width      INTEGER NOT NULL,
  height     INTEGER NOT NULL,
  mtime_ns   INTEGER,            -- 元画像の mtime_ns（監査用）
  src_size   INTEGER,            -- 元画像のバイト数（監査用）
  blob       BLOB NOT NULL,      -- JPEG 本体
  created_at REAL NOT NULL       -- 生成時刻（LRU/掃除用）
);
```
- キーは現状の `thumb_key()` をそのまま流用（`path+mtime_ns+size+辺長`）。
  → 元画像が変われば別行が増える（現状と同じ挙動）。掃除は §4.4 参照。

### 4.2 スレッド対処（推奨：ロック方式）
```python
import sqlite3, threading
class ThumbnailCache:
    def __init__(self, db_path=None, size=THUMB_SIZE):
        self.db_path = Path(db_path) if db_path else thumbnail_db_path()
        self.size = int(size)
        self._lock = threading.RLock()
        self._con = None  # 遅延オープン

    def _conn(self):
        if self._con is None:
            ensure_dir(self.db_path.parent)
            self._con = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._con.execute("PRAGMA journal_mode=WAL;")   # 読み書き並発を改善
            self._con.execute("PRAGMA synchronous=NORMAL;")
            self._con.execute(CREATE_TABLE)
        return self._con
```
- **`check_same_thread=False` ＋ `RLock`** で全 DB 操作を直列化。サムネイルは小さく
  アクセスはバースト的なので、直列化でも体感影響は小さい。
- **WAL モード**で GUI の読み取りとワーカの書き込みの待ち受けを軽減。
- 代替案：`threading.local()` でスレッド別コネクション（書込並列度が上がるが複雑）。
  規模（1 万枚・単一サイズ）ではロック方式で十分と判断。

### 4.3 API 変更（戻り値を bytes に）
```python
def get(self, path) -> bytes | None:      # 既存なら JPEG BLOB を返す
    with self._lock:
        row = self._conn().execute(
            "SELECT blob FROM thumbnails WHERE key=?", (thumb_key(path, self.size),)
        ).fetchone()
    return row[0] if row else None

def ensure(self, path) -> bytes | None:   # 生成して BLOB を返す
    key = thumb_key(path, self.size)
    blob = self.get(path)
    if blob is not None:
        return blob
    blob = self._render_blob(path)        # PIL→JPEG→bytes（プレースホルダ含む）
    if blob is None:
        return None
    with self._lock:
        self._conn().execute(
            "INSERT OR REPLACE INTO thumbnails"
            "(key,path,size,width,height,mtime_ns,src_size,blob,created_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (key, str(path), self.size, w, h, mtime_ns, src_size, blob, time.time()),
        )
        self._conn().commit()
    return blob
```
- `_render()`（ファイル書き込み）は `_render_blob()`（bytes を返す）に置換。
  原子的 `tmp.replace` は不要（トランザクションが担保）。
- 消費側 [materialpanel.py:54-56](rhythmslide/ui/materialpanel.py#L54) を
  ```python
  data = self.cache.get(p.path)
  if data:
      pm = QPixmap()
      if not pm.loadFromData(data):   # ← bytes を直接ロード
          ...
  ```
  に変更。`_icons` のメモリキャッシュ（[materialpanel.py:51](rhythmslide/ui/materialpanel.py#L51)）はそのまま有効。

### 4.4 パス定義（[`paths.py`](rhythmslide/core/paths.py)）
- `thumbnail_cache_dir()`（[paths.py:45](rhythmslide/core/paths.py#L45)）を、
  `thumbnail_db_path() -> data_dir()/thumbcache/thumbnails.db` に置換、または併存。
- ワークスペース内保存の方針（APPDATA 不可）は不変。

---

## 5. 利点 / 懸念

### 利点
- **ファイル数が 1 個**に。1 万枚超で数万個の小さな JPEG が増える問題（NTFS/ネットワーク
  ドライブでの列挙・バックアップ・削除の遅さ、inode 消費）が消える。
- 単一ファイルなので**コピー・バックアップ・削除・移行が容易**。
- BLOB 検索はキー（PRIMARY KEY）で O(log n)。`get` は 1 回の SELECT。

### 懸念・トレードオフ
- **DB 肥大**：1 万枚 × 約 10〜20KB ≈ **100〜200MB** が 1 ファイルに。WAL で一時ファイル増。
- **単一ファイルロック競合**：生成中（ワーカ）と表示（GUI）が同一 DB を触る → §4.2 のロック/WAL が前提。
- **破損リスクの集中**：1 ファイル破損で全キャッシュ失う（現状は 1 枚ずつ独立）。
  → WAL＋`synchronous=NORMAL`、必要に応じ定期バックアップ。
- **既存キャッシュの扱い**：旧 `thumbcache/*.jpg` を DB へ import するか、破棄して再生成するか要決定。
- **掃除（GC）不在**：現状も eviction はないが、元画像の更新で古い行が残り続ける。
  SQLite 化を機に**上限＋LRU**（`created_at` で古い行を削除）を入れると良い（任意）。

---

## 6. 変更が必要なファイル（実装規模）

| ファイル | 変更内容 | 規模 |
|---|---|---|
| [`core/thumbnails.py`](rhythmslide/core/thumbnails.py) | クラスを SQLite 実装に書換え（`get/ensure`→bytes、`_render_blob`、ロック/WAL、任意で LRU）。`thumb_key` は維持。`_render_rgb`（未使用）は削除可 | 中 |
| [`core/paths.py`](rhythmslide/core/paths.py) | `thumbnail_cache_dir()` → `thumbnail_db_path()`（DB パス） | 小 |
| [`ui/materialpanel.py`](rhythmslide/ui/materialpanel.py) | `QPixmap(str(thumb))` → `loadFromData(bytes)`（1 箇所） | 小 |
| [`ui/workers.py`](rhythmslide/ui/workers.py) | 戻り値を捨てているので**実質変更不要**（`ensure` 呼び出しは同一） | 極小 |
| [`tests/test_thumbnails.py`](tests/test_thumbnails.py) | 戻り値が bytes になる前提で検証書換え（`Image.open`→`Image.open(io.BytesIO(blob))`、`first==second`→bytes 一致） | 小 |

`file_for()` は外部未使用なので削除または内部ヘルパー化で可。

---

## 7. 移行戦略（推奨）

1. 起動時に `thumbnails.db` が無く、旧 `thumbcache/*.jpg` が有る場合：
   - **A案（簡単）**：旧キャッシュは無視して再生成（初回だけ遅い）。
   - **B案（親切）**：旧 JPEG を 1 回だけ DB へ import（`thumb_key` を再計算して格納）→ 以降 DB のみ。
2. 移行後は `thumbcache/` を削除（または次回起動で掃除）。
3. 判断が難しければ **A案**で十分（サムネイル再生成は許容範囲）。

---

## 8. 要確認事項（実装前に決めたい点）

1. **既存キャッシュの扱い**：A（破棄して再生成）/ B（DB へ import）のどちらにするか。
2. **サイズ**：単一 256px のままか、複数サイズ（アイコン 64 ＋プレビュー 256 等）にも備えるか。
   （複数なら `size` をキーに含める設計は既に用意済み）
3. **上限・LRU 掃除**を入れるか（1 万枚超で DB が肥大するため推奨）。入れるなら上限 MB 数。
4. **壊れた画像**の扱い：現状同様プレースホルダを BLOB として保存で良いか。
5. **プレゼン/プレビューのフル解像度キャッシュ**（[`imagecache.py`](rhythmslide/core/imagecache.py)）は
   本件の**対象外**（メモリ内 LRU のみ）で良いか。→ 対象外で問題なしと想定。

---

## 9. 実装順序（やるなら）

1. `paths.py` に `thumbnail_db_path()` 追加。
2. `thumbnails.py` を SQLite 版に書換え（`get/ensure`→bytes、`_render_blob`、ロック/WAL）。
3. `materialpanel.py` を `loadFromData` に変更。
4. `test_thumbnails.py` を bytes 前提で更新（＋可能なら複数スレッド同時アクセスの回帰テスト追加）。
5. 必要なら移行処理（A/B）と LRU 掃除を追加。
6. 全体 pytest → オフスクリーンで MainWindow 起動し素材パネル表示を確認。
