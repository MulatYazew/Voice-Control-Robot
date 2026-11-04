"""URAdapter: Universal Robots e-Series (URSim or real), pure Python.

Interfaces used (official UR client interfaces; see PROJECT_STATUS.md for the
documentation each behaviour was checked against):
  * RTDE        :30004  feedback at 125 Hz — actual_q, actual_qd, robot_mode, safety_status, runtime_state
  * Secondary   :30002  receives one fixed URScript program per skill (movej/movel over recorded joints)
  * Dashboard   :29999  'stop', 'robotmode', 'safetystatus', 'programState', 'is in remote control'

Motion completion = RTDE runtime_state back to STOPPED, |qd| ≈ 0, and actual_q at the last target.
Anything else (no feedback, timeout, safety event) is reported as FAILED/UNKNOWN, never as success.
Grok never reaches this module: it only receives validated MotionStep lists from the registry.
"""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Any, Sequence

from ..config import Settings
from ..skills.registry import MotionStep
from .base import (COMPLETED, FAILED, NOT_STARTED, STOPPED, UNKNOWN, RobotError, RobotStatus, StopAck, _Listeners,
                   result)

log = logging.getLogger("voce.ur")

ROBOT_MODES = {-1: "NO_CONTROLLER", 0: "DISCONNECTED", 1: "CONFIRM_SAFETY", 2: "BOOTING", 3: "POWER_OFF",
               4: "POWER_ON", 5: "IDLE", 6: "BACKDRIVE", 7: "RUNNING", 8: "UPDATING_FIRMWARE"}
SAFETY_STATUS = {1: "NORMAL", 2: "REDUCED", 3: "PROTECTIVE_STOP", 4: "RECOVERY", 5: "SAFEGUARD_STOP",
                 6: "SYSTEM_EMERGENCY_STOP", 7: "ROBOT_EMERGENCY_STOP", 8: "VIOLATION", 9: "FAULT",
                 10: "VALIDATE_JOINT_ID", 11: "UNDEFINED_SAFETY_MODE", 12: "AUTOMATIC_MODE_SAFEGUARD_STOP",
                 13: "SYSTEM_THREE_POSITION_ENABLING_STOP"}
RUNTIME = {0: "STOPPING", 1: "STOPPED", 2: "RUNNING", 3: "PAUSING", 4: "PAUSED", 5: "RESUMING", 6: "RETRACTING"}

_FMT = {"DOUBLE": "d", "VECTOR6D": "6d", "VECTOR3D": "3d", "INT32": "i", "UINT32": "I", "UINT64": "Q", "BOOL": "?",
        "UINT8": "B", "VECTOR6INT32": "6i", "VECTOR6UINT32": "6I"}


# ------------------------------------------------------------------ RTDE ---
class RTDEReader:
    """Minimal RTDE v2 output-only client (protocol per UR's RTDE guide)."""

    FIELDS = ["timestamp", "actual_q", "actual_qd", "robot_mode", "safety_status", "runtime_state"]

    def __init__(self, host: str, port: int = 30004, freq: float = 125.0) -> None:
        self.host, self.port, self.freq = host, port, freq
        self.sock: socket.socket | None = None
        self.latest: dict[str, Any] = {}
        self.last_rx = 0.0
        self.connected = False
        self.error: str | None = None
        self._thread: threading.Thread | None = None
        self._run = False
        self.on_data = None

    def _send(self, ptype: str, payload: bytes = b"") -> None:
        self.sock.sendall(struct.pack(">HB", 3 + len(payload), ord(ptype)) + payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("RTDE socket closed")
            buf += chunk
        return buf

    def _recv(self) -> tuple[str, bytes]:
        size, ptype = struct.unpack(">HB", self._recv_exact(3))
        return chr(ptype), self._recv_exact(size - 3)

    def _expect(self, ptype: str) -> bytes:
        for _ in range(50):
            t, p = self._recv()
            if t == ptype:
                return p
            if t == "M":
                log.info("RTDE text message: %r", p)
        raise ConnectionError(f"RTDE: no {ptype!r} reply")

    def connect(self, timeout: float = 3.0) -> None:
        self.sock = socket.create_connection((self.host, self.port), timeout=timeout)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._send("V", struct.pack(">H", 2))
        if not struct.unpack(">B", self._expect("V"))[0]:
            raise ConnectionError("RTDE protocol v2 not accepted")
        fields = list(self.FIELDS)
        self._send("O", struct.pack(">d", self.freq) + ",".join(fields).encode())
        p = self._expect("O")
        recipe = p[0]
        types = p[1:].decode().split(",")
        if "NOT_FOUND" in types:  # older controllers: no safety_status -> use safety_mode
            fields = [f if f != "safety_status" else "safety_mode" for f in fields]
            self._send("O", struct.pack(">d", self.freq) + ",".join(fields).encode())
            p = self._expect("O")
            recipe, types = p[0], p[1:].decode().split(",")
            if "NOT_FOUND" in types:
                raise ConnectionError(f"RTDE fields not available: {dict(zip(fields, types))}")
        self.recipe = recipe
        self.names = fields
        self.fmt = ">B" + "".join(_FMT[t] for t in types)
        self._send("S")
        if not struct.unpack(">B", self._expect("S"))[0]:
            raise ConnectionError("RTDE start refused")
        self.sock.settimeout(1.0)
        self.connected = True
        self.error = None
        self._run = True
        self._thread = threading.Thread(target=self._loop, name="rtde", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        try:
            while self._run:
                t, p = self._recv()
                if t != "U":
                    continue
                vals = struct.unpack(self.fmt, p)
                out: dict[str, Any] = {}
                i = 1
                for name in self.names:
                    if name in ("actual_q", "actual_qd"):
                        out[name] = list(vals[i:i + 6])
                        i += 6
                    else:
                        out["safety_status" if name == "safety_mode" else name] = vals[i]
                        i += 1
                self.latest = out
                self.last_rx = time.monotonic()
                if self.on_data:
                    self.on_data(out)
        except Exception as e:
            self.error = f"RTDE: {e}"
        finally:
            self.connected = False

    def fresh(self, max_age: float = 0.5) -> bool:
        return self.connected and time.monotonic() - self.last_rx < max_age

    def close(self) -> None:
        self._run = False
        try:
            if self.sock:
                self._send("P")
                self.sock.close()
        except OSError:
            pass
        self.connected = False


# ------------------------------------------------------------- Dashboard ---
class Dashboard:
    def __init__(self, host: str, port: int = 29999, timeout: float = 3.0) -> None:
        self.host, self.port, self.timeout = host, port, timeout
        self._lock = threading.Lock()
        self.sock: socket.socket | None = None

    def _connect(self) -> socket.socket:
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        s.recv(1024)  # "Connected: Universal Robots Dashboard Server"
        return s

    def cmd(self, command: str, *, fresh: bool = False) -> str:
        """Send one dashboard command. fresh=True uses a separate connection (for STOP)."""
        if fresh:
            s = self._connect()
            try:
                s.sendall((command + "\n").encode())
                return s.recv(4096).decode(errors="replace").strip()
            finally:
                s.close()
        with self._lock:
            for attempt in (1, 2):
                try:
                    if self.sock is None:
                        self.sock = self._connect()
                    self.sock.sendall((command + "\n").encode())
                    return self.sock.recv(4096).decode(errors="replace").strip()
                except OSError:
                    if self.sock:
                        self.sock.close()
                    self.sock = None
                    if attempt == 2:
                        raise
        return ""

    def close(self) -> None:
        with self._lock:
            if self.sock:
                try:
                    self.sock.sendall(b"quit\n")
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None


# -------------------------------------------------------------- adapter ---
def urscript(motion: Sequence[MotionStep], name: str = "voce_skill") -> str:
    """Fixed program from validated steps only. Numbers are formatted from floats — no text from the conversation."""
    lines = [f"def {name}():"]
    for m in motion:
        if m.move == "wait":
            lines.append(f"  sleep({float(m.seconds):.3f})")
            continue
        q = ", ".join(f"{float(x):.5f}" for x in m.joints)
        fn = "movej" if m.move == "movej" else "movel"   # movel with a joint vector: FK computes the target pose
        lines.append(f"  {fn}([{q}], a={float(m.accel):.3f}, v={float(m.speed):.3f})")
    lines.append("end")
    return "\n".join(lines) + "\n"


class URAdapter:
    name = "ursim"

    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.name = settings.mode  # "ursim" | "real"
        c = settings.robot["connections"]
        self.host = settings.robot_host
        if not self.host:
            raise RobotError("robot host not configured")
        self.ports = c["ports"]
        self.timeout = float(c.get("connect_timeout_s", 3.0))
        m = settings.robot["motion"]
        self.tol = float(m["joint_tolerance_rad"])
        self.eps = float(m["moving_speed_eps"])
        self.stop_decel = float(m.get("stop_decel", 3.0))
        self.rtde = RTDEReader(self.host, int(self.ports["rtde"]))
        self.dash = Dashboard(self.host, int(self.ports["dashboard"]), self.timeout)
        self.listeners = _Listeners()
        self.progress_cb = None
        self._last_emit = 0.0
        self._executing = False
        self._remote: bool | None = None
        self._remote_t = 0.0
        self._dash_t = 0.0
        self._dash_cache: tuple[str | None, str | None] = (None, None)
        self._dash_ok: bool | None = None
        self.rtde.on_data = self._on_data

    def _on_data(self, d: dict[str, Any]) -> None:
        now = time.monotonic()
        if now - self._last_emit > 0.05:
            self._last_emit = now
            self.listeners.emit(d.get("actual_q") or [])

    # ------------------------------------------------------------ protocol ---
    def connect(self) -> None:
        try:
            if not self.rtde.connected:
                self.rtde = RTDEReader(self.host, int(self.ports["rtde"]))
                self.rtde.on_data = self._on_data
                self.rtde.connect(self.timeout)
            end = time.monotonic() + self.timeout
            while not self.rtde.fresh() and time.monotonic() < end:
                time.sleep(0.02)
            if not self.rtde.fresh():
                raise RobotError("RTDE connected but no data")
            self._dash_ok = None
            self._dash_state(0)   # probes the Dashboard; falls back to RTDE if it is not reachable
        except (OSError, ConnectionError) as e:
            raise RobotError(f"{self.host}: {e}") from e

    def disconnect(self) -> None:
        self.rtde.close()
        self.dash.close()

    def current_joints(self) -> list[float] | None:
        return list(self.rtde.latest["actual_q"]) if self.rtde.fresh() else None

    def _moving(self, d: dict[str, Any]) -> bool:
        return any(abs(v) > self.eps for v in d.get("actual_qd", [0]))

    def _dash_state(self, max_age: float = 1.0) -> tuple[str | None, str | None, bool | None]:
        """Robot mode + safety from the Dashboard Server (documented text replies, cached ~1 s).
        Joints / speeds / program state come from RTDE."""
        now = time.monotonic()
        if self._dash_ok is not False and now - self._dash_t > max_age:
            try:
                mode = self.dash.cmd("robotmode").split(":", 1)[-1].strip()
                safety = self.dash.cmd("safetystatus").split(":", 1)[-1].strip()
                if now - self._remote_t > 5:
                    self._remote = self.dash.cmd("is in remote control").lower().startswith("true")
                    self._remote_t = now
                self._dash_cache = (mode or None, safety or None)
                self._dash_ok = True
            except OSError:
                self._dash_ok = False   # e.g. UR's URSim.app does not forward port 29999
                log.warning("Dashboard Server unreachable: robot mode/safety from RTDE instead")
            self._dash_t = now
        if self._dash_ok is False:      # RTDE fallback (mapping verified against URSim 5.26.3)
            d = self.rtde.latest
            return (ROBOT_MODES.get(d.get("robot_mode")), SAFETY_STATUS.get(d.get("safety_status")), None)
        return self._dash_cache[0], self._dash_cache[1], self._remote

    def status(self) -> RobotStatus:
        if not self.rtde.fresh():
            return RobotStatus(connected=False, moving=False, detail=self.rtde.error or "nessun dato RTDE")
        d = self.rtde.latest
        mode, safety, remote = self._dash_state()
        return RobotStatus(connected=True, moving=self._moving(d) or d.get("runtime_state") == 2,
                           robot_mode=mode, safety=safety,
                           program=RUNTIME.get(d.get("runtime_state"), str(d.get("runtime_state"))),
                           remote_control=remote, joints=[round(x, 5) for x in d["actual_q"]],
                           detail=f"{self.name}@{self.host}")

    def _send_script(self, script: str) -> None:
        with socket.create_connection((self.host, int(self.ports["secondary"])), timeout=self.timeout) as s:
            s.sendall(script.encode())

    def stop(self) -> StopAck:
        t0 = time.time()
        detail = []
        ok = False
        try:   # aborts the running secondary program and decelerates
            self._send_script(f"def voce_stop():\n  stopj({self.stop_decel:.2f})\nend\n")
            ok = True
            detail.append("stopj inviato")
        except OSError as e:
            detail.append(f"stopj fallito: {e}")
        if self._dash_ok is not False:
            try:
                r = self.dash.cmd("stop", fresh=True)
                detail.append(f"dashboard: {r}")
                ok = ok or r.lower().startswith("stopped")
            except OSError as e:
                detail.append(f"dashboard non disponibile: {e}")
        t_ack = time.time()
        stopped_at = None
        end = time.monotonic() + 3.0
        while time.monotonic() < end:
            d = self.rtde.latest if self.rtde.fresh() else None
            if d and not self._moving(d) and d.get("runtime_state") != 2:
                stopped_at = time.time()
                break
            time.sleep(0.008)
        return StopAck(requested_at=t0, acknowledged_at=t_ack, acknowledged=ok, motion_stopped_at=stopped_at,
                       detail="; ".join(detail))

    def execute(self, skill, params, motion, cancel: threading.Event, timeout_s: float):
        t0 = time.time()
        targets = [m.joints for m in motion if m.joints]
        total = len(targets)

        def state() -> dict[str, Any]:
            st = self.status()
            return {"joints": st.joints, "moving": st.moving, "safety": st.safety, "program": st.program,
                    "robot_mode": st.robot_mode}

        st = self.status()
        if not st.connected:
            return result(skill, params, t0, NOT_STARTED, state(), error="robot non connesso (RTDE)", total=total)
        if not st.ok_for_motion:
            return result(skill, params, t0, NOT_STARTED, state(),
                          error=f"robot non pronto: modo {st.robot_mode}, sicurezza {st.safety}", total=total)
        if cancel.is_set():
            return result(skill, params, t0, STOPPED, state(), error="fermato prima del movimento", total=total)
        try:
            self._send_script(urscript(motion, f"voce_{skill}"))
        except OSError as e:
            return result(skill, params, t0, NOT_STARTED, state(), error=f"invio programma fallito: {e}", total=total)

        self._executing = True
        try:
            # 1. the controller must start running the program
            started = False
            end = time.monotonic() + 3.0
            while time.monotonic() < end:
                if cancel.is_set():
                    return result(skill, params, t0, STOPPED, state(), error="fermato", total=total)
                d = self.rtde.latest
                if self.rtde.fresh() and (d.get("runtime_state") == 2 or self._moving(d)):
                    started = True
                    break
                time.sleep(0.008)
            if not started:
                return result(skill, params, t0, NOT_STARTED, state(),
                              error="il controller non ha avviato il programma (Remote Control? modalità?)", total=total)
            # 2. follow progress until the program ends
            done = 0
            deadline = time.monotonic() + timeout_s
            while True:
                if cancel.is_set():
                    return result(skill, params, t0, STOPPED, state(), error="fermato", done=done, total=total)
                if not self.rtde.fresh(1.0):
                    return result(skill, params, t0, UNKNOWN, {"joints": None},
                                  error="feedback RTDE perso durante il movimento", done=done, total=total)
                d = self.rtde.latest
                q = d["actual_q"]
                if done < total and all(abs(a - b) <= self.tol for a, b in zip(q, targets[done])):
                    done += 1
                    if self.progress_cb:
                        self.progress_cb(done, total, None)
                _, safety, _ = self._dash_state(0.3)
                if safety is not None and safety not in ("NORMAL", "REDUCED"):
                    return result(skill, params, t0, FAILED, state(), error=f"evento di sicurezza: {safety}",
                                  done=done, total=total)
                if d.get("runtime_state") == 1 and not self._moving(d):
                    break
                if time.monotonic() > deadline:
                    return result(skill, params, t0, UNKNOWN, state(), error="timeout del movimento", done=done,
                                  total=total)
                time.sleep(0.008)
            q = self.rtde.latest["actual_q"]
            if all(abs(a - b) <= self.tol for a, b in zip(q, targets[-1])):
                return result(skill, params, t0, COMPLETED, state(), done=total, total=total)
            return result(skill, params, t0, FAILED, state(), error="programma terminato fuori dal target finale",
                          done=done, total=total)
        finally:
            self._executing = False
