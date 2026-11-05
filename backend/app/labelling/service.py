"""Smart Labelling service: one object the API, the voice tools and the sorting cycle share."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from ..config import CONFIG_DIR, ROOT
from . import compliance, ocr
from . import vision as V
from .label_verification import verify_sheet
from .model_reader import derive_mandatory, read_model_pack
from .specification import ProductSpec, SpecStore


class ApprovalError(Exception):
    pass


class LabellingService:
    def __init__(self, api_key: str | None, *, root: Path | None = None, config_dir: Path = CONFIG_DIR) -> None:
        self.cfg = yaml.safe_load((config_dir / "labelling.yaml").read_text())
        self.camera_cfg = yaml.safe_load((config_dir / "camera.yaml").read_text())
        self.root = root or (ROOT / self.camera_cfg["storage"]["root"])
        self.store = SpecStore(self.root)
        self.insp_dir = self.root / "inspections"
        self.insp_dir.mkdir(parents=True, exist_ok=True)
        self.api_key = api_key
        self.sheet_results: dict[str, dict[str, Any]] = {}   # product_id -> last sheet verification
        self.label_checks: list[dict[str, Any]] = []

    def status(self) -> dict[str, Any]:
        return {"ocr_available": ocr.available(), "grok_configured": bool(self.api_key),
                "vision_model": self.cfg["grok"]["vision_model"], "camera": self.camera_cfg["camera"],
                "suggested_tolerances": dict(self.cfg.get("suggested_tolerances") or {}),
                "tolerances_require_operator": True,
                "products": self.store.list()}

    def sheet_opinion(self, spec: ProductSpec, image: np.ndarray, deterministic: dict[str, Any]) -> dict[str, Any]:
        """Ask Grok whether the sheet matches. Raises on timeout or any failure. Never edits INCI text."""
        from .grok_vision import ask_json, image_part
        import cv2
        ok, buf = cv2.imencode(".jpg", image)
        if not ok:
            raise TimeoutError("immagine non codificabile")
        labels = [{"label_id": i.get("label_id"), "found": i.get("found")} for i in deterministic.get("labels") or []]
        schema = {"type": "object", "additionalProperties": False, "required": ["match", "per_label", "explanation"],
                  "properties": {"match": {"type": "boolean"},
                                 "per_label": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                              "required": ["label_id", "found", "notes"],
                                              "properties": {"label_id": {"type": "string"}, "found": {"type": "boolean"},
                                                             "notes": {"type": "string"}}}},
                                 "explanation": {"type": "string"}}}
        system = ("Compare the label sheet with the approved crops. Do not rewrite or correct any INCI name. "
                  "Reply only with the JSON schema. Your answer is advisory.")
        out, _meta = ask_json(self.api_key, self.cfg["grok"]["vision_model"], system,
                              [{"type": "text", "text": json.dumps({"product": spec.product_name, "labels": labels}, ensure_ascii=False)},
                               image_part(buf.tobytes())],
                              schema, "sheet_check", timeout=float(self.cfg["grok"].get("timeout_s") or 90))
        return out

    # -------------------------------------------------------------- model ---
    def learn(self, photos: dict[str, bytes], name_hint: str | None = None) -> ProductSpec:
        return read_model_pack(photos, self.store, self.cfg, api_key=self.api_key, product_hint=name_hint)

    def get(self, product_id: str, version: int | None = None) -> ProductSpec:
        s = self.store.load(product_id, version)
        if s is None:
            raise KeyError(product_id)
        return s

    def approve(self, product_id: str, *, operator: str, tolerances: dict[str, dict[str, float]],
                confirmed: dict[str, list[str]], edits: dict[str, dict[str, Any]] | None = None) -> ProductSpec:
        """Operator approval. Every tolerance must be given, every uncertain field explicitly confirmed."""
        s = self.get(product_id)
        if s.approval_status == "approved":
            s = self.store.new_version(s)
        for lid, ed in (edits or {}).items():
            l = s.label(lid)
            if l is None:
                raise ApprovalError(f"etichetta sconosciuta {lid}")
            for k in ("reference_text", "product_name", "brand", "legal_text", "required"):
                if k in ed:
                    setattr(l, k, ed[k])
            if "kind" in ed:
                l.kind = ed["kind"]
                l.mandatory_text = ed["kind"] == "ingredients_legal"
            if l.mandatory_text and ("reference_text" in ed or "kind" in ed):
                derive_mandatory(l)
            if "inci" in ed:
                l.inci = [ocr.normalise(x) for x in ed["inci"]] or None
        for l in s.labels:
            t = tolerances.get(l.label_id) or {}
            if t.get("position_pct") is not None and t.get("angle_deg") is not None:
                if not (0 < float(t["position_pct"]) <= 50 and 0 < float(t["angle_deg"]) <= 45):
                    raise ApprovalError(f"{l.label_id}: tolleranza fuori intervallo")
                l.tolerance.position_pct, l.tolerance.angle_deg = float(t["position_pct"]), float(t["angle_deg"])
                l.tolerance.set_by = operator
            ok = set(confirmed.get(l.label_id, []))
            l.uncertain = [u for u in l.uncertain if u not in ok]
        blockers = s.approval_blockers()
        if blockers:
            self.store.save(s)
            raise ApprovalError("; ".join(blockers))
        s.approval_status, s.approved_by, s.approved_at = "approved", operator, time.time()
        self.store.save(s)
        try:
            from ..perception.catalog import enroll_approved
            enroll_approved(s, self.store.product_dir(s.product_id, s.version), self.root.parent / "catalog")
        except Exception:
            pass
        return s

    # -------------------------------------------------------------- sheet ---
    def verify_sheet(self, product_id: str, image: bytes) -> dict[str, Any]:
        s = self.get(product_id)
        img = V.load(image)
        d = self.store.product_dir(s.product_id, s.version)
        r = verify_sheet(s, img, d, self.cfg)
        if (self.cfg.get("grok") or {}).get("sheet_check"):
            from .label_verification import apply_sheet_opinion
            try:
                opinion = self.sheet_opinion(s, img, r)
            except Exception:
                opinion = None
            r = apply_sheet_opinion(r, opinion)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        cv2.imwrite(str(d / f"sheet_{stamp}.jpg"), img)
        r.update(product_id=s.product_id, spec_version=s.version, image=f"sheet_{stamp}.jpg", ts=time.time())
        self.sheet_results[product_id] = r
        return r

    # ---------------------------------------------------------- inspection ---
    def inspect_labels(self, product_id: str, photos: dict[str, bytes | np.ndarray], label_ids: list[str], *,
                       use_grok: bool = False) -> dict[str, Any]:
        """Check only the labels applied so far. No arm movement."""
        s = self.get(product_id)
        wanted = list(label_ids)
        if not wanted or any(s.label(i) is None for i in wanted):
            return {"ok": False, "esito": "etichetta_sconosciuta", "motion": False}
        subset = s.model_copy(deep=True)
        for label in subset.labels:
            label.required = label.label_id in wanted
        imgs = {f: (p if isinstance(p, np.ndarray) else V.load(p)) for f, p in photos.items()}
        iid = compliance.new_inspection_id()
        out = self.insp_dir / iid
        out.mkdir(parents=True)
        for face, img in imgs.items():
            cv2.imwrite(str(out / f"photo_{face}.jpg"), img)
        r = compliance.inspect(subset, imgs, self.cfg, self.store.product_dir(s.product_id, s.version), out_dir=out,
                               api_key=self.api_key, use_grok=use_grok)
        mapped = {"COMPLIANT": "LABEL_OK", "NON_COMPLIANT": "LABEL_DEFECTIVE"}.get(r["decision"], "REVIEW")
        r["label_check"] = mapped
        r["label_ids"] = wanted
        r["motion"] = False
        if mapped == "LABEL_DEFECTIVE" and "front" in wanted and s.label("back") is not None:
            r["advice"] = {"it": "Non applicare l'etichetta posteriore: la frontale è difettosa.",
                           "en": "Don't apply the back label: the front is defective."}
        self.label_checks.append({"product_id": product_id, "label_ids": wanted, "label_check": mapped,
                                  "inspection_id": r.get("inspection_id")})
        (out / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return r

    def inspect(self, product_id: str, photos: dict[str, bytes | np.ndarray], *, use_grok: bool = True) -> dict[str, Any]:
        s = self.get(product_id)
        imgs = {f: (p if isinstance(p, np.ndarray) else V.load(p)) for f, p in photos.items()}
        iid = compliance.new_inspection_id()
        out = self.insp_dir / iid
        out.mkdir(parents=True)
        for f, img in imgs.items():
            cv2.imwrite(str(out / f"photo_{f}.jpg"), img)
        r = compliance.inspect(s, imgs, self.cfg, self.store.product_dir(s.product_id, s.version), out_dir=out,
                               api_key=self.api_key, use_grok=use_grok)
        (out / "result.json").write_text(json.dumps(r, ensure_ascii=False, indent=1, default=str))
        return r

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        items = []
        for d in sorted(self.insp_dir.iterdir(), reverse=True)[:limit]:
            f = d / "result.json"
            if f.exists():
                r = json.loads(f.read_text())
                items.append({k: r.get(k) for k in ("inspection_id", "product_id", "spec_version", "decision", "explanation", "ts")})
        return items

    def inspection(self, iid: str) -> dict[str, Any]:
        return json.loads((self.insp_dir / iid / "result.json").read_text())
