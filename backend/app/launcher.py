"""Start/stop VOCE from a notebook, a script or start.sh.

    from app.launcher import start
    handle = start(mode="ursim")     # idempotent: a second call returns the same running server
    handle.stop()

CLI:  python -m app.launcher --mode ursim [--no-browser]
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass, field

from .config import FRONTEND_DIST, ConfigError, load_settings

_lock = threading.Lock()
_handle: "Handle | None" = None


@dataclass
class Handle:
    mode: str
    url: str
    _server: object = field(repr=False)
    _thread: threading.Thread = field(repr=False)
    warnings: list[str] = field(default_factory=list)

    @property
    def running(self) -> bool:
        return self._thread.is_alive()

    def stop(self, timeout: float = 10.0) -> None:
        """Safe shutdown: the app stops any motion, drops pending actions, disconnects the robot."""
        global _handle
        self._server.should_exit = True
        self._thread.join(timeout)
        with _lock:
            if _handle is self:
                _handle = None
        print(f"VOCE fermato ({self.url}).")

    def __repr__(self) -> str:
        return f"<VOCE {self.mode} {self.url} {'running' if self.running else 'stopped'}>"


def _port_busy(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def start(mode: str = "ursim", *, open_browser: bool = True, port: int | None = None) -> Handle:
    global _handle
    with _lock:
        if _handle is not None and _handle.running:
            if _handle.mode != mode:
                raise RuntimeError(f"VOCE is already running in mode {_handle.mode!r}; call handle.stop() first.")
            print(f"VOCE già in esecuzione: {_handle.url}")
            return _handle

        settings = load_settings(mode)            # validates YAML + env; raises ConfigError
        if port:
            settings.port = port
        url = f"http://{'localhost' if settings.host in ('127.0.0.1', '0.0.0.0') else settings.host}:{settings.port}"
        if _port_busy(settings.host, settings.port):
            raise RuntimeError(f"Port {settings.port} already in use (another VOCE instance?). Stop it first.")
        for w in settings.warnings:
            print("⚠️ ", w)
        if not (FRONTEND_DIST / "index.html").exists():
            print("⚠️  frontend not built: run ./start.sh once (or: cd frontend && npm install && npm run build)")

        import uvicorn

        from .main import create_app
        app = create_app(settings)
        server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning",
                                               ws_ping_interval=20))
        server.install_signal_handlers = lambda: None  # running in a background thread
        th = threading.Thread(target=server.run, name="voce-server", daemon=True)
        th.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(url + "/api/status", timeout=1) as r:
                    if r.status == 200:
                        break
            except OSError:
                time.sleep(0.2)
            if not th.is_alive():
                raise RuntimeError("VOCE server failed to start (see logs/voce.jsonl)")
        else:
            server.should_exit = True
            raise RuntimeError("VOCE server did not become ready in 20 s")

        _handle = Handle(mode=mode, url=url, _server=server, _thread=th, warnings=settings.warnings)
        badge = "🔴 ROBOT REALE" if mode == "real" else f"modalità {mode}"
        print(f"VOCE avviato: {url}  ({badge})")
        if open_browser:
            webbrowser.open(url)
        return _handle


def main() -> None:
    ap = argparse.ArgumentParser(description="Start VOCE")
    ap.add_argument("--mode", default=None, choices=["sim", "ursim", "real"])
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    try:
        h = start(a.mode or load_settings().mode, open_browser=not a.no_browser, port=a.port)
    except (ConfigError, RuntimeError) as e:
        raise SystemExit(f"VOCE: {e}")
    try:
        while h.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        h.stop()


if __name__ == "__main__":
    main()
