"""Simulated station camera. No URSim, no webcam, no network."""

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.conversation.rules import parse
from app.main import create_app
from app.perception.identify import describe_zones, find_in_rows, match_product, rows_from_image
from app.perception.sim_station import BANNER, SimStation, mirror_view
from app.robot.sim import SimAdapter

CATALOG = [{"id": "lumen_crema_viso", "nome": "Crema Viso Idratante", "marca": "ATELIER LUMEN"},
           {"id": "ambra_balsamo_labbra", "nome": "Balsamo Labbra", "marca": "AMBRA"}]
CREAM = "ATELIER LUMEN Crema Viso Idratante"


def test_mirror_view_flips_left_to_right():
    img = np.zeros((8, 16, 3), np.uint8)
    img[:, 0] = (0, 0, 255)
    out = mirror_view(img)
    assert int(out[0, -1, 2]) == 255 and int(out[0, 0, 2]) == 0


def test_scene_moves_only_after_completato():
    station = SimStation()
    station.load_preset("demo")
    before = station.public()["zones"]["sinistra"]["product"]
    for outcome in ("uncertain", "STOPPED", "FAULT", "non_eseguito"):
        station.on_robot("solleva_vassoio", {"posizione": "sinistra"}, outcome)
        assert station.public()["zones"]["sinistra"]["product"] == before
        assert station.public()["uncertain"] is True
    station.on_robot("solleva_vassoio", {"posizione": "sinistra"}, "completato")
    assert station.public()["zones"]["sinistra"] is None
    assert station.public()["on_fork"]["product"] == before
    assert station.public()["uncertain"] is False
    station.on_robot("appoggia_vassoio", {"posizione": "destra"}, "completato")
    assert station.public()["zones"]["destra"]["product"] == before
    station.on_robot("spingi", {"posizione": "destra", "direzione": "scarto"}, "completato")
    assert station.public()["zones"]["destra"] is None
    assert station.public()["reject"]["product"] == before


def test_render_marks_empty_and_occupied_zones():
    station = SimStation()
    station.load_preset("empty")
    empty = rows_from_image(station.render(), CATALOG, read=lambda _crop: "")
    assert [row["stato"] for row in empty] == ["empty", "empty", "empty"]
    assert all(row["sorgente"] == BANNER for row in empty)
    station.load_preset("demo")
    occupied = rows_from_image(station.render(), CATALOG, read=lambda _crop: "")
    assert [row["stato"] for row in occupied] == ["pack", "empty", "pack"]


def test_identity_and_unknown_and_ambiguous():
    assert match_product(CREAM, CATALOG)["id"] == "lumen_crema_viso"
    assert match_product("SIERO MIRTO NERO", CATALOG) is None
    rows = describe_zones(
        {"sinistra": {"present": True}, "centro": {"present": True}, "destra": None},
        {"sinistra": CREAM, "centro": CREAM},
        CATALOG,
        compliance={"lumen_crema_viso": {"decisione": "NON_COMPLIANT", "motivi": ["wrong_inci"]}},
    )
    assert rows[0]["tipo"]["valore"] == "cream"
    assert rows[0]["prodotto"]["fonte"] == "ocr+catalogo"
    assert rows[0]["conformita"]["decisione"] == "NON_COMPLIANT"
    assert rows[1]["conformita"]["decisione"] == "NON_COMPLIANT"
    unknown = describe_zones({"sinistra": {"present": True}, "centro": None, "destra": None},
                             {"sinistra": "SIERO MIRTO NERO"}, CATALOG,
                             compliance={"lumen_crema_viso": {"decisione": "COMPLIANT", "motivi": []}})
    assert unknown[0]["prodotto"]["fonte"] == "unknown product"
    assert unknown[0]["conformita"]["decisione"] != "COMPLIANT"
    assert unknown[0]["tipo"]["valore"] == "serum"
    assert find_in_rows(rows, tipo="cream")["stato"] == "ambiguous"
    station = SimStation()
    station.load_preset("ambiguous")
    assert station.public()["zones"]["destra"] is None
    assert station.public()["zones"]["sinistra"]["product"] == station.public()["zones"]["centro"]["product"]


def test_phrases_do_not_steal_the_refusal_case():
    assert parse("Guarda cosa c'è sul tavolo.").kind == "out_of_scope"
    assert parse("Cosa c'è sul tavolo?").kind == "scene"
    assert parse("What do you see?").kind == "scene"
    assert parse("L'etichetta della crema a destra è corretta?").params["azione"] == "etichetta"
    assert parse("Porta la crema a sinistra a destra").params["posizione"] == "destra"
    assert parse("Dov'è la crema?").params["azione"] == "trova"
    assert parse("Dov'è la crema?").kind == "scene"


def test_sim_api_is_refused_unless_the_source_is_sim(settings):
    app = create_app(settings, SimAdapter(settings.poses["home"]["joints"], time_scale=20))
    with TestClient(app) as client:
        assert client.get("/api/sim/scene").status_code == 409
        assert client.post("/api/sim/scene/preset", json={"name": "demo"}).status_code == 409
        settings.mode = "real"
        settings.camera["source"] = "sim"
        real = create_app(settings, SimAdapter(settings.poses["home"]["joints"], time_scale=20))
        with TestClient(real) as other:
            assert other.get("/api/sim/scene").status_code == 409


def test_sim_scene_and_frame_and_executing_lock(settings):
    settings.camera["source"] = "sim"
    app = create_app(settings, SimAdapter(settings.poses["home"]["joints"], time_scale=0.01))
    with TestClient(app) as client:
        scene = client.get("/api/sim/scene").json()
        assert scene["label"] == BANNER
        assert client.get("/api/camera/frame.jpg").status_code == 200
        client.post("/api/text-command", json={"text": "Solleva il vassoio a sinistra.", "request_id": "a"})
        client.post("/api/text-command", json={"text": "sì, confermo", "request_id": "b"})
        assert client.get("/api/status").json()["gate"] == "EXECUTING"
        refused = client.post("/api/sim/scene", json={"zona": "centro", "product": "lumen_crema_viso", "variant": "correct_1"})
        assert refused.status_code == 409
