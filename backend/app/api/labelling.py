"""Smart Labelling REST API (mounted under /api/labelling)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import ROOT
from ..labelling import vision as V
from ..labelling.service import ApprovalError, LabellingService
from ..labelling.sorting import SortingCycle
from ..labelling.specification import FACES

SAMPLES = ROOT / "data" / "datasets" / "synthetic"


class ApproveBody(BaseModel):
    operator_confirmed: bool
    operator: str = Field("operatore", max_length=60)
    tolerances: dict[str, dict[str, float]]
    confirmed: dict[str, list[str]] = Field(default_factory=dict)
    edits: dict[str, dict[str, Any]] = Field(default_factory=dict)


class CycleStart(BaseModel):
    product_id: str
    operator_confirmed: bool
    layout_confirmed: bool
    operator: str = Field("operatore", max_length=60)


class SamplePhoto(BaseModel):
    face: str
    sample: str = Field(..., description="relative path under data/datasets/synthetic")


class ReviewBody(BaseModel):
    choice: str


def _safe(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    if base.resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "file non trovato")
    return p


def make_router(svc: LabellingService, cycle: SortingCycle) -> APIRouter:
    r = APIRouter(prefix="/api/labelling")
    run = asyncio.get_event_loop

    async def blocking(fn, *a, **k):
        return await asyncio.get_running_loop().run_in_executor(None, lambda: fn(*a, **k))

    @r.get("/status")
    async def status() -> dict[str, Any]:
        return {**svc.status(), "cycle": cycle.status()}

    @r.get("/products")
    async def products() -> list[dict[str, Any]]:
        return svc.store.list()

    @r.post("/products")
    async def learn(name: str = Form(""), front: UploadFile | None = File(None), back: UploadFile | None = File(None),
                    left: UploadFile | None = File(None), right: UploadFile | None = File(None),
                    cap: UploadFile | None = File(None), bottom: UploadFile | None = File(None)) -> dict[str, Any]:
        files = {"front": front, "back": back, "left": left, "right": right, "cap": cap, "bottom": bottom}
        photos = {k: await f.read() for k, f in files.items() if f is not None}
        if not photos:
            raise HTTPException(422, "carica almeno una foto del pacco modello / upload at least one photo")
        try:
            spec = await blocking(svc.learn, photos, name or None)
        except ValueError as e:
            raise HTTPException(422, str(e))
        return spec.model_dump()

    @r.post("/products/from-sample")
    async def learn_sample(body: dict[str, str]) -> dict[str, Any]:
        """Demo without camera: learn from a dataset product's reference photos."""
        pid = body.get("sample_product", "")
        d = (SAMPLES / pid / "reference").resolve()
        if SAMPLES.resolve() not in d.parents or not d.is_dir():
            raise HTTPException(404, "campione sconosciuto")
        photos = {f: (d / f"{f}.jpg").read_bytes() for f in FACES if (d / f"{f}.jpg").exists()}
        spec = await blocking(svc.learn, photos, body.get("name") or pid)
        return spec.model_dump()

    @r.get("/products/{pid}")
    async def product(pid: str) -> dict[str, Any]:
        try:
            s = svc.get(pid)
        except KeyError:
            raise HTTPException(404, "prodotto sconosciuto")
        return {**s.model_dump(), "approval_blockers": s.approval_blockers(),
                "last_sheet": svc.sheet_results.get(pid)}

    @r.post("/products/{pid}/approve")
    async def approve(pid: str, body: ApproveBody) -> dict[str, Any]:
        if not body.operator_confirmed:
            raise HTTPException(400, "serve la conferma esplicita dell'operatore")
        try:
            s = svc.approve(pid, operator=body.operator, tolerances=body.tolerances, confirmed=body.confirmed, edits=body.edits)
        except KeyError:
            raise HTTPException(404, "prodotto sconosciuto")
        except ApprovalError as e:
            raise HTTPException(409, str(e))
        return s.model_dump()

    @r.post("/products/{pid}/sheet")
    async def sheet(pid: str, file: UploadFile | None = File(None), sample: str = Form("")) -> dict[str, Any]:
        data = await file.read() if file else _safe(SAMPLES, sample).read_bytes() if sample else None
        if not data:
            raise HTTPException(422, "foto del foglio mancante")
        try:
            return await blocking(svc.verify_sheet, pid, data)
        except ValueError as e:
            raise HTTPException(409, str(e))

    @r.post("/inspect")
    async def inspect(product_id: str = Form(...), use_grok: bool = Form(True), front: UploadFile | None = File(None),
                      back: UploadFile | None = File(None), left: UploadFile | None = File(None),
                      right: UploadFile | None = File(None), cap: UploadFile | None = File(None),
                      bottom: UploadFile | None = File(None)) -> dict[str, Any]:
        files = {"front": front, "back": back, "left": left, "right": right, "cap": cap, "bottom": bottom}
        photos = {k: await f.read() for k, f in files.items() if f is not None}
        try:
            return await blocking(svc.inspect, product_id, photos, use_grok=use_grok)
        except (ValueError, KeyError) as e:
            raise HTTPException(409, str(e))

    @r.get("/inspections")
    async def history() -> list[dict[str, Any]]:
        return svc.history()

    @r.get("/inspections/{iid}")
    async def inspection(iid: str) -> dict[str, Any]:
        try:
            return svc.inspection(iid)
        except FileNotFoundError:
            raise HTTPException(404, "ispezione sconosciuta")

    @r.get("/file/{rel:path}")
    async def file(rel: str) -> FileResponse:
        return FileResponse(_safe(svc.root, rel))

    @r.get("/samples")
    async def samples() -> dict[str, Any]:
        out = {}
        for p in sorted(x for x in SAMPLES.iterdir() if x.is_dir()):
            out[p.name] = {"reference": sorted(f.name for f in (p / "reference").glob("*.jpg")),
                           "test": sorted({f.name.rsplit("_", 1)[0] for f in (p / "test").glob("*.jpg")})}
        return out

    @r.get("/sample-file/{rel:path}")
    async def sample_file(rel: str) -> FileResponse:
        return FileResponse(_safe(SAMPLES, rel))

    # ------------------------------------------------------------- cycle ---
    @r.get("/cycle")
    async def cycle_status() -> dict[str, Any]:
        return cycle.status()

    @r.post("/cycle/preflight")
    async def preflight(body: dict[str, str]) -> dict[str, Any]:
        return {"problems": cycle.preflight(body.get("product_id", ""))}

    @r.post("/cycle/start")
    async def cycle_start(body: CycleStart) -> dict[str, Any]:
        if not body.operator_confirmed:
            raise HTTPException(400, "serve l'autorizzazione esplicita dell'operatore")
        res = await cycle.start(body.product_id, body.operator, body.layout_confirmed)
        if not res.get("ok"):
            raise HTTPException(409, "; ".join(res.get("problems", [])))
        return res

    @r.post("/cycle/photo")
    async def cycle_photo(face: str = Form(...), file: UploadFile = File(...)) -> dict[str, Any]:
        try:
            img = V.load(await file.read())
        except ValueError as e:
            raise HTTPException(422, str(e))
        return cycle.add_photo(face, img)

    @r.post("/cycle/photo-sample")
    async def cycle_photo_sample(body: SamplePhoto) -> dict[str, Any]:
        """Simulated station camera: feed a dataset image (development / demo without camera)."""
        return cycle.add_photo(body.face, V.load(_safe(SAMPLES, body.sample).read_bytes()))

    @r.post("/cycle/review")
    async def cycle_review(body: ReviewBody) -> dict[str, Any]:
        res = cycle.review_decision(body.choice)
        if not res.get("ok"):
            raise HTTPException(409, res.get("messaggio"))
        return res

    @r.post("/cycle/abort")
    async def cycle_abort() -> dict[str, Any]:
        cycle.abort("operatore")
        return cycle.status()

    _ = run
    return r
