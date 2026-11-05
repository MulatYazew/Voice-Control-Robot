"""Labelling production cycle: one pack, from the input area to accepted / reject / operator review.

  input (sinistra) --fork--> work area under the camera (centro) --photos--> inspection
     COMPLIANT        -> fork: centro -> accepted area (destra)
     NON_COMPLIANT    -> pusher: centro -> reject box (spingi direzione=scarto)
     REVIEW_REQUIRED  -> NO movement; the operator decides "reinspect" or "reject" (never "accept")

Every movement goes through Gate.run_cycle_step (predefined skills only, STOP aborts the cycle).
Photos come from the operator's browser camera / uploads, or from a simulated station feed (dataset images).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np

from ..monitoring.events import EventBus, log_event
from ..safety.gate import Gate, GateState
from .compliance import COMPLIANT, NON_COMPLIANT, REVIEW
from .service import LabellingService

log = logging.getLogger("voce.cycle")

PHASES = ("IDLE", "BRINGING", "AWAITING_PHOTOS", "INSPECTING", "ROUTING_ACCEPT", "ROUTING_REJECT", "HOLD_REVIEW",
          "DONE", "ABORTED")


class SortingCycle:
    PHOTO_TIMEOUT_S = 300

    def __init__(self, gate: Gate, svc: LabellingService, bus: EventBus, camera: Any = None) -> None:
        self.gate, self.svc, self.bus, self.camera = gate, svc, bus, camera
        self.phase = "IDLE"
        self.cycle_id: str | None = None
        self.product_id: str | None = None
        self.required_faces: list[str] = []
        self.photos: dict[str, np.ndarray] = {}
        self.photo_evt = asyncio.Event()
        self.review_evt = asyncio.Event()
        self.review_choice: str | None = None
        self.result: dict[str, Any] | None = None
        self.counters = {"accepted": 0, "rejected": 0, "review": 0}
        self.message: dict[str, str] = {"it": "", "en": ""}
        self.task: asyncio.Task | None = None
        self.log: list[dict[str, Any]] = []

    # ------------------------------------------------------------ public ---
    def status(self) -> dict[str, Any]:
        return {"phase": self.phase, "cycle_id": self.cycle_id, "product_id": self.product_id,
                "required_faces": self.required_faces, "photos_received": sorted(self.photos),
                "decision": (self.result or {}).get("decision"), "inspection_id": (self.result or {}).get("inspection_id"),
                "explanation": (self.result or {}).get("explanation"), "counters": self.counters,
                "message": self.message, "log": self.log[-12:]}

    def preflight(self, product_id: str) -> list[str]:
        """Conditions that must hold before the operator may start a cycle."""
        problems = []
        try:
            spec = self.svc.get(product_id)
        except KeyError:
            return ["prodotto sconosciuto / unknown product"]
        if spec.approval_status != "approved":
            problems.append("specifica non approvata / specification not approved")
        sheet = self.svc.sheet_results.get(product_id)
        if not sheet or not sheet.get("ok") or sheet.get("spec_version") != spec.version:
            problems.append("foglio etichette non verificato per questa versione / label sheet not verified for this version")
        if self.task and not self.task.done():
            problems.append("un ciclo è già in corso / a cycle is already running")
        if self.gate.state != GateState.IDLE:
            problems.append(f"robot non pronto (stato {self.gate.state.value}) / robot not ready")
        return problems

    async def start(self, product_id: str, operator: str, layout_confirmed: bool) -> dict[str, Any]:
        if not layout_confirmed:
            return {"ok": False, "problems": ["confermare la disposizione: pacco a sinistra, centro e destra liberi / "
                                              "confirm layout: pack on the left, centre and right free"]}
        problems = self.preflight(product_id)
        if problems:
            return {"ok": False, "problems": problems}
        # the operator has confirmed the physical layout: reset the ASSUMED object state accordingly
        self.gate.world.trays_at = {"sinistra"}
        self.gate.world.holding_tray = False
        auth = self.gate.authorize_cycle(product_id, operator)
        if not auth.get("ok"):
            return {"ok": False, "problems": [auth.get("messaggio", "autorizzazione negata")]}
        spec = self.svc.get(product_id)
        self.cycle_id, self.product_id = auth["cycle_id"], product_id
        self.required_faces = sorted({l.face for l in spec.labels if l.required})
        self.photos, self.result, self.review_choice = {}, None, None
        self.photo_evt.clear()
        self.review_evt.clear()
        self.log = []
        self.task = asyncio.ensure_future(self._run())
        return {"ok": True, "cycle_id": self.cycle_id}

    def add_photo(self, face: str, img: np.ndarray) -> dict[str, Any]:
        if self.phase != "AWAITING_PHOTOS":
            return {"ok": False, "messaggio": f"il ciclo non attende foto (fase {self.phase})"}
        if face not in self.required_faces:
            return {"ok": False, "messaggio": f"faccia {face} non richiesta: {self.required_faces}"}
        self.photos[face] = img
        self._emit()
        if set(self.photos) >= set(self.required_faces):
            self.photo_evt.set()
        return {"ok": True, "missing": [f for f in self.required_faces if f not in self.photos]}

    def review_decision(self, choice: str) -> dict[str, Any]:
        if self.phase != "HOLD_REVIEW":
            return {"ok": False, "messaggio": "nessun pacco in revisione / no pack under review"}
        if choice not in ("reinspect", "reject"):
            return {"ok": False, "messaggio": "scelte ammesse: reinspect, reject (accettare non è ammesso)"}
        self.review_choice = choice
        self.review_evt.set()
        return {"ok": True}

    def abort(self, reason: str) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
        self._set("ABORTED", f"Ciclo interrotto: {reason}", f"Cycle aborted: {reason}")
        self.gate.end_cycle(reason)

    # ----------------------------------------------------------- internal ---
    def _emit(self) -> None:
        self.bus.publish("cycle", **self.status())

    def _set(self, phase: str, it: str, en: str) -> None:
        from ..i18n import cycle_extra
        self.phase = phase
        self.message = {"it": it, "en": en, **cycle_extra(phase)}
        self.log.append({"ts": time.time(), "phase": phase, "it": it, "en": en})
        log_event(log, f"cycle {self.cycle_id} {phase}", it=it)
        self._emit()

    def _capture_fixed_camera(self) -> None:
        """Front face from a fixed station camera. The back still needs the operator to turn the pack."""
        cam = self.camera
        if cam is None or not getattr(cam, "available", False):
            return
        if str(getattr(cam, "source", "browser")).startswith("browser"):
            return
        if "front" not in self.required_faces or "front" in self.photos:
            return
        got = cam.get_frame()
        if got is None:
            return
        self.add_photo("front", got[0])

    async def _move(self, skill: str, params: dict[str, str]) -> bool:
        r = await self.gate.run_cycle_step(self.cycle_id, skill, params)
        ok = getattr(r, "success", False)
        if not ok:
            err = getattr(r, "error", None) or (r.get("messaggio") if isinstance(r, dict) else "") or self.gate.state.value
            self._set("ABORTED", f"Movimento {skill} {params} non riuscito: {err}. Ciclo fermato.",
                      f"Movement {skill} {params} failed: {err}. Cycle stopped.")
            self.gate.end_cycle(f"movimento fallito: {err}")
        return ok

    async def _run(self) -> None:
        try:
            self._set("BRINGING", "Porto il pacco dall'area di ingresso all'area di lavoro.",
                      "Moving the pack from the input area to the work area.")
            if not await self._move("solleva_vassoio", {"posizione": "sinistra"}):
                return
            if not await self._move("appoggia_vassoio", {"posizione": "centro"}):
                return
            while True:
                self.photos = {}
                self.photo_evt.clear()
                self._set("AWAITING_PHOTOS", "Inquadra il pacco: foto delle facce " + ", ".join(self.required_faces),
                          "Frame the pack: photos of faces " + ", ".join(self.required_faces))
                self._capture_fixed_camera()
                try:
                    await asyncio.wait_for(self.photo_evt.wait(), self.PHOTO_TIMEOUT_S)
                except asyncio.TimeoutError:
                    self.result = None
                    decision = REVIEW
                    self._set("HOLD_REVIEW", "Nessuna foto ricevuta: pacco trattenuto per revisione.",
                              "No photos received: pack held for review.")
                else:
                    self._set("INSPECTING", "Ispezione in corso.", "Inspecting.")
                    loop = asyncio.get_running_loop()
                    self.result = await loop.run_in_executor(None, lambda: self.svc.inspect(self.product_id, dict(self.photos)))
                    decision = self.result["decision"]
                if not self.gate.cycle or not self.gate.cycle.get("active"):
                    self._set("ABORTED", "Ciclo interrotto (STOP).", "Cycle aborted (STOP).")
                    return
                if decision == COMPLIANT:
                    self._set("ROUTING_ACCEPT", "Conforme: porto il pacco nell'area accettati.",
                              "Compliant: moving the pack to the accepted area.")
                    if await self._move("solleva_vassoio", {"posizione": "centro"}) and \
                            await self._move("appoggia_vassoio", {"posizione": "destra"}):
                        self.counters["accepted"] += 1
                        self._done("Pacco conforme depositato nell'area accettati.", "Compliant pack placed in the accepted area.")
                    return
                if decision == NON_COMPLIANT:
                    await self._reject()
                    return
                # REVIEW_REQUIRED: hold, never accept
                self.counters["review"] += 1
                self._set("HOLD_REVIEW", "Revisione richiesta: il pacco resta nell'area di lavoro. Scegli: ispeziona di nuovo o scarta.",
                          "Review required: the pack stays in the work area. Choose: inspect again or reject.")
                self.review_evt.clear()
                await self.review_evt.wait()
                if self.review_choice == "reject":
                    await self._reject()
                    return
                # reinspect: loop back to photos
        except asyncio.CancelledError:
            raise
        except Exception as e:  # pragma: no cover - defensive
            log.exception("cycle failed")
            self._set("ABORTED", f"Errore del ciclo: {e}", f"Cycle error: {e}")
            self.gate.end_cycle(f"errore: {e}")

    async def _reject(self) -> None:
        self._set("ROUTING_REJECT", "Non conforme: spingo il pacco nel contenitore scarti.",
                  "Non-compliant: pushing the pack into the reject box.")
        if await self._move("spingi", {"posizione": "centro", "direzione": "scarto"}):
            self.counters["rejected"] += 1
            self._done("Pacco scartato.", "Pack rejected.")

    def _done(self, it: str, en: str) -> None:
        self._set("DONE", it, en)
        self.gate.end_cycle("completato")
