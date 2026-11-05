"""Deterministic keyword-rule parser for Italian commands.

Used for (1) TEXT MODE when Grok is unavailable and (2) the evaluation
baseline (eval/baseline_rules.py). It never guesses a missing parameter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ..safety import phrases
from ..safety.phrases import skill_triggers
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
    n = phrases.normalize(text)
    has_kana = any(0x3040 <= ord(ch) <= 0x30FF for ch in n)
    has_han = any(0x4E00 <= ord(ch) <= 0x9FFF for ch in n)
    has_eth = any(0x1200 <= ord(ch) <= 0x137F for ch in n)
    if has_kana:
        return "ja"
    if has_han:
        return "zh"
    if has_eth:
        return "am"
    toks = set(phrases.tokens(text))
    en, it = len(toks & EN_WORDS), len(toks & IT_WORDS)
    return "en" if en > it else ("it" if it > en else default)


_COLOURS = {
    "rosso": ["rosso", "rossa", "rossi", "rosse", "red", "红色", "赤い", "ቀይ"],
    "blu": ["blu", "blue", "蓝色", "青い", "ሰማያዊ"],
    "verde": ["verde", "green", "绿色", "緑", "አረንጓዴ"],
    "giallo": ["giallo", "gialla", "yellow", "黄色", "黄色い"],
    "bianco": ["bianco", "bianca", "white", "白色", "白い"],
    "nero": ["nero", "nera", "black", "黑色", "黒い"],
}
_OBJECTS = ["tazza", "bicchiere", "bottiglia", "shampoo", "cup", "bottle", "杯子", "瓶", "洗发水", "シャンプー", "ሻምፑ"]
_BRING = ["portami", "portami", "bring me", "bring the", "拿到我这边", "持ってきて", "አምጣ"]
_WHERE = ["dov e", "dove", "where is", "where s", "在哪", "どこ", "የት"]
_LABEL_LEARN = ["impara questo", "impara il prodotto", "learn this product", "learn the product", "学习这个", "覚えて", "ይማር"]
_LABEL_SHEET = ["foglio etichette", "label sheet", "标签纸", "ラベルシート", "የመለያ ወረቀት"]
_LABEL_CYCLE = ["avvia il ciclo", "start the cycle", "开始循环", "サイクルを開始", "ዑደቱን ጀምር"]
_LABEL_WHY = ["perche l hai scartato", "perche l'hai scartato", "why was it rejected", "why did you reject", "为什么报废", "なぜ捨て", "ለምን ጣል"]
_VAGUE_NONLATIN = ["那边", "那里", "そこ", "あっち", "እዚያ"]
_OOS_NONLATIN = ["画一个", "画圈", "円を描", "ክብ ሳል"]
_UNREACHABLE = ("scaffale", "shelf", "架子", "棚から", "መደርደሪያ")


def _colour_in(t: str) -> str | None:
    for canon, words in _COLOURS.items():
        if any(w in t for w in words):
            return canon
    return None


def _tray_move(t: str) -> Intent | None:
    if not any(w in t for w in ("vassoio", "tray", "托盘", "トレイ", "ትሪ")):
        return None
    zones = _zones_in(t)
    if len(zones) < 2:
        return None
    if not any(w in t for w in ("porta", "sposta", "move the tray", "bring the tray", "搬", "移", "አንቀሳቅስ")):
        return None
    return Intent("skill", skill="sposta_vassoio", params={"origine": zones[0][1], "destinazione": zones[1][1]})


def _object_intent(t: str) -> Intent | None:
    if any(w in t for w in ("vassoio", "tray", "托盘", "トレイ")) and "tazza" not in t and "cup" not in t and "杯子" not in t:
        return None
    colour = _colour_in(t)
    noun = next((w for w in _OBJECTS if w in t), None)
    if not colour and not noun:
        return None
    params = {k: v for k, v in (("colore", colour), ("categoria", "tazza" if noun in ("tazza", "cup", "杯子") else None),
                                ("testo", noun if noun not in ("tazza", "cup", "杯子", "bottiglia", "bottle", "瓶") else None)) if v}
    if any(w in t for w in _WHERE):
        params["azione"] = "cerca"
        return Intent("locate", params=params)
    if any(w in t for w in _BRING) or "portami" in t:
        params["azione"] = "porta"
        return Intent("locate", params=params)
    if noun or colour:
        params["azione"] = "cerca"
        return Intent("locate", params=params)
    return None


def _label_intent(t: str) -> Intent | None:
    if any(w in t for w in _LABEL_LEARN):
        return Intent("label_learn")
    if any(w in t for w in _LABEL_SHEET):
        return Intent("label_sheet")
    if any(w in t for w in _LABEL_CYCLE):
        return Intent("label_cycle")
    if any(w in t for w in _LABEL_WHY):
        return Intent("label_explain")
    return None


def parse(text: str, *, poses: dict[str, Any] | None = None, pending: bool = False) -> Intent:
    t = _n(text)
    if not t:
        return Intent("none")
    if phrases.is_stop(text):
        return Intent("stop")
    if any(w in t for w in _UNREACHABLE):
        return Intent("out_of_scope")
    scene = _scene_intent(t)
    if scene:
        return scene
    if pending:
        v = phrases.confirmation_verdict(text)
        if v == phrases.VALID:
            return Intent("confirm", verdict=v)
        if v in (phrases.NEGATIVE, phrases.UNCERTAIN) and not _skill_of(t):
            return Intent("cancel" if v == phrases.NEGATIVE else "confirm", verdict=v)
    elif phrases.is_cancel(text) and not _skill_of(t):
        return Intent("cancel")
    located = _object_intent(t)
    if located:
        return located
    labelled = _label_intent(t)
    if labelled:
        return labelled
    skill = _skill_of(t)
    if skill is None:
        moved = _tray_move(t)
        if moved:
            return moved
        if _any(OUT_OF_SCOPE, t) or any(w in t for w in _OOS_NONLATIN):
            return Intent("out_of_scope")
        if _any(VAGUE_MOVE, t) or any(w in t for w in _VAGUE_NONLATIN):
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


def _scene_intent(t: str) -> Intent | None:
    """What is on the table. 'Guarda…' stays out of scope: that sentence is a refusal case."""
    if "guarda" in t:
        return None
    if "cosa c e sul tavolo" in t or "what do you see" in t or "what is on the table" in t:
        return Intent("scene", params={"azione": "tavolo"})
    if "etichetta" in t and any(w in t for w in ("corretta", "giusta", "correct")):
        zones = _zones_in(t)
        return Intent("scene", params={"azione": "etichetta", "posizione": zones[0][1] if zones else ""})
    if "dov e la crema" in t or "dove e la crema" in t or "where is the cream" in t:
        return Intent("scene", params={"azione": "trova", "tipo": "cream"})
    if "cos e questo prodotto" in t or "what is this product" in t:
        return Intent("scene", params={"azione": "tavolo"})
    if "trova il prodotto" in t or "find the product" in t:
        nome = t.split("prodotto")[-1].split("product")[-1].strip()
        return Intent("scene", params={"azione": "trova", "nome": nome})
    if "porta la crema" in t or "bring the cream" in t:
        zones = _zones_in(t)
        return Intent("scene", params={"azione": "porta", "tipo": "cream", "posizione": zones[-1][1] if zones else "destra"})
    return None


def _skill_of(t: str) -> str | None:
    for skill, pats in SKILL_PATTERNS:
        if _any(pats, t):
            return skill
    for skill, words in skill_triggers().items():
        if skill == "sposta_vassoio":
            continue
        if any(w and w in t for w in words):
            return skill
    return None
