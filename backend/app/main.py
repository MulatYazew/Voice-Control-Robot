"""FastAPI application: REST + WebSockets + production frontend on one URL."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import FRONTEND_DIST, ROOT, Settings, load_settings
from .conversation.text_mode import TEXT_MODE_LABEL, TextConversation
from .monitoring.events import EventBus, History, Metrics, log_event, setup_logging
from .safety.gate import Gate
from .skills.registry import Registry
from .voice.grok_session import GrokSession
from .api.labelling import make_router
from .conversation import tools as tools_mod
from .labelling.service import LabellingService
from .labelling.sorting import SortingCycle

log = logging.getLogger("voce.api")


def make_adapter(s: Settings):
    if s.mode == "sim":
        from .robot.sim import SimAdapter
        return SimAdapter(s.poses["home"]["joints"])
    from .robot.ur import URAdapter
    return URAdapter(s)


class SimZone(BaseModel):
    zona: str
    product: str | None = None
    variant: str | None = None


class SimPreset(BaseModel):
    name: str


class StopBody(BaseModel):
    source: Literal["button", "space", "http", "pedal"] = "http"


class ResetBody(BaseModel):
    operator_confirmed: bool = Field(..., description="Must be true: deliberate operator action from the UI")


class TextBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)
    engine: Literal["rules", "grok"] = "rules"
    lang: Literal["auto", "it", "en", "zh", "ja", "am"] = "auto"
    request_id: str | None = Field(None, max_length=64)


class _Dedup:
    """Duplicate command protection by client request_id (and identical text within 1 s)."""

    def __init__(self) -> None:
        self.seen: dict[str, tuple[float, Any]] = {}

    def get(self, key: str | None) -> Any:
        now = time.time()
        self.seen = {k: v for k, v in self.seen.items() if now - v[0] < 60}
        return self.seen.get(key)[1] if key and key in self.seen else None

    def put(self, key: str | None, value: Any) -> None:
        if key:
            self.seen[key] = (time.time(), value)


def create_app(settings: Settings | None = None, adapter=None) -> FastAPI:
    s = settings or load_settings()
    setup_logging()
    bus, metrics, history = EventBus(), Metrics(), History()
    reg = Registry(s)
    gate = Gate(s, reg, adapter or make_adapter(s), bus, metrics, history)
    text = TextConversation(gate)
    dedup = _Dedup()
    from .perception.service import Station
    station = Station(s)
    labelling = LabellingService(s.xai_api_key, root=s.data_dir / "labelling")
    cycle = SortingCycle(gate, labelling, bus, camera=station.camera)
    tools_mod.LABELLING = (labelling, cycle)
    tools_mod.PERCEPTION = station
    gate.evidence = station.evidence
    gate.perceive = station
    gate.recheck = station.recheck

    async def _start_cycle_from_voice(product_id: str, operator: str) -> dict[str, Any]:
        return await cycle.start(product_id, operator, layout_confirmed=True)

    gate.cycle_starter = _start_cycle_from_voice

    def _label_voice(intent: Any, lang: str) -> dict[str, Any]:
        pid = (getattr(intent, "params", None) or {}).get("product_id")
        return tools_mod._cycle_voice_tool("propose_labelling_cycle", {"product_id": pid, "lingua": lang}, gate, labelling, cycle)

    gate.label_voice = _label_voice

    def _scene_outcome(skill: str, params: dict, outcome: str) -> None:
        pose = getattr(gate.world, "known_pose", None)
        station.sim.note_pose(pose if isinstance(pose, str) else None)
        station.sim.on_robot(skill, params, outcome)

    gate.on_verified_motion = _scene_outcome
    state: dict[str, Any] = {"voice": None, "grok_text": None, "voice_status": "disconnected"}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log_event(log, "startup", mode=s.mode, warnings=s.warnings)
        await gate.start()                     # never resumes old pending actions (pending is in-memory only)
        watcher = asyncio.ensure_future(_stop_aborts_cycle())
        yield
        watcher.cancel()
        if cycle.phase not in ("IDLE", "DONE", "ABORTED"):
            cycle.abort("shutdown")
        log_event(log, "shutdown")
        if state["grok_text"]:
            await state["grok_text"].close()
        await gate.shutdown()

    async def _stop_aborts_cycle() -> None:
        q = bus.subscribe()
        while True:
            ev = await q.get()
            if ev["type"] == "stop" and cycle.phase not in ("IDLE", "DONE", "ABORTED"):
                cycle.abort(f"STOP ({ev.get('source')})")

    app = FastAPI(title="VOCE", lifespan=lifespan)
    app.include_router(make_router(labelling, cycle))
    app.state.labelling, app.state.cycle = labelling, cycle
    app.state.gate, app.state.bus, app.state.settings, app.state.text = gate, bus, s, text

    def voice_status() -> dict[str, Any]:
        v = state["voice"]
        return {"status": v.status if v else state["voice_status"], "configured": bool(s.xai_api_key)}

    # ------------------------------------------------------------- REST ---
    @app.get("/api/capabilities")
    async def api_capabilities() -> dict[str, Any]:
        from .capabilities import build_capabilities
        cam = station.camera
        age = None
        if cam._ts is not None:
            age = max(0.0, time.time() - cam._ts)
        n = len(list((s.data_dir / "catalog").glob("*/product.json")))
        robot_on = bool(getattr(gate, "robot_status", None) and gate.robot_status.connected)
        return build_capabilities(s, robot_connected=robot_on, frame_age_s=age, catalog_size=n)

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return {**gate.status(), "config": s.public(), "voice": voice_status(), "layout": layout(),
                "catalogue": reg.catalogue(), "server_time": time.time()}

    def layout() -> dict[str, Any]:
        """Schematic only: base-joint angle of each zone's approach pose (for the UI top view)."""
        return {"zones": {z: s.poses[f"{z}_sopra"]["joints"][0] for z in s.zones if f"{z}_sopra" in s.poses},
                "poses": {k: v["joints"] for k, v in s.poses.items() if v.get("selezionabile")}}

    @app.post("/api/stop")
    async def api_stop(body: StopBody | None = None) -> dict[str, Any]:
        return await gate.stop(f"ui:{(body or StopBody()).source}")

    @app.post("/api/reset")
    async def api_reset(body: ResetBody) -> JSONResponse:
        if not body.operator_confirmed:
            raise HTTPException(400, "operator_confirmed must be true")
        r = await gate.reset(operator="ui")
        return JSONResponse(r, status_code=200 if r.get("ok") else 409)

    @app.post("/api/text-command")
    async def api_text(body: TextBody) -> dict[str, Any]:
        cached = dedup.get(body.request_id)
        if cached is not None:
            return {**cached, "duplicate": True}
        if body.engine == "grok":
            if not s.xai_api_key:
                raise HTTPException(503, "Grok non configurato: usa engine=rules")
            if state["grok_text"] is None:
                g = GrokSession(s, gate, bus, metrics, mode="text")
                await g.start_text()
                state["grok_text"] = g
            calls = await state["grok_text"].say_text(body.text)
            out = {"mode": TEXT_MODE_LABEL, "engine": "grok", "input": body.text, "tool_calls": calls,
                   "reply": state["grok_text"].assistant_text[-1] if state["grok_text"].assistant_text else "",
                   "gate": gate.state.value}
        else:
            out = {**await text.handle(body.text, body.lang), "engine": "rules"}
        dedup.put(body.request_id, out)
        return out

    @app.get("/api/camera/scene")
    async def api_camera_scene() -> dict[str, Any]:
        return station.scene()

    def _sim_open() -> None:
        if s.mode == "real" or station.camera.source != "sim":
            raise HTTPException(409, "simulated camera is off")

    @app.get("/api/camera/frame.jpg")
    async def api_camera_jpg() -> Response:
        return await api_camera_get()

    @app.get("/api/sim/scene")
    async def api_sim_scene() -> dict[str, Any]:
        _sim_open()
        return station.sim.public()

    @app.post("/api/sim/scene")
    async def api_sim_set(body: SimZone) -> dict[str, Any]:
        _sim_open()
        if gate.state.value == "EXECUTING":
            raise HTTPException(409, "robot is executing")
        try:
            station.sim.set_zone(body.zona, body.product, body.variant)
        except KeyError:
            raise HTTPException(400, "zona sconosciuta")
        return station.sim.public()

    @app.post("/api/sim/scene/preset")
    async def api_sim_preset(body: SimPreset) -> dict[str, Any]:
        _sim_open()
        if gate.state.value == "EXECUTING":
            raise HTTPException(409, "robot is executing")
        try:
            return station.sim.load_preset(body.name)
        except FileNotFoundError:
            raise HTTPException(404, "preset assente")

    @app.post("/api/sim/scene/reset")
    async def api_sim_reset() -> dict[str, Any]:
        _sim_open()
        if gate.state.value == "EXECUTING":
            raise HTTPException(409, "robot is executing")
        return station.sim.reset()

    @app.get("/api/sim/identify")
    async def api_sim_identify() -> dict[str, Any]:
        _sim_open()
        return {"label": "SIMULATED CAMERA · SYNTHETIC PRODUCTS", "zone": station.zone_rows()}

    @app.get("/api/camera/frame")
    async def api_camera_get() -> Response:
        got = station.camera.get_frame()
        if got is None:
            raise HTTPException(404, "nessuna_immagine")
        from .perception.service import encode_jpeg
        data = encode_jpeg(got[0])
        if not data:
            raise HTTPException(404, "nessuna_immagine")
        return Response(content=data, media_type="image/jpeg")

    async def _jpeg(request: Request):
        import cv2
        import numpy as np
        img = cv2.imdecode(np.frombuffer(await request.body(), np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, "immagine non valida")
        return img

    @app.post("/api/camera/frame")
    async def api_camera_post(request: Request) -> dict[str, Any]:
        if not station.camera.available:
            return {"ok": False, "esito": "nessuna_telecamera"}
        station.camera.push_frame(await _jpeg(request))
        return {"ok": True}

    @app.post("/api/camera/background")
    async def api_camera_background(request: Request) -> dict[str, Any]:
        if not station.camera.available:
            return {"ok": False, "esito": "nessuna_telecamera"}
        station.camera.push_background(await _jpeg(request))
        return {"ok": True}

    class ZonesBody(BaseModel):
        zones: dict[str, list[list[float]]]

    @app.post("/api/camera/zones")
    async def api_camera_zones(body: ZonesBody) -> dict[str, Any]:
        needed = ("sinistra", "centro", "destra")
        if any(len(body.zones.get(z) or []) < 3 for z in needed):
            return {"ok": False, "esito": "zone_non_calibrate"}
        station.camera.save_zones({z: body.zones[z] for z in needed})
        return {"ok": True, "calibrated_at": station.camera.cfg.get("calibrated_at")}

    @app.get("/api/eval/latest")
    async def api_eval() -> JSONResponse:
        p = ROOT / "eval" / "results" / "latest.json"
        if not p.exists():
            return JSONResponse({"available": False})
        data = json.loads(p.read_text())
        lat = ROOT / "eval" / "results" / "latency_latest.json"
        if lat.exists():
            data["latency"] = json.loads(lat.read_text())
        return JSONResponse({"available": True, **data})

    # ------------------------------------------------------- websockets ---
    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        await ws.accept()
        q = bus.subscribe()
        try:
            await ws.send_json({"type": "hello", "config": s.public(), "voice": voice_status(), "layout": layout(),
                                "catalogue": reg.catalogue(), **gate.status()})
            for ev in list(bus.recent)[-60:]:
                if ev["type"] in ("transcript", "assistant_done", "report", "tool", "stop_ack"):
                    await ws.send_json({**ev, "replay": True})
            sender = asyncio.ensure_future(_send_events(ws, q))
            while True:                       # keep reading so disconnects are noticed
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_json({"type": "pong", "ts": time.time()})
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            bus.unsubscribe(q)
            if "sender" in locals():
                sender.cancel()

    async def _send_events(ws: WebSocket, q: asyncio.Queue) -> None:
        while True:
            ev = await q.get()
            try:
                await ws.send_json(ev)
            except Exception:
                return

    @app.websocket("/ws/voice")
    async def ws_voice(ws: WebSocket) -> None:
        await ws.accept()
        if not s.xai_api_key:
            await ws.send_json({"type": "error", "message": "Grok non configurato (XAI_API_KEY): usa TEXT MODE"})
            await ws.close()
            return
        old = state["voice"]
        if old is not None:
            supersede = getattr(old, "supersede", None)
            if supersede is not None:
                await supersede()
                finished = getattr(old, "finished", None)
                if finished is not None:
                    try:
                        await asyncio.wait_for(finished.wait(), 5)
                    except asyncio.TimeoutError:
                        pass
            if state["voice"] is old:
                state["voice"] = None
        lang = ws.query_params.get("lang", "auto")
        if lang not in ("auto", "it", "en", "zh", "ja", "am"):
            lang = "auto"
        sess = GrokSession(s, gate, bus, metrics, client=ws, mode="voice", lang=lang)
        state["voice"] = sess
        try:
            await sess.run_voice()
        finally:
            if state["voice"] is sess:
                state["voice"] = None
                state["voice_status"] = "disconnected"
                bus.publish("voice", status="disconnected", detail="", mode="voice")

    # --------------------------------------------------------- frontend ---
    if (FRONTEND_DIST / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str):
            f = FRONTEND_DIST / path
            if path and f.is_file() and FRONTEND_DIST in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(FRONTEND_DIST / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        async def no_frontend():
            return JSONResponse({"error": "frontend not built: run `cd frontend && npm install && npm run build`"})

    return app
