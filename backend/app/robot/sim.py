"""SimAdapter: in-process joint-space simulator (ROBOT_MODE=sim, and tests).

Moves through the same recorded joint targets as URSim/real, at the configured
speeds, and is interruptible every tick. Has fault-injection hooks for tests.
"""

from __future__ import annotations

import threading
import time
from typing import Sequence

from ..skills.registry import MotionStep
from .base import (COMPLETED, FAILED, NOT_STARTED, STOPPED, UNKNOWN, RobotError, RobotStatus, StopAck, _Listeners,
                   result)


class SimAdapter:
    name = "sim"
    TICK = 0.02

    def __init__(self, start_joints: Sequence[float], *, time_scale: float = 1.0) -> None:
        self._q = [float(x) for x in start_joints]
        self._lock = threading.Lock()
        self._moving = False
        self._stop_evt = threading.Event()
        self.connected = False
        self.time_scale = time_scale     # >1 = faster (tests)
        self.listeners = _Listeners()
        self.progress_cb = None          # (done, total, pose) -> None, set by the gate
        # fault injection (tests)
        self.fail_connect = False
        self.inject: str | None = None   # "disconnect" | "hang" | "protective_stop" | "refuse"
        self.inject_after_steps = 0

    # ------------------------------------------------------------ protocol ---
    def connect(self) -> None:
        if self.fail_connect:
            raise RobotError("simulated connection failure")
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def current_joints(self) -> list[float] | None:
        with self._lock:
            return list(self._q) if self.connected else None

    def status(self) -> RobotStatus:
        return RobotStatus(connected=self.connected, moving=self._moving,
                           robot_mode="RUNNING" if self.connected else None,
                           safety="NORMAL" if self.connected else None, program="PLAYING" if self._moving else "STOPPED",
                           joints=self.current_joints(), detail="simulatore interno")

    def stop(self) -> StopAck:
        t0 = time.time()
        self._stop_evt.set()
        # sim halts on the next tick; report when it actually stopped
        deadline = time.time() + 1.0
        while self._moving and time.time() < deadline:
            time.sleep(0.002)
        t1 = time.time()
        return StopAck(requested_at=t0, acknowledged_at=t1, acknowledged=True,
                       motion_stopped_at=t1 if not self._moving else None, detail="sim: halted")

    def execute(self, skill, params, motion, cancel: threading.Event, timeout_s: float):
        res = self._execute(skill, params, motion, cancel, timeout_s)
        res.final_robot_state = {"joints": self.current_joints(), "moving": self._moving}
        return res

    def _execute(self, skill, params, motion, cancel: threading.Event, timeout_s: float):
        t0 = time.time()
        total = sum(1 for m in motion if m.move != "wait")
        state = lambda: {"joints": self.current_joints(), "moving": self._moving}  # noqa: E731
        if not self.connected:
            return result(skill, params, t0, NOT_STARTED, state(), error="robot non connesso", total=total)
        if self.inject == "refuse":
            self.inject = None
            return result(skill, params, t0, NOT_STARTED, state(), error="comando rifiutato (simulato)", total=total)
        self._stop_evt.clear()
        if cancel.is_set():  # STOP arrived between confirmation and motion start
            return result(skill, params, t0, STOPPED, state(), error="fermato prima del movimento", total=total)
        done = 0
        self._moving = True
        try:
            for step in motion:
                if step.move == "wait":
                    end = time.time() + step.seconds / self.time_scale
                    while time.time() < end:
                        if cancel.is_set() or self._stop_evt.is_set():
                            return result(skill, params, t0, STOPPED, state(), error="fermato", done=done, total=total)
                        time.sleep(self.TICK)
                    continue
                if self.inject and done >= self.inject_after_steps:
                    kind, self.inject = self.inject, None
                    if kind == "disconnect":
                        self.connected = False
                        return result(skill, params, t0, UNKNOWN, state(), error="connessione persa durante il movimento",
                                      done=done, total=total)
                    if kind == "protective_stop":
                        return result(skill, params, t0, FAILED, state(), error="arresto protettivo (simulato)",
                                      done=done, total=total)
                    if kind == "hang":
                        while not (cancel.is_set() or self._stop_evt.is_set()):
                            time.sleep(self.TICK)
                        return result(skill, params, t0, STOPPED, state(), error="fermato", done=done, total=total)
                if not self._move(step, cancel):
                    return result(skill, params, t0, STOPPED, state(), error="fermato", done=done, total=total)
                done += 1
                if self.progress_cb:
                    self.progress_cb(done, total, step.pose)
            return result(skill, params, t0, COMPLETED, state(), done=done, total=total)
        finally:
            self._moving = False
            self.listeners.emit(self.current_joints() or [])

    # ------------------------------------------------------------- motion ---
    def _move(self, step: MotionStep, cancel: threading.Event) -> bool:
        start = list(self._q)
        target = list(step.joints or start)
        span = max(abs(b - a) for a, b in zip(start, target))
        # movel speed is m/s; the sim has no kinematics, so use a joint-speed equivalent for both
        speed = max(step.speed if step.move == "movej" else step.speed * 4, 1e-3)
        dur = max(span / speed, 0.15) / self.time_scale
        t0 = time.time()
        while True:
            if cancel.is_set() or self._stop_evt.is_set():
                return False
            f = min((time.time() - t0) / dur, 1.0)
            e = f * f * (3 - 2 * f)
            with self._lock:
                self._q = [a + (b - a) * e for a, b in zip(start, target)]
            self.listeners.emit(list(self._q))
            if f >= 1.0:
                return True
            time.sleep(self.TICK)
