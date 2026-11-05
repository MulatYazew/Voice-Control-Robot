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


def _apply_view(frame: np.ndarray, view: dict[str, Any]) -> np.ndarray:
    """Crop a configured ROI and flip it when the station uses a mirror."""
    img = frame
    roi = view.get("roi")
    if roi and len(roi) == 4:
        x, y, w, h = (int(v) for v in roi)
        img = frame[max(0, y):max(0, y) + max(0, h), max(0, x):max(0, x) + max(0, w)]
    if view.get("mirror") == "horizontal" and getattr(img, "size", 0):
        import cv2
        img = cv2.flip(img, 1)
    return img

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
        self.interventions = 0
        self.stops = 0
        self.confirm_move = asyncio.Event()

    # ------------------------------------------------------------ public ---
    def status(self) -> dict[str, Any]:
        return {"phase": self.phase, "cycle_id": self.cycle_id, "product_id": self.product_id,
                "required_faces": self.required_faces, "photos_received": sorted(self.photos),
                "decision": (self.result or {}).get("decision"), "inspection_id": (self.result or {}).get("inspection_id"),
                "explanation": (self.result or {}).get("explanation"), "counters": self.counters,
                "interventions": self.interventions, "stops": self.stops,
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
        if sheet and sheet.get("sheet_state") == "SHEET_EMPTY":
            problems.append("foglio etichette esaurito / label sheet finished")
        if self._auto_camera():
            if self.camera.get_frame() is None:
                problems.append("fotogramma assente o non aggiornato")
            try:
                spec = self.svc.get(product_id)
                faces = sorted({l.face for l in spec.labels if l.required})
            except KeyError:
                faces = []
            missing = [f for f in faces if not self._face_coverable(f)]
            if missing:
                problems.append("facce non verificabili dalla stazione: " + ", ".join(missing))
            for name in ("sinistra_sopra", "centro_sopra", "destra_sopra", "centro_spinta_scarto_inizio"):
                if name not in self.gate.s.poses:
                    problems.append(f"posa di destinazione mancante: {name}")
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
        self.interventions = 0
        self.photo_evt.clear()
        self.review_evt.clear()
        self.log = []
        self.task = asyncio.ensure_future(self._run())
        return {"ok": True, "cycle_id": self.cycle_id}

    def _auto_camera(self) -> bool:
        return (self.camera is not None and self.camera.available
                and not str(self.camera.source).startswith("browser"))

    def _face_coverable(self, face: str) -> bool:
        visible = list((self.camera.cfg.get("faces_visible_from_station") or ["front"])) if self.camera else []
        views = (self.camera.cfg.get("views") or {}) if self.camera else {}
        return face in visible or face in views

    def _strict(self) -> bool:
        return bool((self.svc.cfg.get("cycle") or {}).get("confirm_each_move"))

    def add_photo(self, face: str, img: np.ndarray) -> dict[str, Any]:
        if self.phase != "AWAITING_PHOTOS":
            return {"ok": False, "messaggio": f"il ciclo non attende foto (fase {self.phase})"}
        if face not in self.required_faces:
            return {"ok": False, "messaggio": f"faccia {face} non richiesta: {self.required_faces}"}
        self.photos[face] = img
        self.interventions += 1
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
        self.stops += 1
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

    def _halted(self) -> bool:
        cycle = getattr(self.gate, "cycle", None)
        return self.gate.state == GateState.STOPPED or not (cycle and cycle.get("active"))

    async def _settle(self) -> bool:
        seconds = float((self.svc.cfg.get("cycle") or {}).get("settle_s", 0.5))
        end = time.monotonic() + max(0.0, seconds)
        while time.monotonic() < end:
            if self._halted():
                return False
            await asyncio.sleep(min(0.05, end - time.monotonic()))
        return not self._halted()

    async def _capture_fixed_camera(self) -> None:
        """Keep the sharpest of three frames. A mirror view is cropped and flipped. Uploads are not used."""
        if not self._auto_camera():
            return
        if not await self._settle():
            return
        frames: list[np.ndarray] = []
        for _ in range(3):
            if self._halted():
                return
            got = self.camera.get_frame()
            if got is not None:
                frames.append(got[0])
            await asyncio.sleep(0)
        if not frames:
            return
        from .vision import label_sharpness
        best = max(frames, key=lambda im: label_sharpness(im))
        views = self.camera.cfg.get("views") or {}
        for face in self.required_faces:
            if not self._face_coverable(face):
                continue
            img = _apply_view(best, views.get(face) or {})
            if img is None or getattr(img, "size", 0) == 0:
                continue
            self.photos[face] = img
        if set(self.photos) >= set(self.required_faces):
            self.photo_evt.set()
        self._emit()

    async def _move(self, skill: str, params: dict[str, str]) -> bool:
        if self._halted():
            return False
        if self._strict():
            self.confirm_move.clear()
            self._set("AWAIT_MOVE_CONFIRM", f"Confermi la mossa {skill}?", f"Confirm the move {skill}?")
            await self.confirm_move.wait()
            if self._halted():
                return False
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
                await self._capture_fixed_camera()
                if self._halted():
                    self._set("ABORTED", "Ciclo interrotto (STOP).", "Cycle aborted (STOP).")
                    return
                missing = [f for f in self.required_faces if f not in self.photos]
                if self._auto_camera() and missing:
                    self.result = {"decision": REVIEW, "explanation": {
                        "it": "Faccia richiesta non verificata dalla telecamera: nessuna accettazione.",
                        "en": "A required face was not verified by the camera: not accepted."},
                        "findings": [{"code": "unverified_face", "faces": missing}]}
                    decision = REVIEW
                else:
                    try:
                        timeout = self.PHOTO_TIMEOUT_S if not self._auto_camera() else float(
                            (self.svc.cfg.get("cycle") or {}).get("capture_timeout_s", 2.0))
                        await asyncio.wait_for(self.photo_evt.wait(), timeout)
                    except asyncio.TimeoutError:
                        self.result = {"decision": REVIEW, "explanation": {
                            "it": "Foto non ricevute in tempo: serve una verifica umana.",
                            "en": "Photos not received in time: a human check is required."}}
                        decision = REVIEW
                    else:
                        self._set("INSPECTING", "Ispezione in corso.", "Inspecting.")
                        loop = asyncio.get_running_loop()
                        self.result = await loop.run_in_executor(
                            None, lambda: self.svc.inspect(self.product_id, dict(self.photos)))
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
