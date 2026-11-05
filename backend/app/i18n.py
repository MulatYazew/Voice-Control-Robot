"""Operator-facing sentences. Skill ids and stored parameters stay canonical Italian."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from .config import ROOT

_PATH = ROOT / "config" / "i18n" / "messages.yaml"
LANGS = ("it", "en", "zh", "ja", "am")


class _Safe(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


@lru_cache(maxsize=1)
def messages() -> dict[str, Any]:
    if not _PATH.exists():
        return {}
    return yaml.safe_load(_PATH.read_text(encoding="utf-8")) or {}


def localize_params(params: dict[str, str], lang: str) -> dict[str, str]:
    names = (messages().get("zones") or {}).get(lang) or {}
    return {k: names.get(v, v) for k, v in params.items()}


def skill_sentence(skill_id: str, kind: str, lang: str, params: dict[str, str]) -> str | None:
    """kind is conferma or risultato. it/en are owned by skills.yaml; this returns zh/ja/am."""
    block = ((messages().get("skills") or {}).get(skill_id) or {}).get(kind) or {}
    tpl = block.get(lang)
    if not tpl:
        return None
    return tpl.format_map(_Safe(localize_params(params, lang)))


def evidence_sentence(lang: str, *, cosa: str, zona: str, prova: str, conf: str) -> str:
    tpl = (messages().get("evidence") or {}).get(lang)
    if not tpl:
        return ""
    zona_l = localize_params({"zona": zona}, lang).get("zona", zona) if lang != "it" else zona
    if lang == "en":
        zona_l = {"sinistra": "left", "centro": "centre", "destra": "right"}.get(zona, zona)
    return tpl.format_map(_Safe(cosa=cosa, zona=zona_l, prova=prova, conf=conf))


def cycle_extra(phase: str) -> dict[str, str]:
    """zh/ja/am lines. it/en stay the literals in sorting.py."""
    block = (messages().get("cycle") or {}).get(phase) or {}
    return {k: v for k, v in block.items() if k in ("zh", "ja", "am") and isinstance(v, str)}


def looking(lang: str) -> str:
    return (messages().get("looking") or {}).get(lang) or (messages().get("looking") or {}).get("en") or "Looking…"
