from __future__ import annotations

import asyncio
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from .calibration import CalibrationError
from .frames import MAX_FRAME_BYTES
from .service import VisionService

STATIC = Path(__file__).resolve().parent / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
NO_STORE = {"Cache-Control": "no-store"}


class LoopbackOnly(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        host = request.client.host if request.client else ""
        if host not in LOOPBACK:
            return JSONResponse({"error": "operator API is local only"}, status_code=403)
        return await call_next(request)


async def _read_frame(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FRAME_BYTES:
            raise ValueError("frame too large")
    return bytes(body)


def _frame_endpoint(service: VisionService):
    async def push_frame(request: Request) -> JSONResponse:
        try:
            frame = service.store.put_bytes(await _read_frame(request))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"id": frame.frame_id, "size": list(frame.size)})

    return push_frame


def operator_app(service: VisionService) -> Starlette:
    async def index(_: Request) -> FileResponse:
        return FileResponse(STATIC / "operator.html", headers=NO_STORE)

    async def status(_: Request) -> JSONResponse:
        return JSONResponse(service.status())

    async def frame_jpg(_: Request) -> Response:
        frame = service.store.latest()
        if frame is None:
            return JSONResponse({"error": "no frame yet"}, status_code=404)
        return Response(frame.jpeg, media_type="image/jpeg", headers=NO_STORE)

    async def last_locate_jpg(_: Request) -> Response:
        if service.last_annotated is None:
            return JSONResponse({"error": "no locate yet"}, status_code=404)
        return Response(service.last_annotated, media_type="image/jpeg", headers=NO_STORE)

    async def background(_: Request) -> JSONResponse:
        try:
            return JSONResponse(await asyncio.to_thread(service.capture_background))
        except (LookupError, CalibrationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    async def calibration(request: Request) -> JSONResponse:
        if request.method == "GET":
            cal = service.calibration
            if cal is None:
                return JSONResponse({"calibration": None})
            points = [{"px": list(p.px), "robot_mm": list(p.robot_mm)} for p in cal.points]
            return JSONResponse({"calibration": cal.summary(), "points": points})
        try:
            data = await request.json()
            summary = await asyncio.to_thread(service.save_calibration, data["points"])
        except (LookupError, CalibrationError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (KeyError, TypeError, ValueError) as exc:
            return JSONResponse({"error": f"bad request: {exc}"}, status_code=400)
        return JSONResponse({"calibration": summary})

    async def locate(request: Request) -> JSONResponse:
        try:
            data = await request.json()
            query = str(data.get("query", ""))[:300]
            lang = data.get("lang")
            expect = data.get("expect_mm")
            expect_mm = None if expect is None else (float(expect[0]), float(expect[1]))
            tolerance = float(data.get("tolerance_mm", 15.0))
        except (TypeError, ValueError, IndexError) as exc:
            return JSONResponse({"error": f"bad request: {exc}"}, status_code=400)
        result = await asyncio.to_thread(service.locate, query, lang, expect_mm, tolerance)
        return JSONResponse(result.public())

    routes = [
        Route("/", index),
        Route("/api/status", status),
        Route("/api/frame.jpg", frame_jpg),
        Route("/api/frame", _frame_endpoint(service), methods=["POST"]),
        Route("/api/locate/last.jpg", last_locate_jpg),
        Route("/api/background", background, methods=["POST"]),
        Route("/api/calibration", calibration, methods=["GET", "POST"]),
        Route("/api/locate", locate, methods=["POST"]),
    ]
    return Starlette(routes=routes, middleware=[Middleware(LoopbackOnly)])


def phone_app(service: VisionService) -> Starlette:
    """Exposed on the LAN: it can only receive frames, never read results or change setup."""

    async def index(_: Request) -> FileResponse:
        return FileResponse(STATIC / "phone.html", headers=NO_STORE)

    async def ping(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/ping", ping),
            Route("/api/frame", _frame_endpoint(service), methods=["POST"]),
        ]
    )
