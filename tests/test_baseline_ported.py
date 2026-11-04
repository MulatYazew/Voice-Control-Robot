"""The 26 baseline tests (tests/test_safety.py @ 2026-11-04, robot_voice/), ported 1:1 by intent to the
new modules. Where the spec is stricter than the baseline, the stricter behaviour is asserted and noted."""

import asyncio

import pytest

from app.safety.gate import GateState
from app.safety.phrases import ABSENT, NEGATIVE, VALID, confirmation_verdict, is_stop
from app.skills.registry import PRECONDIZIONE, RICHIESTA_AMBIGUA, SKILL_SCONOSCIUTA, SkillError
from app.skills.world import WorldModel

from .conftest import say, wait_state


# baseline: test_stop_words (7)
@pytest.mark.parametrize("t", ["Stop!", "ferma", "Fermati subito", "basta così", "ALT", "halt", "Arrête"])
def test_stop_words(t):
    assert is_stop(t)


# baseline: test_not_stop (3)
@pytest.mark.parametrize("t", ["solleva il vassoio", "porta a destra", "stoppino"])
def test_not_stop(t):
    assert not is_stop(t)


# baseline: test_polarity (9). Baseline "unclear" -> now NEGATIVE (any negation blocks); "neutral"/"unknown" -> ABSENT.
@pytest.mark.parametrize("t,v", [("Sì, procedi", VALID), ("ok", VALID), ("confermo", VALID), ("yes go", VALID),
                                 ("no", NEGATIVE), ("annulla", NEGATIVE), ("no aspetta... sì", NEGATIVE),
                                 ("il vassoio blu", ABSENT), (None, ABSENT)])
def test_polarity(t, v):
    assert confirmation_verdict(t) == v


def _w(settings):
    return WorldModel.initial(settings.zones, settings.robot["workspace"]["trays_assumed_at"])


# baseline: test_ambiguous_tray
def test_ambiguous_tray(registry, settings):
    with pytest.raises(SkillError) as e:
        registry.validate("solleva_vassoio", {}, _w(settings))
    assert e.value.code == RICHIESTA_AMBIGUA and set(e.value.options) == {"sinistra", "destra"}


# baseline: test_sequence_and_implicit_slot (stricter now: a missing slot is never filled in)
async def test_sequence_and_implicit_slot(gate):
    r = gate.propose_sequence([{"skill": "solleva_vassoio", "params": {"posizione": "sinistra"}},
                               {"skill": "appoggia_vassoio", "params": {}}])
    assert not r["ok"] and r["esito"] == RICHIESTA_AMBIGUA and r["passo"] == 2
    r = gate.propose_sequence([{"skill": "solleva_vassoio", "params": {"posizione": "sinistra"}},
                               {"skill": "vai_a_posa", "params": {"posa": "destra_sopra"}},
                               {"skill": "appoggia_vassoio", "params": {"posizione": "centrale"}}])
    assert r["ok"] and r["piano"][2]["parametri"] == {"posizione": "centro"}
    assert not gate.world.holding_tray and "sinistra" in gate.world.trays_at   # dry run did not mutate


# baseline: test_invalid
def test_invalid(registry, settings):
    with pytest.raises(SkillError) as e:
        registry.validate("appoggia_vassoio", {"posizione": "centro"}, _w(settings))   # nothing on the fork
    assert e.value.code == PRECONDIZIONE
    with pytest.raises(SkillError) as e:
        registry.validate("fly", {}, _w(settings))
    assert e.value.code == SKILL_SCONOSCIUTA


# baseline: test_macro (custom skills by voice) -> spec P2: voice-recorded named poses
async def test_macro(gate, settings):
    r = await gate.save_pose("posa prova")
    assert r["ok"] and r["nome"] == "posa_prova"
    assert "posa_prova" in gate.reg.selectable_poses()
    assert (settings.data_dir / "user_poses.yaml").exists()            # persisted
    assert not (await gate.save_pose("home"))["ok"]                     # cannot overwrite a configured pose
    assert gate.propose("vai_a_posa", {"posa": "posa prova"})["ok"]


# baseline: test_no_motion_without_new_turn
async def test_no_motion_without_new_turn(gate):
    await say(gate, "solleva il vassoio a sinistra")
    p = gate.propose("solleva_vassoio", {"posizione": "sinistra"})
    gate.mark_question_asked()
    r = await gate.confirm(p["action_id"])            # model tries to execute in the same turn
    assert not r["ok"] and gate.state == GateState.PENDING_CONFIRMATION
    await say(gate, "no")
    assert not (await gate.confirm(p["action_id"]))["ok"]
    await say(gate, "sì, procedi")
    assert (await gate.confirm(p["action_id"]))["ok"]
    assert await wait_state(gate, "IDLE")
    assert gate.world.holding_tray


# baseline: test_stop_halts_and_reports
async def test_stop_halts_and_reports(gate, sim):
    sim.time_scale = 1.0
    reports = []
    gate.on_report(lambda ev: reports.append(ev))
    p = gate.propose_sequence([{"skill": "vai_a_posa", "params": {"posa": "sinistra_sopra"}},
                               {"skill": "vai_a_posa", "params": {"posa": "destra_sopra"}}])
    gate.mark_question_asked()
    await say(gate, "ok")
    assert (await gate.confirm(p["action_id"]))["ok"]
    await asyncio.sleep(0.4)
    q_before = sim.current_joints()
    assert gate.note_transcript("ferma!")             # voice keyword, no LLM involved
    await asyncio.sleep(0.3)
    assert gate.state == GateState.STOPPED and not sim.status().moving
    assert max(abs(a - b) for a, b in zip(sim.current_joints(), q_before)) < 0.1
    assert any(r["kind"] == "fermato" for r in reports)
    assert gate.pending is None                        # second step of the sequence was dropped


# baseline: test_stop_cancels_pending_and_busy_refuses
async def test_stop_cancels_pending_and_busy_refuses(gate, sim):
    p = gate.propose("spingi", {"posizione": "destra", "direzione": "sinistra"})
    await gate.stop("ui")
    assert gate.pending is None
    await say(gate, "sì")
    assert not (await gate.confirm(p["action_id"]))["ok"]
    assert (await gate.reset())["ok"]                  # new: STOP latches until operator reset
    sim.time_scale = 1.0
    p = gate.propose("spingi", {"posizione": "destra", "direzione": "sinistra"})
    gate.mark_question_asked()
    await say(gate, "confermo")
    assert (await gate.confirm(p["action_id"]))["ok"]
    assert gate.propose("vai_a_posa", {"posa": "home"})["esito"] == "occupato"
    await gate.stop("ui")
