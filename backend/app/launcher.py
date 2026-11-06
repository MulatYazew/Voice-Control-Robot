"""Start/stop VOCE from a notebook, a script or start.sh.

    from app.launcher import start
    handle = start(mode="ursim")   # VOCE runs in its OWN process: Run All, kernel restart/interrupt or closing
                                   # the notebook do not stop it. Calling start() again re-attaches.
    handle.status(); handle.logs()
    handle.stop()                  # graceful: stops motion, drops pending actions, disconnects the robot

    handle = start(mode="ursim", phone_camera=True)   # also start the phone-camera service (grokarm_vision)

CLI (foreground, Ctrl+C to stop):  python -m app.launcher --mode ursim [--no-browser]

Phone camera (from grokarm-ur3e): ``python -m grokarm_vision`` runs as a SEPARATE process. Its operator API stays on
127.0.0.1:8710; only the phone page (HTTPS, self-signed certificate in .certs/) listens on the LAN at :8711 so a phone on
the same Wi-Fi can send frames. VOCE itself stays on 127.0.0.1 and reads frames from
http://127.0.0.1:8710/api/frame.jpg (CAMERA_SOURCE=url). macOS may ask once to allow incoming connections for Python.

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
VISION_PIDFILE = LOG_DIR / "vision_server.json"
VISION_LOG = LOG_DIR / "vision.log"
VISION_PORT, VISION_PHONE_PORT = 8710, 8711


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


def _terminate(pid: int, timeout: float) -> bool:
    """SIGTERM, wait, SIGKILL as a last resort. True if it exited on SIGTERM."""
    os.kill(pid, signal.SIGTERM)
    end = time.time() + timeout
    while time.time() < end and _alive(pid):
        time.sleep(0.2)
    if _alive(pid):
        os.kill(pid, signal.SIGKILL)
        return False
    return True


def vision_instance() -> dict | None:
    """The phone-camera service started by this launcher, if it is still running."""
    if VISION_PIDFILE.exists():
        try:
            info = json.loads(VISION_PIDFILE.read_text())
        except json.JSONDecodeError:
            info = {}
        if info.get("pid") and _alive(info["pid"]) and _port_busy("127.0.0.1", info.get("port", VISION_PORT)):
            return info
        VISION_PIDFILE.unlink(missing_ok=True)
    return None


def start_phone_camera(port: int = VISION_PORT, phone_port: int = VISION_PHONE_PORT, timeout: float = 30.0) -> dict:
    """Start ``python -m grokarm_vision`` in its own process (idempotent). Returns {pid, port, frame_url, phone_urls}."""
    inst = vision_instance()
    if inst:
        print(f"Telecamera del telefono già attiva (pid {inst['pid']}).")
        return inst
    if _port_busy("127.0.0.1", port):
        raise RuntimeError(f"Port {port} is used by another program: free it or pass phone_camera_port=...")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    start_at = VISION_LOG.stat().st_size if VISION_LOG.exists() else 0
    log = VISION_LOG.open("a")
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend") + os.pathsep + os.environ.get("PYTHONPATH", ""),
           "PYTHONUNBUFFERED": "1", "CAMERA_SOURCE": "push", "CAMERA_URL": ""}   # the phone PUSHES frames here; never poll VOCE (no loop)
    proc = subprocess.Popen([sys.executable, "-m", "grokarm_vision", "--port", str(port), "--phone-port", str(phone_port)],
                            cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                            start_new_session=True)
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            raise RuntimeError(f"Phone-camera service exited with code {proc.returncode}. See {VISION_LOG}:\n"
                               + VISION_LOG.read_text(errors="replace")[start_at:][-1500:])
        if _port_busy("127.0.0.1", port):
            break
        time.sleep(0.3)
    else:
        proc.terminate()
        raise RuntimeError(f"Phone-camera service did not start in {timeout:.0f} s (see {VISION_LOG})")
    time.sleep(0.5)
    out = VISION_LOG.read_text(errors="replace")[start_at:]
    phones = [ln.strip() for ln in out.splitlines() if ln.strip().startswith(("https://", "http://")) and f":{phone_port}" in ln]
    info = {"pid": proc.pid, "port": port, "phone_port": phone_port, "phone_urls": phones,
            "frame_url": f"http://127.0.0.1:{port}/api/frame.jpg", "started": time.time()}
    VISION_PIDFILE.write_text(json.dumps(info))
    _note(f"phone camera started pid={proc.pid} port={port} phone_port={phone_port}")
    print(f"Telecamera del telefono avviata (pid {proc.pid}). Apri sul telefono (stessa Wi-Fi, accetta il certificato):")
    for u in phones or [f"https://<ip-del-mac>:{phone_port}/"]:
        print("   ", u)
    return info


def stop_phone_camera(timeout: float = 10.0) -> None:
    inst = vision_instance()
    if not inst:
        return
    _terminate(inst["pid"], timeout)
    VISION_PIDFILE.unlink(missing_ok=True)
    _note(f"phone camera pid {inst['pid']} stopped")
    print("Telecamera del telefono fermata.")


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
        if not _terminate(self.pid, timeout):
            print("⚠️  VOCE non ha risposto a SIGTERM: processo terminato forzatamente.")
        PIDFILE.unlink(missing_ok=True)
        print(f"VOCE fermato ({self.url}) — motivo: {reason}.")
        stop_phone_camera()

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


def start(mode: str = "ursim", *, open_browser: bool = True, port: int | None = None, timeout: float = 40.0,
          phone_camera: bool = False) -> Handle:
    """Start VOCE in a background process (idempotent; re-attaches to a running instance).

    phone_camera=True also starts the grokarm_vision phone-camera service and points VOCE's cell camera at it."""
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
    env = {**os.environ, "PYTHONPATH": str(ROOT / "backend") + os.pathsep + os.environ.get("PYTHONPATH", "")}
    if phone_camera:
        cam = start_phone_camera()
        env.update(CAMERA_SOURCE="url", CAMERA_URL=cam["frame_url"])
    _note(f"starting mode={mode} port={settings.port}" + (" camera=phone" if phone_camera else ""))
    log = SERVER_LOG.open("a")
    proc = subprocess.Popen([sys.executable, "-m", "app.launcher", "--mode", mode, "--port", str(settings.port),
                             "--no-browser"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                            env=env, start_new_session=True)   # own session: kernel interrupt/restart does not reach it
    end = time.time() + timeout
    while time.time() < end:
        if proc.poll() is not None:
            stop_phone_camera()
            raise RuntimeError(f"VOCE server exited with code {proc.returncode}. Last log lines:\n"
                               + "".join(SERVER_LOG.read_text(errors="replace").splitlines(keepends=True)[-15:]))
        if _status(url):
            break
        time.sleep(0.3)
    else:
        proc.terminate()
        stop_phone_camera()
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
