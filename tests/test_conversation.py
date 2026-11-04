"""Text-mode conversation with the deterministic rules (spec §14 Conversation)."""

import asyncio

import pytest

from app.conversation.rules import parse
from app.conversation.text_mode import TEXT_MODE_LABEL, TextConversation
from app.safety.gate import GateState

from .conftest import wait_state


@pytest.mark.parametrize("text,skill,params", [
    ("Solleva il vassoio a sinistra.", "solleva_vassoio", {"posizione": "sinistra"}),
    ("Potresti indicarmi la zona centrale?", "indica", {"posizione": "centro"}),
    ("tira su il vassoio di destra per favore", "solleva_vassoio", {"posizione": "destra"}),
    ("A sinistra, solleva il vassoio", "solleva_vassoio", {"posizione": "sinistra"}),
    ("porta il braccio a casa", "vai_a_posa", {"posa": "home"}),
    ("torna in posizione di attesa", "vai_a_posa", {"posa": "attesa"}),
    ("spingi il vassoio di destra verso sinistra", "spingi", {"posizione": "destra", "direzione": "sinistra"}),
    ("appoggialo al centro", "appoggia_vassoio", {"posizione": "centro"}),
    ("mostrami dov'è la zona destra", "indica", {"posizione": "destra"}),
])
def test_paraphrases(settings, text, skill, params):
    i = parse(text, poses=settings.poses)
    assert i.kind == "skill" and i.skill == skill and i.params == params


@pytest.mark.parametrize("text", ["Disegna un cerchio.", "Che ore sono?", "fai una foto", "aumenta la velocità"])
def test_out_of_scope(settings, text):
    assert parse(text, poses=settings.poses).kind == "out_of_scope"


async def test_text_mode_full_flow(gate):
    tc = TextConversation(gate)
    r = await tc.handle("Solleva il vassoio a sinistra.")
    assert r["mode"] == TEXT_MODE_LABEL and gate.state == GateState.PENDING_CONFIRMATION
    assert "Confermi" in r["reply"]
    r = await tc.handle("Sì, confermo.")
    assert r["result"]["ok"]
    assert await wait_state(gate, "IDLE")
    assert gate.world.holding_tray and "sinistra" not in gate.world.trays_at


async def test_clarification_then_answer(gate):
    tc = TextConversation(gate)
    r = await tc.handle("Solleva il vassoio.")
    assert r["result"]["esito"] == "richiesta_ambigua" and gate.pending is None
    assert "sinistra" in r["reply"] and "destra" in r["reply"]
    r = await tc.handle("quello a destra")
    assert gate.pending and gate.pending.action.params == {"posizione": "destra"}


async def test_vague_request_asks(gate):
    tc = TextConversation(gate)
    r = await tc.handle("Spostalo dall'altra parte.")
    assert r["intent"]["kind"] == "clarify" and gate.pending is None and gate.state == GateState.IDLE


async def test_out_of_scope_no_motion(gate):
    tc = TextConversation(gate)
    r = await tc.handle("Disegna un cerchio.")
    assert "Non posso" in r["reply"] and gate.pending is None and gate.last_result is None


async def test_contextual_correction(gate):
    tc = TextConversation(gate)
    await tc.handle("indica la zona sinistra")
    first = gate.pending.id
    await tc.handle("no, indica la destra")
    assert gate.pending and gate.pending.id != first and gate.pending.action.params == {"posizione": "destra"}


async def test_cancellation(gate):
    tc = TextConversation(gate)
    await tc.handle("indica il centro")
    r = await tc.handle("annulla")
    assert gate.pending is None and gate.state == GateState.IDLE and "Annullato" in r["reply"]


async def test_uncertain_confirmation_does_not_move(gate):
    tc = TextConversation(gate)
    await tc.handle("indica il centro")
    r = await tc.handle("forse")
    assert gate.state == GateState.PENDING_CONFIRMATION and gate.last_result is None
    assert "esplicita" in r["reply"]


async def test_text_stop(gate):
    tc = TextConversation(gate)
    await tc.handle("indica il centro")
    await tc.handle("FERMATI IMMEDIATAMENTE!")
    await asyncio.sleep(0.05)
    assert gate.state == GateState.STOPPED and gate.pending is None
    r = await tc.handle("indica il centro")
    assert gate.state == GateState.STOPPED and gate.pending is None
    assert "STOP" in r["reply"] or "ripristino" in r["reply"]
