from starlette.testclient import TestClient

import scene
from grokarm_vision.app import operator_app, phone_app
from grokarm_vision.config import Settings
from grokarm_vision.frames import encode_jpeg
from grokarm_vision.resolve import OfflineResolver
from grokarm_vision.service import VisionService


def test_full_setup_and_locate_over_http(settings: Settings):
    service = VisionService(settings, resolver=OfflineResolver())
    phone = TestClient(phone_app(service))
    op = TestClient(operator_app(service))

    assert phone.post("/api/frame", content=encode_jpeg(scene.table(), 95)).status_code == 200
    assert op.get("/api/status").json()["problems"] == ["not calibrated", "no empty-table background"]
    r = op.post("/api/calibration", json={"points": scene.calibration_points()})
    assert r.status_code == 200, r.text
    assert op.post("/api/background").status_code == 200
    assert op.get("/api/status").json()["ready"] is True

    phone.post("/api/frame", content=encode_jpeg(scene.with_objects(scene.table(), [("red", 320, 60)]), 95))
    body = op.post("/api/locate", json={"query": "指向红色的", "lang": "zh"}).json()
    assert body["ok"] is True, body
    assert body["message"].startswith("我看到")
    assert op.get("/api/locate/last.jpg").headers["content-type"] == "image/jpeg"


def test_phone_app_cannot_read_results_or_change_setup(settings: Settings):
    phone = TestClient(phone_app(VisionService(settings)))
    assert phone.post("/api/locate", json={"query": "x"}).status_code in (404, 405)
    assert phone.post("/api/calibration", json={"points": []}).status_code in (404, 405)
    assert phone.get("/api/frame.jpg").status_code == 404


def test_bad_frame_and_bad_calibration_rejected(settings: Settings):
    service = VisionService(settings)
    op = TestClient(operator_app(service))
    assert op.post("/api/frame", content=b"not an image").status_code == 400
    op.post("/api/frame", content=encode_jpeg(scene.table()))
    r = op.post("/api/calibration", json={"points": scene.calibration_points()[:3]})
    assert r.status_code == 409


def test_operator_api_rejects_lan_clients(settings: Settings):
    op = TestClient(operator_app(VisionService(settings)), client=("192.168.1.40", 5000))
    assert op.get("/api/status").status_code == 403
