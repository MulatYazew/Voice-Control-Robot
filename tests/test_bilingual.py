"""Italian and English map to the SAME canonical action; replies follow the operator's language."""

import asyncio

import pytest

from app.conversation.rules import detect_language, parse
from app.conversation.text_mode import TextConversation
from app.safety.phrases import NEGATIVE, STOP, UNCERTAIN, VALID, confirmation_verdict, is_cancel, is_stop

from .conftest import wait_state


@pytest.mark.parametrize("it,en", [
    ("Solleva il vassoio a sinistra.", "Lift the tray on the left."),
    ("Indica la zona centrale.", "Point to the central zone."),
    ("Spingi il vassoio di destra verso sinistra.", "Push the right tray towards the left."),
    ("Appoggia il vassoio al centro.", "Put the tray down in the centre."),
    ("Torna a casa.", "Go home."),
])
def test_same_canonical_action(settings, it, en):
    a, b = parse(it, poses=settings.poses), parse(en, poses=settings.poses)
    assert a.kind == b.kind == "skill" and a.skill == b.skill and a.params == b.params


@pytest.mark.parametrize("t,v", [("Yes, proceed.", VALID), ("Confirm.", VALID), ("Go ahead.", VALID), ("Maybe.", UNCERTAIN),
                                 ("Wait.", UNCERTAIN), ("I'm not sure.", NEGATIVE), ("No.", NEGATIVE), ("Stop!", STOP)])
def test_english_confirmations(t, v):
    assert confirmation_verdict(t) == v


@pytest.mark.parametrize("t", ["STOP NOW!", "Stop immediately!", "Freeze!", "Halt", "Emergency!"])
def test_english_stop(t):
    assert is_stop(t)


@pytest.mark.parametrize("t", ["Cancel.", "Never mind.", "Forget it.", "No thanks."])
def test_english_cancel(t):
    assert is_cancel(t)


def test_ambiguous_in_both_languages(settings):
    assert parse("Sposta quello.", poses=settings.poses).kind == "clarify"
    assert parse("Move that.", poses=settings.poses).kind == "clarify"


def test_language_detection():
    assert detect_language("Lift the tray please") == "en"
    assert detect_language("Solleva il vassoio per favore") == "it"


async def test_english_text_flow(gate):
    tc = TextConversation(gate)
    r = await tc.handle("Lift the tray on the left.")
    assert r["language"] == "en" and "Do you confirm?" in r["reply"]
    r = await tc.handle("Yes, proceed.")
    assert r["reply"] == "Proceeding."
    assert await wait_state(gate, "IDLE")
    assert gate.last_result.skill == "solleva_vassoio" and gate.last_result.parameters == {"posizione": "sinistra"}
    r = await tc.handle("Draw a circle.", lang="en")
    assert r["reply"].startswith("I can't do that")
    await tc.handle("STOP NOW!")
    await asyncio.sleep(0.05)
    assert gate.state.value == "STOPPED"
