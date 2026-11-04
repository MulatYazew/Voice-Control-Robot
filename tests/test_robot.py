"""Robot adapter behaviour (mocked/simulated, deterministic) — spec §14 Robot."""

import asyncio
import threading

import pytest

from app.robot.base import COMPLETED, NOT_STARTED, STOPPED, UNKNOWN, RobotError
from app.robot.sim import SimAdapter
from app.safety.gate import GateState

from .conftest import say, wait_state


def motion(registry, settings, skill="indica", params=None):
    from app.skills.world import WorldModel
    w = WorldModel.initial(settings.zones, settings.robot["workspace"]["trays_assumed_at"])
    return registry.validate(skill, params or {"posizione": "centro"}, w)


def test_connection_failure(sim):
    sim.fail_connect = True
    with pytest.raises(RobotError):
        sim.connect()
    a_status = sim.status()
    assert not a_status.connected and not a_status.ok_for_motion


def test_execute_not_connected_is_not_started(sim, registry, settings):
    a = motion(registry, settings)
    r = sim.execute(a.skill, a.params, a.motion, threading.Event(), 10)
    assert r.outcome == NOT_STARTED and not r.success


def test_completed_result_fields(sim, registry, settings):
    sim.connect()
    a = motion(registry, settings)
    r = sim.execute(a.skill, a.params, a.motion, threading.Event(), 10)
    assert r.outcome == COMPLETED and r.success
    d = r.to_dict()
    for k in ("success", "skill", "parameters", "start_time", "end_time", "duration", "final_robot_state", "error"):
        assert k in d
    assert r.steps_completed == r.steps_total == 3


def test_interruptible_and_stop_feedback(sim, registry, settings):
    sim.connect()
    sim.time_scale = 1.0
    a = motion(registry, settings, "solleva_vassoio", {"posizione": "sinistra"})
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", sim.execute(a.skill, a.params, a.motion,
                                                                       threading.Event(), 30)))
    t.start()
    import time
    time.sleep(0.3)
    ack = sim.stop()
    t.join(2)
    assert out["r"].outcome == STOPPED and ack.acknowledged and ack.motion_stopped_at is not None
    assert not sim.status().moving


async def test_disconnect_during_motion_is_fault_unknown(gate, sim):
    sim.inject = "disconnect"
    r = gate.propose("indica", {"posizione": "centro"})
    gate.mark_question_asked()
    await say(gate, "sì")
    await gate.confirm(r["action_id"])
    assert await wait_state(gate, "FAULT")
    assert gate.last_result.outcome == UNKNOWN


async def test_execution_timeout_is_fault(gate, sim, settings):
    sim.inject = "hang"
    gate.reg.skills["indica"].timeout_s = 0.2
    settings.robot["safety"]["execution_timeout_margin_s"] = 0.3
    r = gate.propose("indica", {"posizione": "centro"})
    gate.mark_question_asked()
    await say(gate, "sì")
    await gate.confirm(r["action_id"])
    assert await wait_state(gate, "FAULT", 3)
    assert "timeout" in gate.fault_reason


async def test_no_automatic_retry(gate, sim):
    calls = []
    orig = sim.execute

    def spy(*a, **k):
        calls.append(1)
        return orig(*a, **k)

    sim.execute = spy
    sim.inject = "protective_stop"
    r = gate.propose("indica", {"posizione": "centro"})
    gate.mark_question_asked()
    await say(gate, "sì")
    await gate.confirm(r["action_id"])
    assert await wait_state(gate, "FAULT")
    await asyncio.sleep(0.5)
    assert len(calls) == 1 and gate.state == GateState.FAULT


async def test_stop_works_when_robot_stop_raises(gate, sim):
    def boom():
        raise RuntimeError("controller unreachable")
    sim.stop = boom
    r = await gate.stop("ui")
    assert r["ok"] and gate.state == GateState.STOPPED
    assert gate.last_stop["robot_ack"]["acknowledged"] is False
