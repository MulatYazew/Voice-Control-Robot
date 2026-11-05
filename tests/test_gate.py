"""Safety gate — spec §8, §9, §14."""

import asyncio

from app.monitoring.events import EventBus, History, Metrics
from app.safety.gate import Gate, GateState

from .conftest import say, wait_state


async def propose_and_ask(gate, skill="indica", params=None):
    r = gate.propose(skill, params or {"posizione": "centro"})
    assert r["ok"], r
    gate.mark_question_asked(r["action_id"])
    return r["action_id"]


async def test_valid_confirmation_executes(gate):
    aid = await propose_and_ask(gate)
    await say(gate, "Sì, confermo.")
    r = await gate.confirm(aid)
    assert r["ok"] and gate.state == GateState.EXECUTING
    assert await wait_state(gate, "IDLE")
    assert gate.last_result.success and gate.world.known_pose == "centro_sopra"


async def test_invalid_and_uncertain_confirmation(gate):
    aid = await propose_and_ask(gate)
    for t in ["No.", "Forse.", "Va bene, forse.", "Aspetta.", "Non sono sicuro."]:
        await say(gate, t)
        r = await gate.confirm(aid)
        assert not r["ok"] and r["esito"] == "conferma_non_valida", t
    assert gate.state == GateState.PENDING_CONFIRMATION and gate.last_result is None


async def test_grok_cannot_self_confirm(gate):
    r = gate.propose("indica", {"posizione": "destra"})
    # same response, no question asked, no operator turn
    assert (await gate.confirm(r["action_id"]))["esito"] == "domanda_non_posta"
    gate.mark_question_asked()
    # question asked but the operator has not spoken since
    assert (await gate.confirm(r["action_id"]))["esito"] == "nessuna_risposta"


async def test_confirmation_before_question_is_rejected(gate):
    r = gate.propose("indica", {"posizione": "destra"})
    await say(gate, "sì")           # operator spoke BEFORE the question was asked
    gate.mark_question_asked()
    assert (await gate.confirm(r["action_id"]))["esito"] == "nessuna_risposta"


async def test_expired_confirmation(settings, registry, sim, clock):
    g = Gate(settings, registry, sim, EventBus(), Metrics(path=None), History(), clock=clock)
    await g.start()
    try:
        r = g.propose("indica", {"posizione": "centro"})
        g.mark_question_asked()
        clock.advance(21)  # TTL 20 s
        await say(g, "sì")
        res = await g.confirm(r["action_id"])
        assert not res["ok"] and res["esito"] in ("scaduta", "nessuna_azione_in_attesa")
        assert g.state == GateState.IDLE and g.pending is None
    finally:
        await g.shutdown()


async def test_new_proposal_invalidates_old(gate):
    old = await propose_and_ask(gate, "indica", {"posizione": "sinistra"})
    new = gate.propose("indica", {"posizione": "destra"})["action_id"]
    gate.mark_question_asked(new)
    await say(gate, "sì")
    assert (await gate.confirm(old))["esito"] == "azione_diversa"
    assert (await gate.confirm(new))["ok"]
    await wait_state(gate, "IDLE")
    assert gate.last_result.parameters == {"posizione": "destra"}


async def test_duplicate_confirmation_ignored(gate):
    aid = await propose_and_ask(gate)
    await say(gate, "procedi")
    assert (await gate.confirm(aid))["ok"]
    r2 = await gate.confirm(aid)
    assert not r2["ok"] and r2["esito"] in ("occupato", "duplicato", "nessuna_azione_in_attesa")
    await wait_state(gate, "IDLE")
    # same utterance cannot authorise a second action
    aid2 = await propose_and_ask(gate, "indica", {"posizione": "destra"})
    assert (await gate.confirm(aid2))["esito"] in ("nessuna_risposta", "duplicato")


async def test_stop_during_confirmation(gate):
    await propose_and_ask(gate)
    stopped = await say(gate, "Fermati!")
    assert stopped
    assert await wait_state(gate, "STOPPED")
    assert gate.pending is None


async def test_stop_during_execution(gate, sim):
    sim.time_scale = 1.0
    aid = await propose_and_ask(gate, "solleva_vassoio", {"posizione": "sinistra"})
    await say(gate, "sì")
    assert (await gate.confirm(aid))["ok"]
    await asyncio.sleep(0.3)
    r = await gate.stop("ui")
    assert r["ok"] and gate.state == GateState.STOPPED
    assert gate.last_stop["robot_ack"]["acknowledged"]
    await asyncio.sleep(0.2)
    assert not sim.status().moving
    assert gate.world.holding_tray is False          # sequence did not complete -> no bookkeeping change
    assert gate.last_result.outcome == "stopped"


async def test_stop_between_confirmation_and_motion_start(gate, sim):
    aid = await propose_and_ask(gate)
    await say(gate, "sì")
    q0 = sim.current_joints()
    r = await gate.confirm(aid)
    assert r["ok"]
    await gate.stop("ui")             # before the executor thread had a chance to move
    await asyncio.sleep(0.2)
    assert gate.state == GateState.STOPPED
    assert gate.last_result is None or gate.last_result.outcome == "stopped"
    assert max(abs(a - b) for a, b in zip(sim.current_joints(), q0)) < 0.05


async def test_stopped_requires_reset_and_voice_cannot_reset(gate):
    await gate.stop("ui")
    r = gate.propose("indica", {"posizione": "centro"})
    assert not r["ok"] and r["esito"] == "sistema_fermo"
    await say(gate, "ripristina il sistema")          # voice: nothing changes
    assert gate.state == GateState.STOPPED
    res = await gate.reset(operator="ui")
    assert res["ok"] and gate.state == GateState.IDLE
    assert gate.propose("indica", {"posizione": "centro"})["ok"]


async def test_fault_blocks_and_needs_reset(gate, sim):
    sim.inject = "disconnect"
    aid = await propose_and_ask(gate)
    await say(gate, "sì")
    await gate.confirm(aid)
    assert await wait_state(gate, "FAULT")
    assert gate.propose("indica", {"posizione": "centro"})["esito"] == "guasto"
    # reset reconnects and verifies the robot before re-enabling
    res = await gate.reset()
    assert res["ok"] and gate.state == GateState.IDLE


async def test_reset_refused_if_robot_not_ok(gate, sim):
    await gate.stop("ui")
    sim.fail_connect = True
    sim.connected = False
    res = await gate.reset()
    assert not res["ok"] and gate.state == GateState.STOPPED


async def test_reconnection_clears_pending(gate):
    aid = await propose_and_ask(gate)
    await gate.on_voice_disconnected("test")
    assert gate.pending is None and gate.state == GateState.IDLE
    await say(gate, "sì")
    assert (await gate.confirm(aid))["esito"] == "nessuna_azione_in_attesa"


async def test_voice_loss_while_executing_stops(gate, sim):
    sim.time_scale = 1.0
    aid = await propose_and_ask(gate, "solleva_vassoio", {"posizione": "sinistra"})
    await say(gate, "sì")
    await gate.confirm(aid)
    await asyncio.sleep(0.2)
    await gate.on_voice_disconnected("socket closed")
    assert gate.state == GateState.STOPPED


async def test_ambiguous_proposal_never_pends(gate):
    r = gate.propose("solleva_vassoio", {})
    assert r["esito"] == "richiesta_ambigua" and r["opzioni_valide"] == ["sinistra", "destra"]
    assert gate.state == GateState.IDLE and gate.pending is None


async def test_sequence_needs_confirmation_per_movement(gate):
    r = gate.propose_sequence([{"skill": "solleva_vassoio", "params": {"posizione": "sinistra"}},
                               {"skill": "appoggia_vassoio", "params": {"posizione": "centro"}}])
    assert r["ok"] and len(r["piano"]) == 2
    gate.mark_question_asked()
    await say(gate, "sì")
    assert (await gate.confirm(r["action_id"]))["ok"]
    assert await wait_state(gate, "PENDING_CONFIRMATION")   # step 2 waits for a NEW confirmation
    step2 = gate.pending
    assert step2.step_index == 1 and step2.action.skill == "appoggia_vassoio"
    # the old "sì" cannot authorise step 2
    gate.mark_question_asked()
    assert (await gate.confirm(step2.id))["esito"] in ("nessuna_risposta", "duplicato")
    await say(gate, "confermo")
    assert (await gate.confirm(step2.id))["ok"]
    assert await wait_state(gate, "IDLE")
    assert gate.world.trays_at == {"centro", "destra"} and not gate.world.holding_tray


async def test_sequence_with_invalid_step_rejected_upfront(gate):
    r = gate.propose_sequence([{"skill": "solleva_vassoio", "params": {"posizione": "sinistra"}},
                               {"skill": "appoggia_vassoio", "params": {"posizione": "destra"}}])  # destra occupied
    assert not r["ok"] and r["passo"] == 2 and gate.pending is None


async def test_robot_unavailable_blocks_confirmation(gate, sim):
    aid = await propose_and_ask(gate)
    sim.connected = False
    await say(gate, "sì")
    r = await gate.confirm(aid)
    assert not r["ok"] and r["esito"] == "robot_non_disponibile"


async def test_already_at_pose_is_not_a_movement(gate, sim):
    """2026-11-05: 'move to the right' while already at destra_sopra was sent to the robot and misreported."""
    aid = await propose_and_ask(gate, "vai_a_posa", {"posa": "destra_sopra"})
    await say(gate, "sì")
    assert (await gate.confirm(aid))["ok"]
    assert await wait_state(gate, "IDLE")
    await gate.refresh_robot()
    assert gate.world.known_pose == "destra_sopra"
    r = gate.propose("vai_a_posa", {"posa": "destra_sopra"})
    assert not r["ok"] and r["esito"] == "gia_in_posa" and "already" in r["messaggio_en"]
    assert gate.pending is None and gate.state == GateState.IDLE
    assert gate.propose("vai_a_posa", {"posa": "home"})["ok"]        # a different pose is still proposed normally
