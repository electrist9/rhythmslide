"""VLC 互換 HTTP インターフェース（rcserver）の純関数テスト。

ネットワーク（QTcpServer）は使わず、handle_request などの純関数だけを検証する。
"""
from __future__ import annotations

import base64
import json
from urllib.parse import quote

from rhythmslide.ui.rcserver import handle_request


class FakeController:
    """制御口を模倣し、呼ばれたコマンドを記録する。"""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def play(self) -> None:
        self.calls.append(("play",))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def stop(self) -> None:
        self.calls.append(("stop",))

    def seek(self, seconds: float) -> None:
        self.calls.append(("seek", seconds))

    def set_rate(self, rate: float) -> None:
        self.calls.append(("rate", rate))

    def load(self, path: str) -> None:
        self.calls.append(("load", path))

    def status(self) -> dict:
        return {
            "apiversion": 4, "state": "stopped", "currentplid": 0,
            "time": 0.0, "length": 0.0, "rate": 1.0,
        }

    def playlist(self) -> dict:
        return {"id": 0, "name": "playlist", "type": "root", "items": []}


def _get(target: str, password: str | None = None) -> bytes:
    lines = [f"GET {target} HTTP/1.1", "Host: 127.0.0.1:8080"]
    if password is not None:
        token = base64.b64encode((":" + password).encode()).decode()
        lines.append("Authorization: Basic " + token)
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _parse(resp: bytes) -> tuple[int, dict | None, str]:
    head, _, body = resp.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode()
    code = int(status_line.split(" ")[1])
    return code, (json.loads(body) if body else None), head.decode()


def test_status_json_has_apiversion_and_plid():
    c = FakeController()
    code, obj, _ = _parse(handle_request(_get("/requests/status.json"), "", c))
    assert code == 200
    assert obj["apiversion"] == 4
    assert "currentplid" in obj


def test_play_pause_commands():
    c = FakeController()
    handle_request(_get("/requests/status.json?command=pl_forceresume"), "", c)
    handle_request(_get("/requests/status.json?command=pl_forcepause"), "", c)
    assert ("play",) in c.calls
    assert ("pause",) in c.calls


def test_seek_uses_seconds():
    c = FakeController()
    handle_request(_get("/requests/status.json?command=seek&val=12"), "", c)
    assert ("seek", 12.0) in c.calls


def test_change_rate():
    c = FakeController()
    handle_request(_get("/requests/status.json?command=change_rate&val=1.5000"), "", c)
    assert ("rate", 1.5) in c.calls


def test_in_play_loads_decoded_path():
    c = FakeController()
    path = "F:\\music\\song.mp3"
    handle_request(_get("/requests/status.json?command=in_play&input=" + quote(path)), "", c)
    assert ("load", path) in c.calls


def test_in_play_empty_stops():
    c = FakeController()
    handle_request(_get("/requests/status.json?command=in_play&input="), "", c)
    assert ("stop",) in c.calls


def test_playlist_json_root():
    c = FakeController()
    code, obj, _ = _parse(handle_request(_get("/requests/playlist.json"), "", c))
    assert code == 200
    assert obj["type"] == "root"


def test_auth_required_then_ok():
    c = FakeController()
    code, _, head = _parse(handle_request(_get("/requests/status.json"), "secret", c))
    assert code == 401
    assert "WWW-Authenticate" in head
    code, obj, _ = _parse(handle_request(_get("/requests/status.json", "secret"), "secret", c))
    assert code == 200
    assert obj["apiversion"] == 4


def test_auth_wrong_password():
    c = FakeController()
    code, _, _ = _parse(handle_request(_get("/requests/status.json", "wrong"), "secret", c))
    assert code == 401


def test_unknown_path_404():
    c = FakeController()
    code, _, _ = _parse(handle_request(_get("/requests/nope.json"), "", c))
    assert code == 404
