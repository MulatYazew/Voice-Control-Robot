"""Camera, zone geometry and the deterministic object decision. No device, no network."""

import numpy as np

import cv2

from app.perception.camera import CameraService, point_in_polygon
from app.perception.evidence import EvidenceStore
from app.perception.finder import Candidate, Query, decide, proposals_from_frame


def test_point_in_polygon_and_uncalibrated():
    square = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert point_in_polygon(5, 5, square)
    assert not point_in_polygon(15, 5, square)
    cam = CameraService({"available": True, "source": "browser", "zones_px": {"sinistra": None, "centro": None, "destra": None}})
    assert not cam.calibrated()
    assert cam.zone_at(1, 1) is None
    cam.cfg["zones_px"] = {"sinistra": square, "centro": [[20, 0], [30, 0], [30, 10], [20, 10]],
                           "destra": [[40, 0], [50, 0], [50, 10], [40, 10]]}
    assert cam.calibrated()
    assert cam.zone_at(5, 5) == "sinistra"
    assert cam.zone_at(25, 5) == "centro"


def test_stale_and_missing_and_unavailable():
    now = {"t": 1000.0}
    cam = CameraService({"available": True, "source": "browser", "max_age_s": 1.5}, now=lambda: now["t"])
    assert cam.get_frame() is None
    img = np.zeros((4, 4, 3), np.uint8)
    cam.push_frame(img)
    assert cam.get_frame() is not None
    now["t"] = 1002.0
    assert cam.get_frame() is None
    off = CameraService({"available": False, "source": "browser"}, now=lambda: now["t"])
    off.push_frame(img)
    assert off.get_frame() is None


def test_decide_single_ambiguous_missing_disagree_uncalibrated():
    red = Candidate("sinistra", colore="rosso", colore_de=4, fonti=["CV"])
    red2 = Candidate("centro", colore="rosso", colore_de=5, fonti=["CV"])
    blue = Candidate("destra", colore="blu", colore_de=3, testo="SALVIA", testo_score=92, fonti=["CV", "OCR"])
    q = Query(colore="rosso")
    one = decide(q, [red, blue], calibrated=True, camera_available=True)
    assert one["ok"] and one["zona"] == "sinistra"
    amb = decide(q, [red, red2], calibrated=True, camera_available=True)
    assert amb["esito"] == "richiesta_ambigua" and not amb["ok"]
    missing = decide(Query(colore="verde"), [red, blue], calibrated=True, camera_available=True)
    assert missing["esito"] == "non_trovato"
    wrong = decide(Query(testo="menta"), [blue], calibrated=True, camera_available=True)
    assert wrong["esito"] == "non_trovato"
    disagree = Candidate("sinistra", colore="rosso", colore_de=4, grok_colore="blu", fonti=["CV"])
    bad = decide(Query(colore="rosso"), [disagree], calibrated=True, camera_available=True)
    assert bad["esito"] == "incerto"
    assert decide(q, [red], calibrated=False, camera_available=True)["esito"] == "zone_non_calibrate"
    assert decide(q, [red], calibrated=True, camera_available=False)["esito"] == "nessuna_telecamera"
    off = Candidate(None, colore="rosso", colore_de=2, su_vassoio=False, fonti=["CV"])
    assert decide(q, [off], calibrated=True, camera_available=True)["esito"] == "non_su_vassoio"


def test_evidence_expires_and_zone_must_match(gate):
    store = EvidenceStore(ttl_s=15)
    gate.evidence = store
    eid = store.put({"zona": "sinistra", "candidato": {"colore": "rosso", "colore_de": 4, "fonti": ["CV"]}})
    ok = gate.propose("solleva_vassoio", {"posizione": "sinistra"}, evidenza_id=eid)
    assert ok["ok"] and "Vedo" in ok["frase_conferma"]
    store._items[eid]["t"] -= 20
    stale = gate.propose("solleva_vassoio", {"posizione": "sinistra"}, evidenza_id=eid)
    assert stale["esito"] == "evidenza_scaduta"
    fresh = store.put({"zona": "destra", "candidato": {"colore": "blu", "colore_de": 3, "fonti": ["CV"]}})
    assert gate.propose("solleva_vassoio", {"posizione": "destra"}, evidenza_id=fresh)["ok"]
    other = store.put({"zona": "centro", "candidato": {"colore": "rosso", "colore_de": 1, "fonti": ["CV"]}})
    assert gate.propose("indica", {"posizione": "sinistra"}, evidenza_id=other)["esito"] == "evidenza_scaduta"


def test_synthetic_red_blob_is_one_zone():
    frame = np.full((200, 600, 3), 180, np.uint8)
    cv2.circle(frame, (100, 100), 30, (40, 40, 210), -1)
    cv2.circle(frame, (300, 100), 30, (40, 40, 210), -1)
    zones = {"sinistra": [[0, 0], [200, 0], [200, 200], [0, 200]],
             "centro": [[200, 0], [400, 0], [400, 200], [200, 200]],
             "destra": [[400, 0], [600, 0], [600, 200], [400, 200]]}
    props = proposals_from_frame(frame, zones, None)
    cands = [Candidate(zona=p["zona"], colore=p["colore"], colore_de=p["colore_de"], fonti=["CV"]) for p in props]
    one = [c for c in cands if c.zona == "sinistra"]
    assert one and one[0].colore == "rosso"
    amb = decide(Query(colore="rosso"), cands, calibrated=True, camera_available=True)
    assert amb["esito"] == "richiesta_ambigua"


async def test_recheck_aborts(gate):
    gate.recheck = lambda _p: {"ok": False, "esito": "scena_cambiata", "messaggio": "sparito"}
    r = gate.propose("indica", {"posizione": "centro"})
    gate.mark_question_asked(r["action_id"])
    gate.note_turn_committed("voice")
    gate.note_transcript("sì", source="voice")
    out = await gate.confirm(r["action_id"])
    assert out["esito"] == "scena_cambiata" and gate.last_result is None
    assert gate.state.value == "IDLE"
