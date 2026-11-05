"""Start/stop VOCE from a notebook, a script or start.sh.

    from app.launcher import start
    handle = start(mode="ursim")   # VOCE runs in its OWN process: Run All, kernel restart/interrupt or closing
                                   # the notebook do not stop it. Calling start() again re-attaches.
    handle.status(); handle.logs()
    handle.stop()                  # graceful: stops motion, drops pending actions, disconnects the robot

CLI (foreground, Ctrl+C to stop):  python -m app.launcher --mode ursim [--no-browser]

Why a separate process: when VOCE ran inside the Jupyter kernel, every kernel restart, "Run All" reaching the
stop cell, or closing the notebook shut the server down and dropped the voice session ("browser disconnesso")
although nobody had said STOP or pressed the STOP button (traced in logs/voce.jsonl on 2026-11-05).
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from dataclasses import dataclass
from pathlib import Path

from .config import FRONTEND_DIST, LOG_DIR, ROOT, ConfigError, load_settings

PIDFILE = LOG_DIR / "voce_server.json"
SERVER_LOG = LOG_DIR / "server.log"


def _alive(pid: int) -> bool:
    # If the server is OUR child, reap it first: an exited-but-unreaped child (zombie) still answers kill(pid, 0).
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass   # not our child (started by another kernel / terminal): fall through
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _status(url: str, timeout: float = 1.5) -> dict | None:
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def _port_busy(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _url(host: str, port: int) -> str:
    return f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"


@dataclass
class Handle:
    mode: str
    url: str
    pid: int | None

    @property
    def running(self) -> bool:
        return (self.pid is None or _alive(self.pid)) and _status(self.url) is not None

    def status(self) -> dict:
        s = _status(self.url) or {}
        r = s.get("robot", {})
        out = {"running": bool(s), "url": self.url, "pid": self.pid, "mode": s.get("mode"), "gate": s.get("gate"),
               "voice": (s.get("voice") or {}).get("status"), "robot": f"{r.get('robot_mode')} / {r.get('safety')}"}
        print(out)
        return out

    def logs(self, n: int = 40) -> None:
        """Last lines of the server log (logs/server.log)."""
        if SERVER_LOG.exists():
            print("".join(SERVER_LOG.read_text(errors="replace").splitlines(keepends=True)[-n:]))

    def stop(self, timeout: float = 20.0, reason: str = "handle.stop() dal notebook") -> None:
        """Graceful shutdown (SIGTERM -> FastAPI lifespan: motion stopped, pending dropped, robot disconnected)."""
        if self.pid is None or not _alive(self.pid):
            print(f"VOCE non era in esecuzione ({self.url}).")
            return
        _note(f"shutdown requested: {reason}")
        os.kill(self.pid, signal.SIGTERM)
        end = time.time() + timeout
        while time.time() < end and _alive(self.pid):
            time.sleep(0.2)
        if _alive(self.pid):
            os.kill(self.pid, signal.SIGKILL)
            print("⚠️  VOCE non ha risposto a SIGTERM: processo terminato forzatamente.")
        PIDFILE.unlink(missing_ok=True)
        print(f"VOCE fermato ({self.url}) — motivo: {reason}.")

    def __repr__(self) -> str:
        return f"<VOCE {self.mode} {self.url} pid={self.pid} {'running' if self.running else 'stopped'}>"


def _note(msg: str) -> None:
    """Record lifecycle events in the server log AND logs/voce.jsonl so every shutdown is traceable."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    with SERVER_LOG.open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} LAUNCHER {msg}\n")
    with (LOG_DIR / "voce.jsonl").open("a") as f:
        f.write(json.dumps({"ts": round(time.time(), 3), "level": "INFO", "logger": "voce.launcher", "msg": msg}) + "\n")


def running_instance() -> Handle | None:
    """The VOCE server already running on this machine (any kernel, terminal or start.sh), if any."""
    if PIDFILE.exists():
        try:
            info = json.loads(PIDFILE.read_text())
        except json.JSONDecodeError:
            info = {}
        if info.get("pid") and _alive(info["pid"]) and _status(info["url"]):
            return Handle(info["mode"], info["url"], info["pid"])
        PIDFILE.unlink(missing_ok=True)   # stale
    return None


def start(mode: str = "ursim", *, open_browser: bool = True, port: int | None = None, timeout: float = 40.0) -> Handle:
    """Start VOCE in a background process (idempotent; re-attaches to a running instance)."""
    inst = running_instance()
    if inst:
        if inst.mode != mode:
            raise RuntimeError(f"VOCE is already running in mode {inst.mode!r} (pid {inst.pid}). "
                               f"Stop it first: handle.stop()  (or: from app.launcher import running_instance; "
                               f"running_instance().stop())")
        print(f"VOCE già in esecuzione: {inst.url} (pid {inst.pid}, modalità {inst.mode}) — riconnesso.")
        return inst

    settings = load_settings(mode)            # validates YAML + env before spawning
    if port:
        settings.port = port
    url = _url(settings.host, settings.port)
    if _port_busy(settings.host, settings.port):
        raise RuntimeError(f"Port {settings.port} is used by another program (not a VOCE started by this launcher). "
                           f"Free it, then run this cell again.")
    for w in settings.warnings:
        print("⚠️ ", w)
    if not (FRONTEND_DIST / "index.html").exists():
        print("⚠️  frontend not built: run the pre-flight cell (or: cd frontend && npm install && npm run build)")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _note(f"starting mode={mode} port={settings.port}")
    log = SERVER_LOG.open("a")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    proc = subprocess.Popen([sys.executable, "-m", "app.launcher", "--mode", mode, "--port", str(settings.port),
                             "--no-browser"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            env=env, start_new_session=True)   # own session: kernel interrupt/restart does not reach it
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"VOCE server exited with code {proc.returncode}. Last log lines:\n"
                               + "".join(SERVER_LOG.read_text(errors="replace").splitlines(keepends=True)[-15:]))
        if _status(url):
            break
        time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError(f"VOCE server did not become ready in {timeout:.0f} s (see {SERVER_LOG})")
    h = Handle(mode, url, proc.pid)
    badge = "🔴 ROBOT REALE" if mode == "real" else f"modalità {mode}"
    print(f"VOCE avviato: {url}  ({badge}, pid {proc.pid}). Log: {SERVER_LOG}")
    print("VOCE resta attivo anche con Run All, riavvio del kernel o chiusura del notebook; si ferma solo con handle.stop().")
    if open_browser:
        webbrowser.open(url)
    return h


def serve(mode: str, port: int | None, open_browser: bool) -> None:
    """Run VOCE in THIS process (foreground). Used by start.sh and by the background process."""
    import uvicorn

    from .main import create_app
    settings = load_settings(mode)
    if port:
        settings.port = port
    url = _url(settings.host, settings.port)
    if _port_busy(settings.host, settings.port):
        raise SystemExit(f"VOCE: port {settings.port} already in use (another VOCE?). Open {url}")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PIDFILE.write_text(json.dumps({"pid": os.getpid(), "mode": mode, "url": url, "port": settings.port,
                                   "started": time.time()}))
    if open_browser:
        def _open():
            for _ in range(100):
                if _status(url, 0.5):
                    webbrowser.open(url)
                    return
                time.sleep(0.2)
        threading.Thread(target=_open, daemon=True).start()
    print(f"VOCE {mode} on {url} (pid {os.getpid()}) — Ctrl+C to stop", flush=True)
    try:   # uvicorn handles SIGINT/SIGTERM itself: graceful shutdown runs the FastAPI lifespan (safe stop)
        uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="warning", ws_ping_interval=20)
    finally:
        try:
            if json.loads(PIDFILE.read_text()).get("pid") == os.getpid():
                PIDFILE.unlink(missing_ok=True)
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        _note(f"pid {os.getpid()} stopped")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run VOCE in the foreground (Ctrl+C to stop)")
    ap.add_argument("--mode", default=None, choices=["sim", "ursim", "real"])
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--port", type=int, default=None)
    a = ap.parse_args()
    try:
        serve(a.mode or load_settings().mode, a.port, not a.no_browser)
    except ConfigError as e:
        raise SystemExit(f"VOCE: {e}")


if __name__ == "__main__":
    main()
