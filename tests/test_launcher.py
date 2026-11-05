"""VOCE runs in its own process: re-attach, survive the caller, stop only on request (2026-11-05 incident)."""

import os
import socket
import subprocess
import sys
import textwrap

import pytest


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def launcher(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    monkeypatch.setenv("VOCE_LOG_DIR", str(logs))      # inherited by the server process
    monkeypatch.setenv("ROBOT_MODE", "sim")
    import app.launcher as mod                          # no reload: would duplicate exception classes
    monkeypatch.setattr(mod, "LOG_DIR", logs)
    monkeypatch.setattr(mod, "PIDFILE", logs / "voce_server.json")
    monkeypatch.setattr(mod, "SERVER_LOG", logs / "server.log")
    yield mod
    inst = mod.running_instance()
    if inst:
        inst.stop(reason="test cleanup")


def test_start_reattach_survive_and_stop(launcher, tmp_path):
    port = free_port()
    h = launcher.start("sim", open_browser=False, port=port)
    assert h.running and h.pid != os.getpid()                    # separate process, not the caller
    again = launcher.start("sim", open_browser=False, port=port)  # e.g. Run All / cell executed twice
    assert again.pid == h.pid
    # a NEW interpreter (= restarted kernel) finds and controls the same server
    code = textwrap.dedent(f"""
        import os; os.environ['VOCE_LOG_DIR'] = {str(tmp_path / 'logs')!r}
        from app.launcher import running_instance
        i = running_instance(); print(i.pid if i else 'none')
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30).stdout.strip()
    assert out == str(h.pid)
    with pytest.raises(RuntimeError, match="already running"):
        launcher.start("ursim", open_browser=False, port=port)      # never silently switches mode
    h.stop(reason="test")
    assert not h.running and launcher.running_instance() is None
    log = (tmp_path / "logs" / "voce.jsonl").read_text()
    assert "shutdown requested: test" in log                    # every shutdown is traceable
