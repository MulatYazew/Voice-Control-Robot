"""Coloured cups -> zone -> `indica` proposal (still confirmed by the operator)."""

import cv2
import numpy as np

from app.safety.gate import GateState
from app.vision.camera import CameraHub
from app.vision.cups import colour_of, find_cups
from app.vision.inspector import MockInspector

from .conftest import say, wait_state

ZONES = ["sinistra", "centro", "destra"]


def table(*cups: tuple[str, int]) -> np.ndarray:
    """cups: (colour, x centre) on a grey table, 960x540."""
    img = np.full((540, 960, 3), (95, 100, 105), np.uint8)
    bgr = {"red": (30, 30, 210), "blue": (200, 90, 20), "green": (50, 170, 40), "yellow": (30, 210, 230),
           "orange": (0, 100, 255), "white": (245, 245, 245), "black": (25, 25, 25)}
    for colour, x in cups:
        cv2.ellipse(img, (x, 300), (45, 30), 0, 0, 360, bgr[colour], -1)
        cv2.rectangle(img, (x - 40, 300), (x + 40, 380), bgr[colour], -1)
    return img


def jpeg(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def test_shallow_pale_cup_on_a_white_table():
    img = np.full((540, 960, 3), (240, 240, 240), np.uint8)
    cv2.ellipse(img, (200, 340), (46, 22), 0, 0, 360, (236, 236, 242), -1)
    cv2.rectangle(img, (154, 340), (246, 400), (236, 236, 242), -1)
    cv2.ellipse(img, (200, 338), (46, 22), 0, 0, 360, (214, 214, 220), 3)
    cups = find_cups(img, ZONES, {})
    assert [(c.colour, c.zone) for c in cups] == [("white", "sinistra")]


def test_find_cups_zones_and_colours():
    cups = find_cups(table(("red", 150), ("blue", 480), ("yellow", 800)), ZONES, {})
    assert [(c.colour, c.zone) for c in cups] == [("red", "sinistra"), ("blue", "centro"), ("yellow", "destra")]
    assert find_cups(np.full((540, 960, 3), 100, np.uint8), ZONES, {}) == []


def test_mirrored_camera_and_roi():
    cfg = {"image_zone_order": ["destra", "centro", "sinistra"]}
    assert find_cups(table(("red", 150)), ZONES, cfg)[0].zone == "destra"
    assert find_cups(table(("red", 150)), ZONES, {"roi": [0.5, 0.0, 1.0, 1.0]}) == []


def test_orange_black_and_skin():
    cups = find_cups(table(("orange", 200), ("black", 760)), ZONES, {})
    assert [(c.colour, c.zone) for c in cups] == [("orange", "sinistra"), ("black", "destra")]
    skin = np.full((540, 960, 3), (95, 100, 105), np.uint8)
    cv2.ellipse(skin, (300, 280), (70, 90), 0, 0, 360, (140, 170, 210), -1)
    cv2.ellipse(skin, (700, 280), (70, 90), 0, 0, 360, (80, 140, 200), -1)
    assert find_cups(skin, ZONES, {}) == []


def test_colour_words():
    assert colour_of("indica il bicchiere rosso") == "red"
    assert colour_of("指一下蓝色的纸杯") == "blue"
    assert colour_of("zeig auf den grünen Becher") == "green"
    assert colour_of("red or blue?") is None and colour_of("the cup") is None


async def _gate_with(gate, img):
    gate.s.robot.setdefault("cups", {})["grok_fallback"] = False
    cam = CameraHub(gate.s)
    gate.attach_vision(cam, MockInspector())
    cam.push(jpeg(img))
    return cam


async def test_point_at_cup_proposes_indica_and_needs_yes(gate):
    await _gate_with(gate, table(("red", 150), ("blue", 480)))
    gate.s.lang = "zh"
    r = await gate.point_at_cup("blue")
    assert r["ok"] and r["skill"] == "indica" and r["parametri"] == {"posizione": "centro"}
    assert r["frase_conferma"].startswith("我看到蓝色的纸杯在中间区域")
    assert gate.state == GateState.PENDING_CONFIRMATION
    gate.mark_question_asked(r["action_id"])
    await say(gate, "是的")
    assert (await gate.confirm(r["action_id"]))["ok"]
    assert await wait_state(gate, "IDLE") and gate.world.known_pose == "centro_sopra"


async def test_point_at_cup_missing_ambiguous_no_camera(gate):
    r = await gate.point_at_cup("red")
    assert r["esito"] == "telecamera_non_disponibile"
    await _gate_with(gate, table(("green", 150), ("green", 800), ("red", 480)))
    r = await gate.point_at_cup("blue")
    assert r["esito"] == "oggetto_non_trovato" and "red (centro)" in r["visti"]
    r = await gate.point_at_cup("green")
    assert r["esito"] == "richiesta_ambigua" and r["opzioni_valide"] == ["sinistra", "destra"]
    assert (await gate.point_at_cup("gold"))["esito"] == "richiesta_ambigua"
    assert gate.pending is None


async def test_text_mode_cup(gate):
    from app.conversation.text_mode import TextConversation
    await _gate_with(gate, table(("yellow", 800)))
    out = await TextConversation(gate).handle("point at the yellow cup")
    assert out["result"]["parametri"] == {"posizione": "destra"} and gate.pending.question_asked_at is not None


async def test_background_mode_finds_white_and_black(gate):
    cam = await _gate_with(gate, table())
    assert gate.cups_background()["detector"] == "background"
    cam.push(jpeg(table(("white", 150), ("black", 800))))
    seen = gate.scan_cups()["cups"]
    assert [(c["colore"], c["zona"]) for c in seen] == [("white", "sinistra"), ("black", "destra")]
    r = await gate.point_at_cup("白色")
    assert r["ok"] and r["parametri"] == {"posizione": "sinistra"} and r["bicchiere"]["rilevatore"] == "background"
    gate.cancel()
    assert gate.cups_background(clear=True)["detector"] == "colour"
    left = gate.scan_cups()["cups"]
    assert [(c["colore"], c["zona"]) for c in left] == [("black", "destra")]
    assert "frac" in left[0]


def test_grok_cup_lookup_validates_reply():
    import json

    import httpx

    from app.vision.cups import grok_find_cups
    reply = {"cups": [{"colour": "white", "zone": "centro"}, {"colour": "gray", "zone": "destra"},
                      {"colour": "white", "zone": "x=0.3"}, {"colour": "gold", "zone": "centro"}]}

    def handler(req):
        body = json.loads(req.content)
        assert "sinistra, centro, destra" in body["messages"][0]["content"][1]["text"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})
    out = grok_find_cups(table(), ZONES, "k", "m", "https://x", transport=httpx.MockTransport(handler))
    assert out == [{"colore": "white", "zona": "centro"}, {"colore": "grey", "zona": "destra"}]
