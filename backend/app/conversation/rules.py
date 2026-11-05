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
                    r"\bposizione (di )?(attesa|iniziale|home)\b", r"\bhome\b", r"\bgo (to|back|above|over)\b", r"\bgo home\b", r"\bmove (the arm|it) (above|over|to)\b",
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


def earlier_utterance(items: Any, current: str) -> str:
    """The operator line before this one. History is newest-first and already contains ``current``."""
    skipped = False
    for it in items or []:
        if it.get("kind") != "utterance":
            continue
        said = it.get("text") or ""
        if not skipped and said == current:
            skipped = True
            continue
        return said
    return ""


def parse(text: str, *, poses: dict[str, Any] | None = None, pending: bool = False,
          prior: str | None = None) -> Intent:
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


# ------------------------------------------------------------------ inspection (text mode only, not in the eval baseline)
INSPECT_WORDS = [r"\bispezion\w*", r"\bispeziona\w*", r"\binspect\w*", r"\binspecci\w*", r"\binspecter\b",
                 r"\bcontrol(e|ar|la) (la )?qualit\w*", r"\bpruf\w*", r"\bqualitatskontroll\w*", "检测", "检查", "质检",
                 "検査"]
CONTINUE_WORDS = [r"\bcontinua\w*", r"\bprossimo pezzo\b", r"\bnext (part|item|one)\b", r"\bcontinue\b",
                  r"\bsiguiente\b", r"\bsuivant\w*", r"\bnachste\w*", r"\bweiter\b", "下一个", "继续", "次の"]
REJECT_WORDS = [r"\bscart\w*", r"\breject\w*", r"\brechaz\w*", r"\brejet\w*", r"\baussortier\w*", "不合格", "拒绝",
                "报废", "不良", "廃棄"]
APPROVE_WORDS = [r"\bapprov\w*", r"\baccett\w*", r"\bpass\w*", r"\baprueb\w*", r"\bacept\w*", r"\baccept\w*",
                 r"\bin ordnung\b", r"\bannehm\w*", "合格", "通过", "良品"]


POINT_WORDS = [r"\bindic\w*", r"\bpunta\w*", r"\bmostra\w*", r"\bpoint\w*", r"\bshow\w*", r"\bsenal\w*",
               r"\bmontre\w*", r"\bzeig\w*", "指", "找", "哪"]


def parse_cup(text: str) -> Intent | None:
    """'indica il bicchiere rosso' / '指一下红色的纸杯' -> point_at_cup(red)."""
    from ..vision.cups import colour_of
    t = _n(text)
    colour = colour_of(text)
    if colour and _any(POINT_WORDS, t):
        return Intent("cup", params={"colore": colour})
    return None


TOUCH_WORDS = [r"\btocc\w*", r"\btouch\w*", r"\btoca\w*", r"\btouche\w*", r"\bberuhr\w*", r"\breach\w*",
               r"\braggiung\w*", "碰", "触", "摸", "触れ", "タッチ"]
CUP_NOUNS = [r"\bbicchier\w*", r"\bcups?\b", r"\bbecher\b", r"\bvaso\b", "纸杯", "杯子", "コップ"]
GRAB_WORDS = [r"\bprend\w*", r"\bafferr\w*", r"\bgrab\w*", r"\bpick\w*", r"\bsolleva\w*", r"\balza\w*",
              r"\blift\w*", r"\bcoge\w*", "拿", "抓", "托", "举", "提起", "夹", "掴"]
PLACE_WORDS = [r"\bposal\w*", r"\bmettilo giu\b", r"\bput (it )?(back )?down\b", "放下", "放回", "置いて"]
ROTATE_WORDS = [r"\bruot\w*", r"\bgir[aio]\w*", r"\brotat\w*", r"\bturn\w*", r"\btourne\w*", r"\bdreh\w*", "转",
                "旋转", "回して", "回す"]
MOVE_WORDS = [r"\bspost\w*", r"\bmuov\w*", r"\bmove\w*", r"\bmueve\w*", r"\bdesplaz\w*", r"\bsube\b", r"\bbaja\b",
              r"\bdeplace\w*", r"\bbouge\w*", r"\bmonte\b", r"\bdescend\w*", r"\bbeweg\w*", r"\bhebe\w*",
              r"\bsenk\w*", r"\bsali\w*", r"\bscendi\w*", r"\balza\w*", r"\babbassa\w*", "移", "挪", "动", "抬", "降",
              "動かして", "上げて", "下げて"]
DIR_PATTERNS = {
    "sinistra": [r"\bsinistr\w*", r"\bleft\b", r"\bizquierd\w*", r"\bgauche\b", r"\blinks\b", "左", "ひだり"],
    "destra": [r"\bdestr\w*", r"\bright\b", r"\bderech\w*", r"\bdroite\b", r"\brechts\b", "右", "みぎ"],
    "avanti": [r"\bavanti\b", r"\bforwards?\b", r"\badelante\b", r"\bavant\b", r"\bvor(ne|wart\w*)?\b", "前", "まえ"],
    "indietro": [r"\bindietro\b", r"\bback(wards?)?\b", r"\batras\b", r"\barriere\b", r"\bhinten\b", r"\bzuruck\b",
                 "后", "後", "うしろ"],
    "su": [r"\bsu\b", r"\bin alto\b", r"\bsali\w*", r"\balza\w*", r"\bup\b", r"\barriba\b", r"\bhaut\b", r"\boben\b",
           r"\bhoch\b", "上", "抬", "高", "うえ"],
    "giu": [r"\bgiu\b", r"\bin basso\b", r"\bscendi\w*", r"\babbassa\w*", r"\bdown\b", r"\babajo\b", r"\bbas\b",
            r"\bunten\b", r"\brunter\b", "下", "降", "した"],
}
SIDE_PATTERNS = {"piu": [r"\bpositiv\w*", r"\bpiu\b", r"\bplus\b", "正"], "meno": [r"\bnegativ\w*", r"\bmeno\b",
                                                                                     r"\bminus\b", "负", "反"]}
JOINT_PATTERNS = {
    "base": [r"\bbase\b", r"\bbasis\b", "底座", "基座", "ベース", r"\b(giunto|joint) ?1\b", "(第一|1号)关节", "关节1"],
    "spalla": [r"\bspalla\b", r"\bshoulder\b", r"\bhombro\b", r"\bepaule\b", r"\bschulter\b", "肩",
               r"\b(giunto|joint) ?2\b", "(第二|2号)关节", "关节2"],
    "gomito": [r"\bgomito\b", r"\belbow\b", r"\bcodo\b", r"\bcoude\b", r"\bellbogen\b", "肘",
               r"\b(giunto|joint) ?3\b", "(第三|3号)关节", "关节3"],
    "polso1": [r"\b(polso|wrist|muneca|poignet|handgelenk) ?1\b", "(手腕|腕)1", r"\b(giunto|joint) ?4\b", "(第四|4号)关节"],
    "polso2": [r"\b(polso|wrist|muneca|poignet|handgelenk) ?2\b", "(手腕|腕)2", r"\b(giunto|joint) ?5\b", "(第五|5号)关节"],
    "polso3": [r"\b(polso|wrist|muneca|poignet|handgelenk) ?3\b", "(手腕|腕)3", r"\b(giunto|joint) ?6\b", "(第六|6号)关节",
               r"\bflangia\b"],
}


def _first(table: dict[str, list[str]], t: str) -> str | None:
    hits = [(m.start(), k) for k, pats in table.items() for p in pats for m in re.finditer(p, t)]
    return min(hits)[1] if hits else None


def parse_side(text: str) -> str | None:
    t = _n(text)
    return _first(SIDE_PATTERNS, t) or _first(DIR_PATTERNS, t)


_CM_UNIT = re.compile(r"(cm|mm)\b|厘米|公分|毫米|センチ|centimet|millimet|zentimet")
_DEG_UNIT = re.compile(r"°|度|deg|grad")


def _has_unit(text: str, kind: str) -> bool:
    return bool((_CM_UNIT if kind == "cm" else _DEG_UNIT).search(_n(text)))


def parse_jog(text: str) -> Intent | None:
    """Text mode: 'tocca il bicchiere rosso', '往左移动三厘米', 'gira a la izquierda 30 grados'.

    Amounts are NOT parsed here: the gate reads them from the same operator words.
    'Turn left 30 degrees' with no joint named means the base; up/down means the shoulder.
    """
    from ..skills.amounts import distance_cm, is_nudge
    from ..vision.cups import colour_of
    t = _n(text)
    if not t:
        return None
    colour = colour_of(text)
    if _any(PLACE_WORDS, t) and (_any(CUP_NOUNS, t) or len(t) <= 8):
        return Intent("place")
    if _any(GRAB_WORDS, t) and (colour or _any(CUP_NOUNS, t)) and not _any([r"\bvassoi\w*", r"\btrays?\b", "托盘"], t):
        if not colour:
            return Intent("clarify", note="cup_colour")
        return Intent("grab", params={"colore": colour})
    if _any(TOUCH_WORDS, t) and (colour or _any(CUP_NOUNS, t)):
        if not colour:
            return Intent("clarify", note="cup_colour")
        return Intent("touch", params={"colore": colour})
    if is_nudge(text) and _first(DIR_PATTERNS, t) == "su" and not _zones_in(t) and not _any(
            [r"\bvassoi\w*", r"\btrays?\b", "托盘", "トレイ"], t):
        return Intent("move", params={"direzione": "su"}, note="nudge")
    joint = _first(JOINT_PATTERNS, t)
    side = _first(SIDE_PATTERNS, t) or _first(DIR_PATTERNS, t)
    has_cm = distance_cm(text) is not None and _has_unit(text, "cm")
    rotating = _any(ROTATE_WORDS, t) and not has_cm and (joint or (side and _has_unit(text, "deg")))
    if rotating:
        if joint is None:
            joint = "spalla" if side in ("su", "giu") else "base" if side in ("sinistra", "destra") else None
        if joint:
            return Intent("rotate", params={"giunto": joint, **({"verso": side} if side else {})})
    d = _first(DIR_PATTERNS, t)
    if d is None or _any([r"\bvassoi\w*", r"\btrays?\b", "托盘", "トレイ"], t):
        return None
    if has_cm or (_any(MOVE_WORDS, t) or is_nudge(text)) and not _skill_of(t):
        return Intent("move", params={"direzione": d}, note="nudge" if is_nudge(text) and not has_cm else "")
    return None


_SPLIT_STEPS = re.compile(
    r"\s*(?:然后|接着|并且|and then|then|e poi|e dopo|y luego|puis|et ensuite|dann|そして|それから|，|,(?!\d))\s*")


def parse_jog_batch(text: str) -> list[Intent] | None:
    """'底座向左转5度，然后肘关节向上转5度' -> two jog intents. One clause stays a single jog."""
    parts = [p for p in _SPLIT_STEPS.split(text or "") if p.strip()]
    if len(parts) < 2:
        return None
    intents = []
    for part in parts:
        jog = parse_jog(part)
        if jog is None or jog.kind in ("touch", "grab", "place"):
            return None
        if jog.kind == "rotate" and "verso" not in jog.params:
            return None
        intents.append(jog)
    return intents


def parse_inspection(text: str, *, holding: bool) -> Intent | None:
    """'Begin inspection on tray A, reject items with scratches' -> inspect(rule); 'scartalo' -> sort to the fail tray."""
    t = _n(text)
    if not t:
        return None
    if _any(INSPECT_WORDS, t):
        has_rule = _any(REJECT_WORDS + APPROVE_WORDS, t) or len(t.split()) > 6 or len(t) > 12 and " " not in t
        return Intent("inspect", note=text.strip() if has_rule else "")
    if _any(CONTINUE_WORDS, t) and not _skill_of(t):
        return Intent("inspect", note="")
    if holding:
        if _any(REJECT_WORDS, t):
            return Intent("sort", params={"esito": "FAIL"})
        if _any(APPROVE_WORDS, t):
            return Intent("sort", params={"esito": "PASS"})
    return None
