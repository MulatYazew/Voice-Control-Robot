"""Smart Labelling: specification approval, inspection decisions, sheet check, Grok downgrade-only, sorting cycle.
Uses the synthetic dataset (scripts/make_synthetic_packs.py) and Tesseract; no xAI calls (Grok is mocked)."""

import asyncio
import json
from pathlib import Path

import pytest

from app.config import ROOT
from app.labelling import compliance, ocr
from app.labelling.service import ApprovalError, LabellingService
from app.labelling.sorting import SortingCycle

DATA = ROOT / "data" / "datasets" / "synthetic"
P = "lumen_crema_viso"
pytestmark = pytest.mark.skipif(not (ocr.available() and (DATA / P).exists()),
                                reason="needs tesseract + synthetic dataset (python scripts/make_synthetic_packs.py)")
TOL = {"position_pct": 4.0, "angle_deg": 3.0}


def ref(name):
    return (DATA / P / "reference" / name).read_bytes()


def pack(case):
    return {f: (DATA / P / "test" / f"{case}_{f}.jpg").read_bytes() for f in ("front", "back")}


@pytest.fixture
def svc(tmp_path):
    return LabellingService(None, root=tmp_path / "labelling")


@pytest.fixture
def approved(svc):
    spec = svc.learn({"front": ref("front.jpg"), "back": ref("back.jpg")}, P)
    truth = json.loads((DATA / P / "truth.json").read_text())
    return svc.approve(spec.product_id, operator="test", tolerances={l.label_id: TOL for l in spec.labels},
                       confirmed={l.label_id: l.uncertain for l in spec.labels},
                       edits={"front": {"kind": "front_brand", "product_name": truth["name"], "brand": truth["brand"]},
                              "back": {"kind": "ingredients_legal"}})


def test_spec_reads_geometry_and_inci_without_training(svc):
    spec = svc.learn({"front": ref("front.jpg"), "back": ref("back.jpg")}, P)
    assert spec.approval_status == "pending" and {l.face for l in spec.labels} == {"front", "back"}
    back = spec.label("back")
    # positions are relative to the normalised face (1.5% edge trim -> sizes ~3% larger than on the full face)
    assert abs(back.position["cy"] - 0.55) < 0.01 and abs(back.position["w"] - 0.80) < 0.03
    assert "AQUA" in back.reference_text and "PHENOXYETHANOL" in back.reference_text


def test_approval_requires_tolerances_and_confirmations(svc):
    spec = svc.learn({"front": ref("front.jpg"), "back": ref("back.jpg")}, P)
    with pytest.raises(ApprovalError, match="tolleranza"):
        svc.approve(spec.product_id, operator="t", tolerances={}, confirmed={})
    with pytest.raises(ApprovalError, match="confermare"):   # uncertain fields not confirmed
        svc.approve(spec.product_id, operator="t", tolerances={l.label_id: TOL for l in spec.labels}, confirmed={})
    assert svc.get(spec.product_id).approval_status == "pending"


def test_unapproved_spec_cannot_inspect(svc):
    spec = svc.learn({"front": ref("front.jpg"), "back": ref("back.jpg")}, P)
    with pytest.raises(ValueError):
        svc.inspect(spec.product_id, pack("correct_1"), use_grok=False)


@pytest.mark.parametrize("case,expected", [("correct_1", "COMPLIANT"), ("wrong_inci", "NON_COMPLIANT"),
                                           ("crooked", "NON_COMPLIANT"), ("creased", "NON_COMPLIANT"),
                                           ("swapped", "NON_COMPLIANT"), ("blurred", "REVIEW_REQUIRED")])
def test_decisions(svc, approved, case, expected):
    r = svc.inspect(approved.product_id, pack(case), use_grok=False)
    assert r["decision"] == expected, r["explanation"]["en"]
    assert r["explanation"]["it"] and r["explanation"]["en"]


def test_missing_face_photo_is_review_not_compliant(svc, approved):
    r = svc.inspect(approved.product_id, {"front": pack("correct_1")["front"]}, use_grok=False)
    assert r["decision"] == "REVIEW_REQUIRED" and any(f["code"] == "missing_image" for f in r["findings"])


def test_grok_can_only_downgrade(svc, approved, monkeypatch):
    monkeypatch.setattr(compliance, "_grok_second_opinion",
                        lambda *a, **k: {"verdict": "non_compliant", "reason_en": "looks creased", "reason_it": "piega"})
    svc.api_key = "test"
    r = svc.inspect(approved.product_id, pack("correct_1"), use_grok=True)
    assert r["decision"] == "REVIEW_REQUIRED"            # never NON_COMPLIANT on Grok alone, never stays COMPLIANT
    monkeypatch.setattr(compliance, "_grok_second_opinion", lambda *a, **k: {"verdict": "compliant"})
    r = svc.inspect(approved.product_id, pack("wrong_inci"), use_grok=True)
    assert r["decision"] == "NON_COMPLIANT"              # Grok cannot upgrade a rejection


def test_sheet_verification(svc, approved):
    assert svc.verify_sheet(approved.product_id, ref("sheet.jpg"))["decision"] == "MATCH"
    assert svc.verify_sheet(approved.product_id, ref("sheet_wrong_product.jpg"))["decision"] == "MISMATCH"
    assert svc.verify_sheet(approved.product_id, ref("sheet_missing_back.jpg"))["decision"] == "MISMATCH"


# ------------------------------------------------------------ cycle ---
async def _cycle(gate, svc, approved, case, review_choice=None):
    cyc = SortingCycle(gate, svc, gate.bus)
    svc.verify_sheet(approved.product_id, ref("sheet.jpg"))
    res = await cyc.start(approved.product_id, "test", layout_confirmed=True)
    assert res["ok"], res
    for _ in range(300):
        if cyc.phase == "AWAITING_PHOTOS":
            break
        await asyncio.sleep(0.02)
    from app.labelling import vision as V
    for f, b in pack(case).items():
        cyc.add_photo(f, V.load(b))
    for _ in range(600):
        if cyc.phase in ("DONE", "ABORTED", "HOLD_REVIEW"):
            break
        await asyncio.sleep(0.02)
    if review_choice and cyc.phase == "HOLD_REVIEW":
        cyc.review_decision(review_choice)
        for _ in range(600):
            if cyc.phase in ("DONE", "ABORTED"):
                break
            await asyncio.sleep(0.02)
    return cyc


async def test_cycle_accepts_compliant(gate, svc, approved):
    cyc = await _cycle(gate, svc, approved, "correct_1")
    assert cyc.phase == "DONE" and cyc.counters["accepted"] == 1
    assert gate.world.trays_at == {"destra"} and not gate.cycle["active"]


async def test_cycle_rejects_non_compliant(gate, svc, approved):
    cyc = await _cycle(gate, svc, approved, "wrong_inci")
    assert cyc.phase == "DONE" and cyc.counters["rejected"] == 1
    assert gate.world.trays_at == set()                    # pushed into the reject box
    assert gate.last_result.parameters == {"posizione": "centro", "direzione": "scarto"}


async def test_cycle_review_holds_and_never_accepts(gate, svc, approved):
    cyc = await _cycle(gate, svc, approved, "blurred")
    assert cyc.phase == "HOLD_REVIEW" and gate.world.trays_at == {"centro"}   # no movement after inspection
    assert not cyc.review_decision("accept")["ok"]                            # accepting is not an option
    cyc.review_decision("reject")
    for _ in range(600):
        if cyc.phase == "DONE":
            break
        await asyncio.sleep(0.02)
    assert cyc.counters["rejected"] == 1 and cyc.counters["accepted"] == 0


async def test_cycle_requires_verified_sheet(gate, svc, approved):
    cyc = SortingCycle(gate, svc, gate.bus)
    res = await cyc.start(approved.product_id, "test", layout_confirmed=True)
    assert not res["ok"] and any("foglio" in p for p in res["problems"])


async def test_stop_aborts_cycle(gate, svc, approved, sim):
    sim.time_scale = 1.0
    cyc = SortingCycle(gate, svc, gate.bus)
    svc.verify_sheet(approved.product_id, ref("sheet.jpg"))
    await cyc.start(approved.product_id, "test", layout_confirmed=True)
    await asyncio.sleep(0.4)                                  # moving the pack
    await gate.stop("ui")
    await asyncio.sleep(0.3)
    assert gate.state.value == "STOPPED" and not gate.cycle["active"]
    assert cyc.phase == "ABORTED"
    r = await gate.run_cycle_step(cyc.cycle_id, "appoggia_vassoio", {"posizione": "centro"})
    assert not getattr(r, "success", False)


async def test_cycle_rejects_movement_outside_the_cycle(gate, svc, approved):
    gate.authorize_cycle(approved.product_id, "test")
    r = await gate.run_cycle_step(gate.cycle["id"], "vai_a_posa", {"posa": "home"})
    assert isinstance(r, dict) and r["esito"] == "movimento_non_previsto"
