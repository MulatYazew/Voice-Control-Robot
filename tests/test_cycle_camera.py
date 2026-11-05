"""Station-camera cycle: no upload, mirror view, missing face, stale frame, STOP."""

import asyncio

import numpy as np
import pytest

from app.labelling.sorting import SortingCycle, _apply_view


class _Label:
    def __init__(self, face: str) -> None:
        self.face = face
        self.required = True
        self.label_id = face


class _Spec:
    approval_status = "approved"
    version = 1
    product_name = "Prova"
    product_id = "prova"
    labels = [_Label("front"), _Label("back")]


class _Svc:
    def __init__(self, decision: str = "COMPLIANT") -> None:
        self.cfg = {"cycle": {"settle_s": 0.0, "confirm_each_move": False, "capture_timeout_s": 0.2}, "grok": {}}
        self.sheet_results = {"prova": {"ok": True, "spec_version": 1, "sheet_state": "OK"}}
        self.decision = decision
        self.seen: dict = {}

    def get(self, product_id: str) -> _Spec:
        return _Spec()

    def inspect(self, product_id: str, photos: dict, use_grok: bool = True) -> dict:
        self.seen = {k: v.copy() for k, v in photos.items()}
        return {"decision": self.decision, "explanation": {"it": "esito", "en": "result"}, "inspection_id": "T1"}


class _Cam:
    def __init__(self, frame: np.ndarray | None, *, faces=None, views=None) -> None:
        self.available = True
        self.source = "folder:station"
        self.cfg = {"faces_visible_from_station": faces or ["front", "back"], "views": views or {}}
        self.frame = frame
        self.calls = 0

    def get_frame(self):
        self.calls += 1
        if self.frame is None:
            return None
        return self.frame.copy(), 1.0


def _frame() -> np.ndarray:
    img = np.zeros((48, 96, 3), np.uint8)
    img[:, :48] = (240, 240, 240)
    return img


async def _wait(cyc: SortingCycle, phases: set[str]) -> None:
    for _ in range(400):
        if cyc.phase in phases:
            return
        await asyncio.sleep(0.02)


@pytest.mark.asyncio
async def test_auto_cycle_accepts_without_upload(gate):
    svc, cam = _Svc("COMPLIANT"), _Cam(_frame())
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    res = await cyc.start("prova", "test", layout_confirmed=True)
    assert res["ok"], res
    await _wait(cyc, {"DONE", "ABORTED", "HOLD_REVIEW"})
    assert cyc.phase == "DONE" and cyc.counters["accepted"] == 1 and cyc.interventions == 0
    assert gate.world.trays_at == {"destra"}


@pytest.mark.asyncio
async def test_auto_cycle_rejects_without_upload(gate):
    svc, cam = _Svc("NON_COMPLIANT"), _Cam(_frame())
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    assert (await cyc.start("prova", "test", layout_confirmed=True))["ok"]
    await _wait(cyc, {"DONE", "ABORTED", "HOLD_REVIEW"})
    assert cyc.phase == "DONE" and cyc.counters["rejected"] == 1 and cyc.interventions == 0


def test_mirror_view_flips_the_back_roi():
    img = _frame()
    flipped = _apply_view(img, {"roi": [0, 0, 96, 48], "mirror": "horizontal"})
    assert flipped[0, 0, 0] == 0 and img[0, 0, 0] == 240


@pytest.mark.asyncio
async def test_mirror_view_is_what_inspection_receives(gate):
    svc = _Svc("COMPLIANT")
    cam = _Cam(_frame(), faces=["front"], views={"back": {"roi": [0, 0, 96, 48], "mirror": "horizontal"}})
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    assert (await cyc.start("prova", "test", layout_confirmed=True))["ok"]
    await _wait(cyc, {"DONE", "ABORTED", "HOLD_REVIEW"})
    assert svc.seen["front"][0, 0, 0] == 240
    assert svc.seen["back"][0, 0, 0] == 0
    assert cyc.interventions == 0


@pytest.mark.asyncio
async def test_uncovered_face_is_review_and_preflight_refuses(gate):
    svc, cam = _Svc("COMPLIANT"), _Cam(_frame(), faces=["front"])
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    res = await cyc.start("prova", "test", layout_confirmed=True)
    assert not res["ok"] and any("non verificabili" in p for p in res["problems"])


@pytest.mark.asyncio
async def test_stale_frame_does_not_start(gate):
    svc, cam = _Svc(), _Cam(None, faces=["front", "back"])
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    res = await cyc.start("prova", "test", layout_confirmed=True)
    assert not res["ok"] and any("fotogramma" in p for p in res["problems"])


@pytest.mark.asyncio
async def test_frame_lost_after_preflight_is_review(gate):
    class Lost(_Cam):
        def get_frame(self):
            self.calls += 1
            if self.calls == 1:
                return self.frame.copy(), 1.0
            return None

    svc, cam = _Svc("COMPLIANT"), Lost(_frame())
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    assert (await cyc.start("prova", "test", layout_confirmed=True))["ok"]
    await _wait(cyc, {"HOLD_REVIEW", "DONE", "ABORTED"})
    assert cyc.phase == "HOLD_REVIEW" and cyc.counters["accepted"] == 0 and cyc.interventions == 0


@pytest.mark.asyncio
async def test_stop_during_capture_aborts(gate):
    svc, cam = _Svc(), _Cam(_frame())
    svc.cfg["cycle"]["settle_s"] = 2.0
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    assert (await cyc.start("prova", "test", layout_confirmed=True))["ok"]
    await _wait(cyc, {"AWAITING_PHOTOS", "ABORTED"})
    await gate.stop("ui")
    await _wait(cyc, {"ABORTED"})
    assert cyc.phase == "ABORTED" and cyc.counters["accepted"] == 0


@pytest.mark.asyncio
async def test_empty_sheet_refuses_the_cycle(gate):
    svc, cam = _Svc(), _Cam(_frame())
    svc.sheet_results["prova"]["sheet_state"] = "SHEET_EMPTY"
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)
    res = await cyc.start("prova", "test", layout_confirmed=True)
    assert not res["ok"] and any("esaurito" in p for p in res["problems"])


@pytest.mark.asyncio
async def test_strict_mode_waits_before_each_move(gate):
    svc, cam = _Svc("COMPLIANT"), _Cam(_frame())
    svc.cfg["cycle"]["confirm_each_move"] = True
    cyc = SortingCycle(gate, svc, gate.bus, camera=cam)

    async def allow() -> None:
        for _ in range(500):
            if cyc.phase == "AWAIT_MOVE_CONFIRM":
                cyc.confirm_move.set()
            if cyc.phase in ("DONE", "ABORTED", "HOLD_REVIEW"):
                return
            await asyncio.sleep(0.01)

    assert (await cyc.start("prova", "test", layout_confirmed=True))["ok"]
    await allow()
    assert cyc.phase == "DONE" and cyc.counters["accepted"] == 1
