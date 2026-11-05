"""Sheet check: Grok may only downgrade, and a missing copy is counted."""

import cv2
import numpy as np

from app.labelling.compliance import REVIEW
from app.labelling.label_verification import MATCH, MISMATCH, _best_match, _remaining_copies, apply_sheet_opinion
from app.labelling.service import LabellingService


def _base(decision: str, ok: bool) -> dict:
    return {"decision": decision, "ok": ok, "labels": [], "explanation": {"it": "Foglio.", "en": "Sheet."}}


def test_grok_no_downgrades_a_match():
    out = apply_sheet_opinion(_base(MATCH, True), {"match": False, "explanation": "looks wrong", "per_label": []})
    assert out["decision"] == REVIEW and out["ok"] is False
    assert out["ai_opinion"]["label"].startswith("AI opinion")


def test_grok_yes_cannot_repair_a_mismatch():
    out = apply_sheet_opinion(_base(MISMATCH, False), {"match": True, "explanation": "looks fine", "per_label": []})
    assert out["decision"] == MISMATCH and out["ok"] is False


def test_grok_timeout_keeps_the_deterministic_result():
    out = apply_sheet_opinion(_base(MATCH, True), None)
    assert out["decision"] == MATCH and out["ok"] is True
    assert out["ai_check"] == "not_available"
    assert "not available" in out["explanation"]["en"]


def test_service_uses_the_downgrade_rule(tmp_path, monkeypatch):
    svc = LabellingService(None, root=tmp_path)
    svc.cfg["grok"]["sheet_check"] = True

    class Spec:
        product_id, version, product_name = "p", 1, "P"

    svc.get = lambda product_id: Spec()
    monkeypatch.setattr("app.labelling.service.verify_sheet",
                        lambda *a, **k: {"decision": MATCH, "ok": True, "labels": [], "sheet_state": "OK",
                                         "explanation": {"it": "Foglio.", "en": "Sheet."}})
    svc.sheet_opinion = lambda *a, **k: {"match": False, "explanation": "no", "per_label": []}
    (svc.store.product_dir("p", 1)).mkdir(parents=True)
    ok, buf = cv2.imencode(".jpg", np.zeros((16, 16, 3), np.uint8))
    assert ok
    out = svc.verify_sheet("p", buf.tobytes())
    assert out["decision"] == REVIEW and out["ok"] is False


def test_front_check_ignores_the_back_and_does_not_move(tmp_path, monkeypatch):
    from app.labelling.specification import LabelSpec, ProductSpec
    svc = LabellingService(None, root=tmp_path)
    spec = ProductSpec(product_id="p", approval_status="approved", product_name="P", labels=[
        LabelSpec(label_id="front", face="front", position={"cx": 0.5, "cy": 0.4, "w": 0.2, "h": 0.2}),
        LabelSpec(label_id="back", face="back", position={"cx": 0.5, "cy": 0.5, "w": 0.2, "h": 0.2}),
    ])
    svc.get = lambda product_id: spec
    seen: dict = {}

    def fake_inspect(spec, photos, cfg, product_dir, **kwargs):
        seen["required"] = [label.label_id for label in spec.labels if label.required]
        return {"decision": "NON_COMPLIANT", "inspection_id": "I9", "explanation": {"it": "storta", "en": "crooked"},
                "findings": [{"code": "misaligned"}]}

    monkeypatch.setattr("app.labelling.service.compliance.inspect", fake_inspect)
    out = svc.inspect_labels("p", {"front": np.zeros((12, 12, 3), np.uint8)}, ["front"])
    assert seen["required"] == ["front"]
    assert out["label_check"] == "LABEL_DEFECTIVE" and out["motion"] is False
    assert "back label" in out["advice"]["en"]


def test_one_copy_counts_as_one_left_and_two_copies_as_two():
    tmpl = np.zeros((40, 50), np.uint8)
    tmpl[4:36, 4:46] = 220
    one = np.full((180, 180), 20, np.uint8)
    one[20:60, 30:80] = tmpl
    score, box = _best_match(one, tmpl)
    assert score >= 0.8 and _remaining_copies(one, tmpl, box) == 1
    two = one.copy()
    two[100:140, 90:140] = tmpl
    score, box = _best_match(two, tmpl)
    assert _remaining_copies(two, tmpl, box) == 2
