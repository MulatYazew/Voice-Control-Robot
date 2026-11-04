"""Common robot adapter interface (sim, URSim, real UR)."""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Protocol, Sequence

from ..skills.registry import MotionStep

# execution outcomes
COMPLETED = "completed"        # every motion step reached its target per feedback
STOPPED = "stopped"            # interrupted by STOP; robot halted
NOT_STARTED = "not_started"    # refused/failed before any motion command was sent
FAILED = "failed"              # known failure (e.g. protective stop) after motion began
UNKNOWN = "unknown"            # outcome cannot be determined (timeout, comms loss) -> FAULT


class RobotError(Exception):
    pass


@dataclass
class RobotStatus:
    connected: bool
    moving: bool
    robot_mode: str | None = None
    safety: str | None = None
    program: str | None = None
    remote_control: bool | None = None
    joints: list[float] | None = None
    detail: str = ""

    @property
    def ok_for_motion(self) -> bool:
        return (self.connected and not self.moving and self.safety in (None, "NORMAL", "REDUCED")
                and self.robot_mode in (None, "RUNNING"))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["ok_for_motion"] = self.ok_for_motion
        return d


@dataclass
class ExecutionResult:
    success: bool
    skill: str
    parameters: dict[str, str]
    start_time: float
    end_time: float
    duration: float
    final_robot_state: dict[str, Any]
    error: str | None
    outcome: str
    steps_completed: int = 0
    steps_total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StopAck:
    requested_at: float
    acknowledged_at: float
    acknowledged: bool
    motion_stopped_at: float | None = None   # when feedback showed the robot not moving
    detail: str = ""

    @property
    def ack_latency_ms(self) -> float:
        return round((self.acknowledged_at - self.requested_at) * 1000, 1)

    @property
    def halt_latency_ms(self) -> float | None:
        return round((self.motion_stopped_at - self.requested_at) * 1000, 1) if self.motion_stopped_at else None

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ack_latency_ms": self.ack_latency_ms, "halt_latency_ms": self.halt_latency_ms}


class RobotAdapter(Protocol):
    name: str

    def connect(self) -> None: ...

    def status(self) -> RobotStatus: ...

    def execute(self, skill: str, params: dict[str, str], motion: Sequence[MotionStep],
                cancel: threading.Event, timeout_s: float) -> ExecutionResult: ...

    def stop(self) -> StopAck: ...

    def current_joints(self) -> list[float] | None: ...

    def disconnect(self) -> None: ...


@dataclass
class _Listeners:
    cbs: list[Callable[[list[float]], None]] = field(default_factory=list)

    def emit(self, joints: list[float]) -> None:
        for cb in list(self.cbs):
            try:
                cb(joints)
            except Exception:
                pass


def result(skill: str, params: dict[str, str], t0: float, outcome: str, state: dict[str, Any], *,
           error: str | None = None, done: int = 0, total: int = 0) -> ExecutionResult:
    t1 = time.time()
    return ExecutionResult(success=outcome == COMPLETED, skill=skill, parameters=dict(params), start_time=t0,
                           end_time=t1, duration=round(t1 - t0, 3), final_robot_state=state, error=error,
                           outcome=outcome, steps_completed=done, steps_total=total)
