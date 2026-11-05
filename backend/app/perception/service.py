"""Facade used by the tools and the text mode. Vision never starts a movement."""

from __future__ import annotations

from typing import Any

import numpy as np

from ..config import Settings
from .camera import CameraService
from .catalog import Catalog
from .evidence import EvidenceStore
from .finder import Candidate, Query, decide, proposals_from_frame


class Station:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.camera = CameraService(settings.camera, config_path=settings.config_dir / "camera.yaml")
        self.evidence = EvidenceStore(ttl_s=15.0)
        self.catalog = Catalog(settings.data_dir / "catalog")
        self.available = bool(self.camera.available)

    def locate(self, args: dict[str, Any]) -> dict[str, Any]:
        query = Query.from_args(args)
        if not self.available:
            return decide(query, [], calibrated=False, camera_available=False)
        got = self.camera.get_frame()
        if got is None:
            return {"ok": False, "esito": "nessuna_immagine",
                    "messaggio": "Non ho un fotogramma recente. Non muovo nulla."}
        img, ts = got
        if not self.camera.calibrated():
            return decide(query, [], calibrated=False, camera_available=True)
        raw = proposals_from_frame(img, self.camera.cfg.get("zones_px") or {}, self.camera.background())
        candidates = [self._candidate(p, query) for p in raw]
        result = decide(query, candidates, calibrated=True, camera_available=True)
        result["frame_ts"] = ts
        if result.get("ok"):
            result["evidenza_id"] = self.evidence.put(
                {"zona": result["zona"], "candidato": result["candidato"], "frame_ts": ts})
        return result

    def _candidate(self, prop: dict[str, Any], query: Query) -> Candidate:
        text, score = "", 0.0
        if query.testo:
            try:
                from ..labelling import ocr
                from rapidfuzz import fuzz
                if ocr.available():
                    text = str(ocr.read(prop["crop"]).get("text") or "")
                    score = float(fuzz.partial_ratio(text.lower(), query.testo.lower())) if text else 0.0
            except Exception:
                text = ""
        return Candidate(zona=prop["zona"], colore=prop["colore"], colore_de=float(prop["colore_de"]),
                         testo=text, testo_score=score, fonti=list(prop.get("fonti") or ["CV"]))

    def recheck(self, pending: Any) -> dict[str, Any] | None:
        """Fast deterministic look. Returns an error dict to abort, or None to proceed."""
        eid = (pending.action.extra or {}).get("evidenza_id")
        if not eid or not self.available:
            return None
        ev = self.evidence.get(eid)
        if not ev:
            return {"ok": False, "esito": "evidenza_scaduta", "messaggio": "L'evidenza non è più valida. Nessun movimento."}
        got = self.camera.get_frame()
        if got is None:
            return {"ok": False, "esito": "nessuna_immagine", "messaggio": "Il fotogramma è assente. Nessun movimento."}
        img, _ts = got
        zona = ev.get("zona")
        want = (ev.get("candidato") or {}).get("colore")
        if not zona or not want or not self.camera.calibrated():
            return None
        props = proposals_from_frame(img, self.camera.cfg.get("zones_px") or {}, self.camera.background())
        colours = [p["colore"] for p in props if p["zona"] == zona]
        if colours and want not in colours:
            return {"ok": False, "esito": "scena_cambiata",
                    "messaggio": f"Nella zona {zona} non vedo più {want}. Nessun movimento."}
        return None

    def scene(self) -> dict[str, Any]:
        frame = self.camera.get_frame() if self.available else None
        props: list[dict[str, Any]] = []
        if frame is not None and self.camera.calibrated():
            props = proposals_from_frame(frame[0], self.camera.cfg.get("zones_px") or {}, self.camera.background())
        return {"available": self.available, "calibrated": self.camera.calibrated(),
                "source": self.camera.source, "has_frame": frame is not None,
                "candidates": [{k: v for k, v in p.items() if k != "crop"} for p in props]}


def encode_jpeg(img: np.ndarray) -> bytes | None:
    import cv2
    ok, buf = cv2.imencode(".jpg", img)
    return bytes(buf) if ok else None
