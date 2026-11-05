"""The gate's optional stereo height: used only when it is on, valid, and for the same image size."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app.safety.gate import Gate


def _server(payload):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def _self(url):
    added = []
    return SimpleNamespace(s=SimpleNamespace(robot={"stereo": {"url": url}}),
                           history=SimpleNamespace(add=lambda **k: added.append(k))), added


FRAME = SimpleNamespace(size=(1600, 1200))
CUP = {"bbox": [700, 500, 120, 160]}


@pytest.mark.parametrize("payload,expect", [
    ({"ok": True, "height_m": 0.092, "frame_size": [1600, 1200], "points": 400, "plane": "marker"}, 0.092),
    ({"ok": True, "height_m": 0.092, "frame_size": [1280, 720]}, None),      # other camera image
    ({"ok": True, "height_m": 0.80, "frame_size": [1600, 1200]}, None),      # implausible
    ({"ok": False, "error": "no depth"}, None),
])
async def test_stereo_height(payload, expect, monkeypatch):
    monkeypatch.delenv("STEREO_URL", raising=False)
    srv, url = _server(payload)
    try:
        me, added = _self(url)
        got = await Gate._stereo_height_m(me, FRAME, CUP)
        assert got == expect
        assert bool(added) == (expect is not None)
    finally:
        srv.shutdown()


async def test_stereo_off_by_default(monkeypatch):
    monkeypatch.delenv("STEREO_URL", raising=False)
    me, _ = _self(None)
    assert await Gate._stereo_height_m(me, FRAME, CUP) is None


async def test_stereo_service_down(monkeypatch):
    monkeypatch.delenv("STEREO_URL", raising=False)
    me, _ = _self("http://127.0.0.1:9")
    assert await Gate._stereo_height_m(me, FRAME, CUP) is None
