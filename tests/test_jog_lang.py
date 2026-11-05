"""Directional commands in every operator language, including a turn with no joint named."""

import pytest

from app.skills.amounts import angle_deg, distance_cm
from app.config import VOICE_HINTS
from app.conversation.rules import parse, parse_jog
from .conftest import wait_state
from app.safety.phrases import keyterms_for
from app.skills.cartesian import canon_direction, canon_joint


@pytest.mark.parametrize("text,kind,params", [
    ("sposta a sinistra di 3 cm", "move", {"direzione": "sinistra"}),
    ("gira a sinistra di 20 gradi", "rotate", {"giunto": "base", "verso": "sinistra"}),
    ("ruota la base di 30 gradi a destra", "rotate", {"giunto": "base", "verso": "destra"}),
    ("sali di 2 cm", "move", {"direzione": "su"}),
    ("move up 2 centimeters", "move", {"direzione": "su"}),
    ("turn left 15 degrees", "rotate", {"giunto": "base", "verso": "sinistra"}),
    ("turn the shoulder up 10 degrees", "rotate", {"giunto": "spalla", "verso": "su"}),
    ("往左移动三厘米", "move", {"direzione": "sinistra"}),
    ("向左转30度", "rotate", {"giunto": "base", "verso": "sinistra"}),
    ("向上转10度", "rotate", {"giunto": "spalla", "verso": "su"}),
    ("向前3厘米", "move", {"direzione": "avanti"}),
    ("mueve a la izquierda 3 cm", "move", {"direzione": "sinistra"}),
    ("gira a la izquierda 30 grados", "rotate", {"giunto": "base", "verso": "sinistra"}),
    ("hacia arriba 2 centimetros", "move", {"direzione": "su"}),
    ("deplace a gauche de 3 cm", "move", {"direzione": "sinistra"}),
    ("tourne a gauche de 30 degres", "rotate", {"giunto": "base", "verso": "sinistra"}),
    ("en bas 2 cm", "move", {"direzione": "giu"}),
    ("bewege nach links 3 cm", "move", {"direzione": "sinistra"}),
    ("drehe 30 Grad nach links", "rotate", {"giunto": "base", "verso": "sinistra"}),
    ("nach oben 2 cm", "move", {"direzione": "su"}),
    ("左に3センチ動かして", "move", {"direzione": "sinistra"}),
    ("左に30度回して", "rotate", {"giunto": "base", "verso": "sinistra"}),
    ("ひだりに3センチ", "move", {"direzione": "sinistra"}),
    ("上に2センチ", "move", {"direzione": "su"}),
])
def test_jog_phrases(text, kind, params):
    jog = parse_jog(text)
    assert jog is not None and jog.kind == kind and jog.params == params


def test_grab_cup_is_a_touch():
    assert parse_jog("拿到橙色纸杯").params == {"colore": "orange"}
    assert parse_jog("拿到这个纸杯").note == "cup_colour"
    assert parse_jog("solleva il vassoio a sinistra") is None


@pytest.mark.parametrize("text", [
    "Solleva il vassoio a sinistra.",
    "spingi il vassoio di destra verso sinistra",
    "A sinistra, solleva il vassoio",
])
def test_tray_commands_are_not_jogs(text, settings):
    assert parse_jog(text) is None
    assert parse(text, poses=settings.poses).kind == "skill"


@pytest.mark.parametrize("said,canon", [
    ("a la izquierda", "sinistra"),
    ("nach links", "sinistra"),
    ("à gauche", "sinistra"),
    ("arriba", "su"),
    ("左", "sinistra"),
    ("die Basis", "base"),
    ("el hombro", "spalla"),
    ("épaule", "spalla"),
])
def test_spoken_direction_maps_to_id(said, canon):
    assert (canon_joint if canon in ("base", "spalla") else canon_direction)(said) == canon


@pytest.mark.parametrize("text", ["肘关节向上十公分", "肘关节向上十厘米", "elbow up 10 cm", "10公分向上"])
def test_length_is_not_degrees(text):
    assert distance_cm(text) == 10
    assert angle_deg(text) is None


def test_degrees_are_not_centimetres():
    assert angle_deg("肘关节向上十度") == 10
    assert distance_cm("肘关节向上十度") is None


def test_cm_phrase_is_a_tip_move():
    jog = parse_jog("肘关节向上十公分")
    assert jog is not None and jog.kind == "move" and jog.params == {"direzione": "su"}


async def test_one_yes_runs_two_joint_turns(gate, sim):
    import math
    from app.conversation.text_mode import TextConversation
    with sim._lock:
        sim._q = list(gate.s.poses["sinistra_sopra"]["joints"])
    q0 = list(sim.current_joints())
    tc = TextConversation(gate)
    r = await tc.handle("底座向左转5度，然后肘关节向上转5度")
    assert r["result"]["ok"], r
    assert r["result"]["parametri"]["passi"] == "2"
    assert "确认" in r["reply"] or "Confermi" in r["reply"] or "Proceed" in r["reply"]
    r2 = await tc.handle("是的")
    assert r2["result"]["ok"], r2
    assert await wait_state(gate, "IDLE")
    q1 = sim.current_joints()
    assert abs((q1[0] - q0[0]) - math.radians(5)) < 0.05
    assert abs((q1[2] - q0[2]) + math.radians(5)) < 0.05


async def test_joint_plus_centimetres_moves_the_tip(gate):
    from .conftest import say
    await say(gate, "肘关节向上十公分")
    r = await gate.propose_rotate("gomito", "su", source="rules")
    # Centimetres must never become a joint rotation. The tip move itself may be refused
    # when the current pose cannot keep the tool orientation.
    assert "gradi" not in r.get("parametri", {})
    if r.get("ok"):
        assert r["parametri"]["direzione"] == "su" and r["parametri"]["cm"] == "10"
    else:
        assert r["esito"] == "fuori_dai_limiti"


def test_spanish_voice_hint_is_regional():
    assert VOICE_HINTS["es"] == "es-ES"
    assert VOICE_HINTS["zh"] == "zh"
    assert VOICE_HINTS["ja"] == "ja"


def test_keyterms_follow_the_selected_language():
    es = keyterms_for("es")
    assert es[0] == "izquierda"
    assert "sinistra" not in es
    assert keyterms_for("zh")[0] == "向左"
    assert "stop" in keyterms_for("fr")
