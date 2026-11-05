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
        from .sim_station import SimStation
        self.sim = SimStation(root=settings.data_dir.parent, scenes=settings.config_dir / "scenes")
        if self.camera.source == "sim":
            self.camera.available = True
            self.available = True
            self.camera.renderer = self.sim.render

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
                "simulated": self.camera.source == "sim",
                "label": "SIMULATED CAMERA · SYNTHETIC PRODUCTS" if self.camera.source == "sim" else None,
                "candidates": [{k: v for k, v in p.items() if k != "crop"} for p in props]}

    def zone_rows(self) -> list[dict[str, Any]]:
        from .identify import rows_from_image
        from .sim_station import BANNER, catalog_from_dataset
        if self.camera.source == "sim":
            img = self.sim.render()
            source = BANNER
        else:
            got = self.camera.get_frame()
            if got is None:
                return []
            img, source = got[0], f"camera:{self.camera.source}"
        return rows_from_image(img, catalog_from_dataset(self.sim.root), sorgente=source, mirror=self.sim.mirror)

    def answer_scene(self, params: dict[str, Any]) -> dict[str, Any]:
        """Spoken answers. A move is only proposed; the gate still waits for a fresh yes."""
        from .identify import find_in_rows
        from .sim_station import BANNER
        if self.camera.source != "sim" and not self.available:
            return {"ok": False, "esito": "nessuna_telecamera",
                    "messaggio": "Nessuna telecamera su questa stazione. Non invento ciò che non vedo."}
        rows = self.zone_rows()
        action = params.get("azione")
        if action == "etichetta":
            zone = params.get("posizione") or ""
            row = next((r for r in rows if r["zona"] == zone), None)
            if row is None or row["stato"] != "pack":
                return {"ok": False, "esito": "not_found", "messaggio": "Non vedo un pacco in quella zona.", "zone": rows}
            decision = (row.get("conformita") or {}).get("decisione")
            if not row.get("prodotto", {}).get("id") or decision is None:
                return {"ok": True, "esito": "not_checkable", "zone": rows,
                        "messaggio": "Non posso giudicare la conformità: questo prodotto non ha una specifica approvata."}
            return {"ok": True, "zone": rows, "messaggio": f"{decision}. {row['conformita'].get('motivi')}"}
        if action == "trova":
            found = find_in_rows(rows, tipo=params.get("tipo") or None, nome=params.get("nome") or None)
            if found["stato"] == "ambiguous":
                return {"ok": False, "esito": "ambiguous", "zone": found["zone"],
                        "messaggio": "Ne vedo più di uno: " + " e ".join(found["zone"]) + ". Quale?"}
            if found["stato"] != "match":
                return {"ok": False, "esito": "not_found", "messaggio": "Non lo vedo sul tavolo."}
            return {"ok": True, "zone": found["zone"], "messaggio": f"Lo vedo in {found['zone'][0]}. Non mi muovo."}
        if action == "porta":
            found = find_in_rows(rows, tipo=params.get("tipo") or "cream")
            if found["stato"] == "ambiguous":
                return {"ok": False, "esito": "ambiguous", "zone": found["zone"],
                        "messaggio": "Ne vedo più di uno. Quale zona?"}
            if found["stato"] != "match":
                return {"ok": False, "esito": "not_found", "messaggio": "Non lo vedo sul tavolo."}
            dest = params.get("posizione") or "destra"
            return {"ok": True, "propose": {"skill": "sposta_vassoio",
                                            "params": {"origine": found["zone"][0], "destinazione": dest}},
                    "messaggio": f"Vedo la crema in {found['zone'][0]}. {BANNER}."}
        parts = []
        for row in rows:
            if row["stato"] == "empty":
                parts.append(f"{row['zona']}: vuota")
            elif row.get("prodotto", {}).get("id"):
                parts.append(f"{row['zona']}: {row['prodotto'].get('nome') or row['prodotto']['id']}")
            else:
                parts.append(f"{row['zona']}: prodotto sconosciuto")
        return {"ok": True, "zone": rows, "sorgente": BANNER if self.camera.source == "sim" else self.camera.source,
                "messaggio": "; ".join(parts)}


def encode_jpeg(img: np.ndarray) -> bytes | None:
    import cv2
    ok, buf = cv2.imencode(".jpg", img)
    return bytes(buf) if ok else None
