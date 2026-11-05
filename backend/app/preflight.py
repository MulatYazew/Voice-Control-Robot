"""Pre-flight for the final implementation: verify (and where safe, prepare) everything VOCE needs.

    from app.preflight import prepare, run_tests, run_evaluations
    report = prepare(mode="ursim")      # checks + builds frontend / dataset if missing; never moves the robot

Checks: configuration, API key, Python deps, Tesseract (ita+eng), frontend build, synthetic dataset,
Grok reachability, robot connection (read-only RTDE/Dashboard status), free port.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Any

from .config import FRONTEND_DIST, ROOT, ConfigError, load_settings

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _row(name: str, status: str, detail: str) -> dict[str, str]:
    return {"check": name, "status": status, "detail": detail}


def _print(rows: list[dict[str, str]]) -> None:
    icon = {OK: "✅", WARN: "⚠️ ", FAIL: "❌"}
    w = max(len(r["check"]) for r in rows)
    for r in rows:
        print(f"{icon[r['status']]} {r['check']:<{w}}  {r['detail']}")


def _newest(path, patterns) -> float:
    return max((p.stat().st_mtime for pat in patterns for p in path.rglob(pat)), default=0.0)


def check_frontend(build: bool = True) -> dict[str, str]:
    idx = FRONTEND_DIST / "index.html"
    src = ROOT / "frontend" / "src"
    stale = idx.exists() and _newest(src, ["*.ts", "*.tsx", "*.css"]) > idx.stat().st_mtime
    if idx.exists() and not stale:
        return _row("frontend", OK, "build up to date (frontend/dist)")
    if not build:
        return _row("frontend", FAIL if not idx.exists() else WARN, "not built" if not idx.exists() else "build older than sources")
    npm = shutil.which("npm")
    if not npm:
        return _row("frontend", FAIL if not idx.exists() else WARN, "npm not found: install Node.js to build the UI")
    cwd = ROOT / "frontend"
    if not (cwd / "node_modules").exists():
        subprocess.run([npm, "install", "--no-audit", "--no-fund"], cwd=cwd, check=False, capture_output=True)
    r = subprocess.run([npm, "run", "build"], cwd=cwd, capture_output=True, text=True)
    return _row("frontend", OK if r.returncode == 0 else FAIL,
                "built now" if r.returncode == 0 else (r.stdout + r.stderr)[-300:])


def check_dataset(generate: bool = True) -> dict[str, str]:
    d = ROOT / "data" / "datasets" / "synthetic"
    n = len(list(d.glob("*/truth.json"))) if d.exists() else 0
    if n:
        return _row("dataset (synthetic)", OK, f"{n} products, {sum(1 for _ in d.rglob('*.jpg'))} images")
    if not generate:
        return _row("dataset (synthetic)", WARN, "missing: python scripts/make_synthetic_packs.py")
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "make_synthetic_packs.py")], capture_output=True, text=True)
    return _row("dataset (synthetic)", OK if r.returncode == 0 else WARN, r.stdout.strip()[-200:] or r.stderr[-200:])


def check_ocr() -> dict[str, str]:
    from .labelling import ocr
    if not ocr.available():
        return _row("OCR (Tesseract)", FAIL, "not installed: brew install tesseract tesseract-lang (labelling → REVIEW only)")
    langs = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout.split()
    missing = [l for l in ("ita", "eng") if l not in langs]
    return _row("OCR (Tesseract)", WARN if missing else OK, f"missing languages {missing}" if missing else "ita + eng")


def check_lexicon() -> dict[str, str]:
    try:
        from .safety.phrases import lexicon_review_status, load_lexicon
        data = load_lexicon()
        st = lexicon_review_status()
        n = sum(len(v) for v in data["stop"].values())
        unver = st["unverified_languages"]
        detail = f"{n} stop entries · languages {', '.join(data['stop'])}"
        if unver:
            detail += f" · UNVERIFIED review: {', '.join(unver)}"
        return _row("lexicon", WARN if unver else OK, detail)
    except Exception as e:
        return _row("lexicon", FAIL, str(e))


def check_python() -> dict[str, str]:
    missing = []
    for mod in ("fastapi", "uvicorn", "websockets", "yaml", "cv2", "pytesseract", "rapidfuzz", "multipart", "httpx"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return _row("python packages", FAIL if missing else OK,
                f"missing {missing}: pip install -r requirements.txt" if missing else f"Python {sys.version.split()[0]}")


def check_grok(api_key: str | None) -> dict[str, str]:
    if not api_key:
        return _row("Grok (xAI)", WARN, "XAI_API_KEY not set: voice off, TEXT MODE only, labelling without Grok")
    try:
        req = urllib.request.Request("https://api.x.ai/v1/language-models", headers={"Authorization": f"Bearer {api_key}"})
        models = [m["id"] for m in json.loads(urllib.request.urlopen(req, timeout=10).read()).get("models", [])]
        return _row("Grok (xAI)", OK, f"key valid ({api_key[:4]}…{api_key[-4:]}), {len(models)} models")
    except Exception as e:
        return _row("Grok (xAI)", WARN, f"not reachable: {e}")


def check_robot(settings) -> list[dict[str, str]]:
    """Read-only: connect, read mode / safety / remote control. Never sends motion."""
    if settings.mode == "sim":
        return [_row("robot", OK, "in-process simulator (ROBOT_MODE=sim)")]
    from .robot.ur import URAdapter
    rows = []
    try:
        a = URAdapter(settings)
        a.connect()
        st = a.status()
        a.disconnect()
    except Exception as e:
        hint = ("start your URSim (desktop app or scripts/ursim.sh start), power on + release brakes"
                if settings.mode == "ursim" else "check the real robot IP / network")
        return [_row("robot connection", FAIL, f"{settings.robot_host}: {e} → {hint}")]
    rows.append(_row("robot connection", OK, f"{st.detail} · joints streaming (RTDE)"))
    good = st.robot_mode == "RUNNING" and st.safety in ("NORMAL", "REDUCED")
    rows.append(_row("robot state", OK if good else WARN,
                     f"mode {st.robot_mode}, safety {st.safety}" + ("" if good else " → power on and release the brakes in PolyScope")))
    if st.remote_control is False:
        rows.append(_row("remote control", WARN, "Local Control: external programs are rejected → enable Remote Control in PolyScope"))
    if settings.mode == "real":
        unverified = [k for k, v in settings.poses.items() if not v.get("verificata_reale")]
        rows.append(_row("real poses", WARN if unverified else OK,
                         f"{len(unverified)} poses not recorded on the real robot (those skills will be refused)" if unverified
                         else "all poses verified"))
    return rows


def check_port(host: str, port: int) -> dict[str, str]:
    with socket.socket() as s:
        s.settimeout(0.3)
        busy = s.connect_ex((host, port)) == 0
    if not busy:
        return _row("port", OK, f"{host}:{port} free")
    try:
        urllib.request.urlopen(f"http://{host}:{port}/api/status", timeout=2)
        return _row("port", WARN, f"VOCE already running on :{port} (start() will refuse a second copy: pkill -f app.launcher)")
    except Exception:
        return _row("port", FAIL, f"{host}:{port} used by another program")


def prepare(mode: str = "ursim", *, build_frontend: bool = True, generate_dataset: bool = True) -> dict[str, Any]:
    """Run every check, fix what is safe to fix automatically, print a checklist. Never moves the robot."""
    rows: list[dict[str, str]] = [check_python(), check_lexicon()]
    try:
        s = load_settings(mode)
        rows.append(_row("configuration", OK, f"mode {s.mode} · {len(s.skills)} skills · {len(s.poses)} poses · zones {s.zones}"))
    except ConfigError as e:
        rows.append(_row("configuration", FAIL, str(e)))
        _print(rows)
        return {"ok": False, "checks": rows}
    rows += [check_grok(s.xai_api_key), check_ocr(), check_frontend(build_frontend), check_dataset(generate_dataset)]
    rows += check_robot(s)
    rows.append(check_port(s.host, s.port))
    for w in s.warnings:
        if "UNKNOWN" in w:
            rows.append(_row("hardware facts", WARN, w))
    _print(rows)
    ok = not any(r["status"] == FAIL for r in rows)
    print("\n" + ("READY: run the next cell to start VOCE." if ok else "NOT READY: fix the ❌ items above."))
    return {"ok": ok, "mode": mode, "checks": rows}


def _run(args: list[str], timeout: int) -> tuple[int, str]:
    t0 = time.time()
    r = subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, "PYTHONPATH": str(ROOT / "backend")})
    return r.returncode, f"({time.time() - t0:.0f}s)\n" + (r.stdout + r.stderr)[-1500:]


def run_tests() -> bool:
    """Full automated test suite (no robot, no xAI)."""
    code, out = _run(["-m", "pytest", "-q", "tests", "-p", "no:cacheprovider", "--color=no"], 900)
    print("\n".join(l for l in out.splitlines() if "passed" in l or "failed" in l or "error" in l.lower())[-600:] or out)
    return code == 0


def run_evaluations(*, voice: bool = False, labelling: bool = False, latency: bool = False, spoken: bool = False) -> None:
    """Optional evaluations (they call the xAI API and take minutes). Results go to eval/results/.
    latency=True needs VOCE running with a robot connected; it MOVES the robot (predefined poses only)."""
    if voice:
        print("== voice: rules vs Grok on eval/testset.csv")
        print(_run(["-m", "eval.run_eval", "--engines", "rules,grok"], 1800)[1][-900:])
    if labelling:
        print("== labelling: synthetic dataset with Grok")
        print(_run(["-m", "eval.labelling.run_eval"], 2400)[1][-900:])
    if latency:
        print("== STOP latency (moves the robot between home and attesa)")
        print(_run(["-m", "eval.latency", "--trials", "10", *(["--spoken"] if spoken else [])], 1800)[1][-1200:])
    if not (voice or labelling or latency):
        print("Nothing selected. Set voice=True / labelling=True / latency=True.")
