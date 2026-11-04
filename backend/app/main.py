"""FastAPI application: REST + WebSockets + production frontend on one URL."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import FRONTEND_DIST, ROOT, Settings, load_settings
from .conversation.text_mode import TEXT_MODE_LABEL, TextConversation
from .monitoring.events import EventBus, History, Metrics, log_event, setup_logging
from .safety.gate import Gate
from .skills.registry import Registry
from .voice.grok_session import GrokSession

log = logging.getLogger("voce.api")


def make_adapter(s: Settings):
    if s.mode == "sim":
        from .robot.sim import SimAdapter
        return SimAdapter(s.poses["home"]["joints"])
    from .robot.ur import URAdapter
    return URAdapter(s)


class StopBody(BaseModel):
    source: Literal["button", "space", "http", "pedal"] = "http"


class ResetBody(BaseModel):
    operator_confirmed: bool = Field(..., description="Must be true: deliberate operator action from the UI")


class TextBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=300)
    engine: Literal["rules", "grok"] = "rules"
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
    state: dict[str, Any] = {"voice": None, "grok_text": None, "voice_status": "disconnected"}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log_event(log, "startup", mode=s.mode, warnings=s.warnings)
        await gate.start()                     # never resumes old pending actions (pending is in-memory only)
        yield
        log_event(log, "shutdown")
        if state["grok_text"]:
            await state["grok_text"].close()
        await gate.shutdown()

    app = FastAPI(title="VOCE", lifespan=lifespan)
    app.state.gate, app.state.bus, app.state.settings, app.state.text = gate, bus, s, text

    def voice_status() -> dict[str, Any]:
        v = state["voice"]
        return {"status": v.status if v else state["voice_status"], "configured": bool(s.xai_api_key)}

    # ------------------------------------------------------------- REST ---
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
        bus.publish("transcript", role="user", text=body.text, final=True, source="text", mode=TEXT_MODE_LABEL)
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
            out = {**await text.handle(body.text), "engine": "rules"}
        dedup.put(body.request_id, out)
        return out

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
        if state["voice"] is not None:        # one operator voice channel at a time
            await ws.send_json({"type": "error", "message": "Un'altra sessione vocale è già attiva."})
            await ws.close()
            return
        sess = GrokSession(s, gate, bus, metrics, client=ws, mode="voice")
        state["voice"] = sess
        try:
            await sess.run_voice()
        finally:
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
