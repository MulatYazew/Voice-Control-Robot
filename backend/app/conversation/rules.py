"""Deterministic keyword-rule parser for Italian commands.

Used for (1) TEXT MODE when Grok is unavailable and (2) the evaluation
baseline (eval/baseline_rules.py). It never guesses a missing parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..safety import phrases
from ..skills.registry import ZONE_ALIASES

SKILL_PATTERNS: list[tuple[str, list[str]]] = [
    # order matters: more specific first
    ("appoggia_vassoio", [r"\bappoggi\w*", r"\bdeposit\w*", r"\bmett\w* giu\b", r"\bposa(lo|re)? il vassoio\b",
                          r"\bposalo\b", r"\blascia(lo)? (il vassoio|giu)\b", r"\bmetti(lo)? (nel|nella|in|a)\b",
                          r"\bsgancia\w*", r"\brilascia\w*", r"\bput (it |the tray )?down\b", r"\bset (it )?down\b",
                          r"\bplace\b", r"\bdrop (it|the tray)\b", r"\bput (it|the tray) (in|on|at)\b"]),
    ("solleva_vassoio", [r"\bsolleva\w*", r"\balza\w*", r"\btira(lo)? su\b", r"\bprendi\w*", r"\braccogli\w*",
                         r"\bpreleva\w*", r"\bsollev\w*", r"\bcarica\w*", r"\blift\w*", r"\bpick\w*", r"\braise\b",
                         r"\btake (the|that) tray\b"]),
    ("spingi", [r"\bsping\w*", r"\bspinta\b", r"\bspintona\w*", r"\bpush\w*"]),
    ("indica", [r"\bindic\w*", r"\bmostra\w*", r"\bpunta\w*", r"\bfammi vedere\b", r"\bsegnala\w*", r"\bpoint\w*",
                r"\bshow\w*", r"\bindicate\b"]),
    ("vai_a_posa", [r"\bvai\b", r"\btorna\w*", r"\bporta(ti)? (il braccio|in|a)\b", r"\bposizionat\w*",
                    r"\bspostati\b", r"\bmuoviti\b", r"\bgo to\b", r"\breturn\b", r"\bparcheggia\w*", r"\bin posa\b",
                    r"\bposizione (di )?(attesa|iniziale|home)\b", r"\bhome\b", r"\bgo (to|back)\b", r"\bgo home\b",
                    r"\bwaiting position\b", r"\bstandby\b"]),
]

VAGUE_MOVE = [r"\bspost\w*", r"\bmuov\w*", r"\bportalo\b", r"\bportala\b", r"\bfallo\b", r"\bdall altra parte\b",
              r"\bla\b(?! zona)", r"\bmove\b", r"\bother side\b", r"\bthere\b", r"\bthat one\b"]
OUT_OF_SCOPE = [r"\bdisegn\w*", r"\bscriv\w*", r"\bcerchi\w*", r"\bquadrat\w*", r"\bball\w*", r"\bcanta\w*",
                r"\bvelocit\w*", r"\bcoordinat\w*", r"\btraiettori\w*", r"\bruota\w*", r"\bgira\w*", r"\bsald\w*",
                r"\bavvita\w*", r"\bafferra\w*", r"\bguarda\w*", r"\bvedi\b", r"\bfoto\w*", r"\bcaffe\b",
                r"\bdraw\w*", r"\bdance\w*", r"\bgrab\w*", r"\bcircle\b", r"\bweather\b", r"\bspeed\b",
                r"\bcoordinates?\b", r"\bphoto\w*", r"\brotate\b", r"\bwrite\b"]

ZONE_WORDS = {
    "sinistra": [r"\bsinistr\w*", r"\bsx\b", r"\bleft\b"],
    "centro": [r"\bcentr\w*", r"\bin mezzo\b", r"\bmezzo\b", r"\bmiddle\b", r"\bcenter\b", r"\bcentre\b"],
    "destra": [r"\bdestr\w*", r"\bdx\b", r"\bright\b"],
}
DIR_MARKERS = r"\b(verso|in direzione|direzione|spostandolo|spingendolo|towards?|to the)\b"


@dataclass
class Intent:
    kind: str                      # skill | stop | confirm | cancel | clarify | out_of_scope | none
    skill: str | None = None
    params: dict[str, str] = field(default_factory=dict)
    verdict: str | None = None
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "skill": self.skill, "params": self.params, "verdict": self.verdict}


def _n(text: str) -> str:
    return phrases.normalize(text).replace("?", " ")


def _any(pats: list[str], t: str) -> bool:
    return any(re.search(p, t) for p in pats)


def _non_latin(alias: str) -> bool:
    return any(ord(ch) > 127 for ch in alias)


def _zones_in(t: str) -> list[tuple[int, str]]:
    found = []
    for z, pats in ZONE_WORDS.items():
        for p in pats:
            for m in re.finditer(p, t):
                found.append((m.start(), z))
    for z, aliases in ZONE_ALIASES.items():
        for alias in aliases:
            if not _non_latin(alias):
                continue
            start = 0
            while True:
                i = t.find(alias, start)
                if i < 0:
                    break
                found.append((i, z))
                start = i + max(1, len(alias))
    return sorted(set(found))


def _pose_in(t: str, poses: dict[str, Any]) -> str | None:
    for name, p in poses.items():
        if not p.get("selezionabile"):
            continue
        cands = [name.replace("_", " ")] + [phrases.normalize(a) for a in p.get("alias", [])]
        if any(re.search(rf"\b{re.escape(c)}\b", t) for c in cands if c):
            return name
    return None


EN_WORDS = {"the", "please", "lift", "tray", "push", "point", "put", "down", "go", "yes", "stop", "left", "right",
            "center", "centre", "cancel", "confirm", "move", "to", "and", "show", "zone", "home", "now", "could", "you"}
IT_WORDS = {"il", "la", "lo", "per", "favore", "solleva", "vassoio", "spingi", "indica", "appoggia", "vai", "si", "sì",
            "ferma", "fermati", "sinistra", "destra", "centro", "annulla", "confermo", "zona", "potresti", "torna", "a"}


def detect_language(text: str, default: str = "it") -> str:
    toks = set(phrases.tokens(text))
    en, it = len(toks & EN_WORDS), len(toks & IT_WORDS)
    return "en" if en > it else ("it" if it > en else default)


def parse(text: str, *, poses: dict[str, Any] | None = None, pending: bool = False) -> Intent:
    t = _n(text)
    if not t:
        return Intent("none")
    if phrases.is_stop(text):
        return Intent("stop")
    if pending:
        v = phrases.confirmation_verdict(text)
        if v == phrases.VALID:
            return Intent("confirm", verdict=v)
        if v in (phrases.NEGATIVE, phrases.UNCERTAIN) and not _skill_of(t):
            return Intent("cancel" if v == phrases.NEGATIVE else "confirm", verdict=v)
    elif phrases.is_cancel(text) and not _skill_of(t):
        return Intent("cancel")
    skill = _skill_of(t)
    if skill is None:
        if _any(OUT_OF_SCOPE, t):
            return Intent("out_of_scope")
        if _any(VAGUE_MOVE, t):
            return Intent("clarify", note="movimento vago")
        zones = _zones_in(t)
        if zones:  # parameter only (answer to a clarification question)
            return Intent("clarify", params={"posizione": zones[0][1]}, note="solo parametro")
        return Intent("out_of_scope")
    params: dict[str, str] = {}
    if skill == "vai_a_posa":
        pose = _pose_in(t, poses or {})
        if pose:
            params["posa"] = pose
        else:
            z = _zones_in(t)
            if z:
                params["posa"] = f"{z[0][1]}_sopra"
    else:
        zones = _zones_in(t)
        if skill == "spingi":
            m = re.search(DIR_MARKERS, t)
            if m:
                after = [z for pos, z in zones if pos > m.start()]
                before = [z for pos, z in zones if pos < m.start()]
                if after:
                    params["direzione"] = after[0]
                if before:
                    params["posizione"] = before[0]
            elif len(zones) >= 2:
                params["posizione"], params["direzione"] = zones[0][1], zones[1][1]
            elif zones:
                params["posizione"] = zones[0][1]
        elif zones:
            params["posizione"] = zones[0][1]
    return Intent("skill", skill=skill, params=params)


def _skill_of(t: str) -> str | None:
    for skill, pats in SKILL_PATTERNS:
        if _any(pats, t):
            return skill
    return None
