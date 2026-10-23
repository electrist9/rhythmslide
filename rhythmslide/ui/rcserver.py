"""外部制御用の VLC 互換 HTTP インターフェース（MultiFunPlayer 対応）。

VLC の mod_http が提供する `status.json` / `playlist.json` / コマンド API の
サブセットを応答する小さな HTTP サーバ。MultiFunPlayer の `VlcMediaSource` は
VLC 本体の代わりにこれを叩くだけで、再生・停止・シーク・ファイル読み込みができる。

設計:
- `QTcpServer` を **GUI スレッド**で動かし、`readyRead` で受信したリクエストを
  その場で解析して制御を GUI スレッドで実行する。worker スレッドを作らないため
  `QMediaPlayer`（GUI スレッド専用）を安全に操作でき、スレッド間中継が不要。
- HTTP 解析・コマンド分发・JSON 生成は `handle_request` などの純関数に分離し、
  ネットワークを伴わずテストできるようにしている。

MultiFunPlayer が実際に使うコマンドのみを実装する（volume 等は不使用）。
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

log = logging.getLogger(__name__)

# 応答の HTTP ステータスライン用テキスト
_STATUS_TEXT = {
    200: "OK",
    400: "Bad Request",
    401: "Unauthorized",
    404: "Not Found",
    405: "Method Not Allowed",
    500: "Internal Server Error",
}


class ControlHandler:
    """サーバが呼ぶ制御口のプロトコル（duck typing 用、実体は mainwindow 側）。"""

    def play(self) -> None: ...
    def pause(self) -> None: ...
    def stop(self) -> None: ...
    def seek(self, seconds: float) -> None: ...
    def set_rate(self, rate: float) -> None: ...
    def load(self, path: str) -> None: ...
    def status(self) -> Dict[str, Any]: ...
    def playlist(self) -> Dict[str, Any]: ...


# ---------------------------------------------------------------------------
# 純関数（ネットワーク不要でテスト可能）
# ---------------------------------------------------------------------------
def build_response(code: int, obj: Any, www_auth: bool = False) -> bytes:
    """JSON 本文付き HTTP 応答バイト列を作る（Connection: close）。"""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    head = [
        f"HTTP/1.1 {code} {_STATUS_TEXT.get(code, 'OK')}",
        "Content-Type: application/json; charset=utf-8",
        f"Content-Length: {len(body)}",
        "Connection: close",
    ]
    if www_auth:
        head.append('WWW-Authenticate: Basic realm="RhythmSlide"')
    return ("\r\n".join(head) + "\r\n\r\n").encode("latin-1") + body


def authorized(headers: Dict[str, str], password: str) -> bool:
    """Basic 認証を確認する。パスワード未設定なら常に許可。"""
    if not password:
        return True
    auth = headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(auth[6:].strip()).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return False
    _user, _sep, pw = decoded.partition(":")
    return pw == password


def run_command(command: str, query: Dict[str, List[str]], handler: ControlHandler) -> None:
    """VLC コマンドを制御口へ分发する。MultiFunPlayer が使うものだけを実装。"""
    val = (query.get("val") or [""])[0]
    inp = (query.get("input") or [""])[0]
    if command in ("pl_forceresume", "pl_play", "pl_resume"):
        handler.play()
    elif command in ("pl_forcepause", "pl_pause"):
        handler.pause()
    elif command == "pl_stop":
        handler.stop()
    elif command == "seek":
        try:
            handler.seek(float(val))
        except ValueError:
            pass
    elif command == "change_rate":
        try:
            handler.set_rate(float(val))
        except ValueError:
            pass
    elif command == "in_play":
        # MultiFunPlayer はファイルロードに in_play&input=<URLエンコードしたパス> を使う。
        if inp:
            handler.load(inp)
        else:
            handler.stop()
    # volume / pl_next / pl_previous / add&uri 等は MultiFunPlayer は使わない（無視）。


def handle_request(data: bytes, password: str, handler: ControlHandler) -> bytes:
    """1 本の HTTP リクエストを処理し、応答バイト列を返す。"""
    text = data.decode("latin-1", "replace")
    head = text.split("\r\n\r\n", 1)[0]
    lines = head.split("\r\n")
    request_line = lines[0] if lines else ""
    parts = request_line.split(" ")
    if len(parts) < 2:
        return build_response(400, {"error": "bad request"})
    method, target = parts[0].upper(), parts[1]
    if method != "GET":
        return build_response(405, {"error": "method not allowed"})

    headers: Dict[str, str] = {}
    for ln in lines[1:]:
        if not ln:
            break
        key, sep, value = ln.partition(":")
        if sep:
            headers[key.strip().lower()] = value.strip()

    if not authorized(headers, password):
        return build_response(401, {"error": "unauthorized"}, www_auth=True)

    parsed = urlparse(target)
    path = parsed.path
    query = parse_qs(parsed.query, keep_blank_values=True)

    if path.endswith("/status.json"):
        command = (query.get("command") or [""])[0]
        if command:
            run_command(command, query, handler)
        return build_response(200, handler.status())
    if path.endswith("/playlist.json"):
        return build_response(200, handler.playlist())
    return build_response(404, {"error": "not found"})


# ---------------------------------------------------------------------------
# QTcpServer による配線（GUI スレッドで生成すること）
# ---------------------------------------------------------------------------
class VlcControlServer(QObject):
    """VLC 互換 HTTP サーバ。`handler` の制御口を GUI スレッドで呼ぶ。"""

    def __init__(self, handler: ControlHandler, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.handler = handler
        self.password: str = ""
        self._server = QTcpServer(self)
        self._server.newConnection.connect(self._on_new_connection)
        self._sockets: List[QTcpSocket] = []
        self._buffers: Dict[int, bytes] = {}

    def start(self, port: int, password: str = "", host: str = "127.0.0.1") -> bool:
        """指定ポートで受信を開始する。失敗時は False。

        host は待ち受けアドレス。既定は 127.0.0.1（ローカルのみ）。0.0.0.0 などに
        すると LAN 内の他端末からも接続できる（セキュリティ確保が前提）。不正な
        アドレス文字列の場合は安全側へフォールバックする。
        """
        self.stop()
        self.password = password or ""
        addr = QHostAddress(host)
        if addr.isNull():
            log.warning(
                "待ち受けアドレスが不正です（%s）。127.0.0.1 にフォールバックします。", host,
            )
            addr = QHostAddress("127.0.0.1")
            host = "127.0.0.1"
        if not self._server.listen(addr, int(port)):
            log.warning(
                "外部制御サーバの起動に失敗: %s (%s:%s)",
                self._server.errorString(), host, port,
            )
            return False
        log.info("外部制御サーバ起動: http://%s:%s/requests/status.json", host, port)
        return True

    def stop(self) -> None:
        """受信を止め、接続中のソケットを閉じる。"""
        for sock in list(self._sockets):
            sock.abort()
        self._sockets.clear()
        self._buffers.clear()
        if self._server.isListening():
            self._server.close()

    def is_running(self) -> bool:
        return self._server.isListening()

    # --- シグナルハンドラ -------------------------------------------------
    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            sock = self._server.nextPendingConnection()
            if sock is None:
                continue
            self._sockets.append(sock)
            sock.readyRead.connect(lambda s=sock: self._on_ready_read(s))
            sock.disconnected.connect(lambda s=sock: self._drop(s))

    def _drop(self, sock: QTcpSocket) -> None:
        if sock in self._sockets:
            self._sockets.remove(sock)
        self._buffers.pop(id(sock), None)
        sock.deleteLater()

    def _on_ready_read(self, sock: QTcpSocket) -> None:
        buf = self._buffers.get(id(sock), b"") + bytes(sock.readAll())
        # GET はボディを持たないため、ヘッダ終端まで来れば処理できる。
        if b"\r\n\r\n" not in buf and b"\n\n" not in buf:
            self._buffers[id(sock)] = buf
            return
        self._buffers.pop(id(sock), None)
        try:
            resp = handle_request(buf, self.password, self.handler)
        except Exception as exc:  # 応答は必ず返す
            log.debug("外部制御のリクエスト処理でエラー: %s", exc)
            resp = build_response(500, {"error": "internal"})
        sock.write(resp)
        sock.flush()
        sock.disconnectFromHost()
