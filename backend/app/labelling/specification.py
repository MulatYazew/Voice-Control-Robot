"""Product labelling specification (versioned, language-independent) and its on-disk store.

A specification is produced from the model-pack photos (CV geometry + OCR text + Grok interpretation)
and starts as `pending`. It can only become `approved` through an explicit operator action that sets
every tolerance and resolves every uncertain field. Only approved specifications are used to inspect.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

FACES = ("front", "back", "left", "right", "cap", "bottom")
Face = Literal["front", "back", "left", "right", "cap", "bottom"]


class Tolerance(BaseModel):
    position_pct: float | None = None     # max centre offset, % of face width/height (camera uncalibrated)
    angle_deg: float | None = None        # max rotation vs the model
    set_by: str | None = None             # who set it (operator) — never the system

    @property
    def complete(self) -> bool:
        return self.position_pct is not None and self.angle_deg is not None


class LabelSpec(BaseModel):
    label_id: str                         # canonical, e.g. "front", "back"
    face: Face
    required: bool = True
    kind: str = "other"                   # front_brand | ingredients_legal | other
    # geometry measured by CV on the model-pack photo (relative to the normalised face)
    position: dict[str, float] = Field(default_factory=dict)   # cx, cy, w, h, angle
    region: str | None = None             # e.g. "upper_center" (human-readable)
    orientation: str = "upright"
    # content
    reference_text: str = ""              # OCR of the model label, approved by the operator (source of truth)
    grok_text: str | None = None          # Grok's own reading, for cross-checking only
    product_name: str | None = None
    brand: str | None = None
    has_logo: bool | None = None
    inci: list[str] | None = None         # parsed from reference_text
    legal_text: str | None = None
    mandatory_text: bool = False          # INCI / legal: must match EXACTLY
    tolerance: Tolerance = Field(default_factory=Tolerance)
    uncertain: list[str] = Field(default_factory=list)          # fields needing human confirmation
    reference_crop: str | None = None     # path of the model label crop (relative to the product dir)


class ProductSpec(BaseModel):
    product_id: str
    version: int = 1
    product_name: str | None = None
    brand: str | None = None
    format: str | None = None             # e.g. "50 ml"
    packaging_type: str | None = None     # box | bottle | jar | tube | unknown
    faces: dict[str, str] = Field(default_factory=dict)        # face -> reference image path (relative)
    labels: list[LabelSpec] = Field(default_factory=list)
    approval_status: Literal["pending", "approved", "rejected"] = "pending"
    approved_by: str | None = None
    approved_at: float | None = None
    created_at: float = Field(default_factory=time.time)
    source: dict[str, Any] = Field(default_factory=dict)      # grok model, prompt hash, OCR engine, warnings
    notes: list[str] = Field(default_factory=list)

    def label(self, label_id: str) -> LabelSpec | None:
        return next((l for l in self.labels if l.label_id == label_id), None)

    def approval_blockers(self) -> list[str]:
        b = []
        if not self.labels:
            b.append("nessuna etichetta nella specifica / no labels in the specification")
        for l in self.labels:
            if not l.tolerance.complete:
                b.append(f"{l.label_id}: tolleranza non impostata / tolerance not set")
            if l.uncertain:
                b.append(f"{l.label_id}: campi da confermare / fields to confirm: {', '.join(l.uncertain)}")
            if l.mandatory_text and not l.reference_text.strip():
                b.append(f"{l.label_id}: testo obbligatorio mancante / mandatory text missing")
            if not l.position:
                b.append(f"{l.label_id}: posizione non misurata / position not measured")
        return b


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return s[:48] or "prodotto"


class SpecStore:
    """data/labelling/products/<product_id>/v<k>/spec.json (+ images). Versions are immutable once approved."""

    def __init__(self, root: Path) -> None:
        self.root = root / "products"
        self.root.mkdir(parents=True, exist_ok=True)

    def product_dir(self, product_id: str, version: int) -> Path:
        return self.root / product_id / f"v{version}"

    def new_id(self, base: str) -> str:
        base = slug(base)
        pid, k = base, 2
        while (self.root / pid).exists():
            pid, k = f"{base}_{k}", k + 1
        return pid

    def save(self, spec: ProductSpec) -> Path:
        d = self.product_dir(spec.product_id, spec.version)
        d.mkdir(parents=True, exist_ok=True)
        (d / "spec.json").write_text(spec.model_dump_json(indent=1))
        return d

    def load(self, product_id: str, version: int | None = None) -> ProductSpec | None:
        pdir = self.root / product_id
        if not pdir.exists():
            return None
        versions = sorted(int(p.name[1:]) for p in pdir.glob("v*") if (p / "spec.json").exists())
        if not versions:
            return None
        v = version if version in versions else versions[-1]
        return ProductSpec.model_validate_json((pdir / f"v{v}" / "spec.json").read_text())

    def list(self) -> list[dict[str, Any]]:
        out = []
        for pdir in sorted(self.root.iterdir()):
            s = self.load(pdir.name)
            if s:
                out.append({"product_id": s.product_id, "version": s.version, "product_name": s.product_name,
                            "approval_status": s.approval_status, "labels": [l.label_id for l in s.labels],
                            "created_at": s.created_at})
        return out

    def new_version(self, spec: ProductSpec) -> ProductSpec:
        """Editing an approved spec creates a new pending version (the approved one stays immutable)."""
        if spec.approval_status != "approved":
            return spec
        nv = spec.model_copy(deep=True)
        nv.version += 1
        nv.approval_status, nv.approved_by, nv.approved_at = "pending", None, None
        src, dst = self.product_dir(spec.product_id, spec.version), self.product_dir(nv.product_id, nv.version)
        dst.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.name != "spec.json":
                (dst / f.name).write_bytes(f.read_bytes())
        return nv
