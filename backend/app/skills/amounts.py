"""Read a distance / angle from the OPERATOR's own words ("3 cm", "三十度", "ten degrees").

The language model never supplies the number: the gate takes it from the transcript of the
operator's latest turn, so what moves is exactly what the operator said (and is then read back
in the confirmation question).
"""

from __future__ import annotations

import re
import unicodedata

CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "兩": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
             "八": 8, "九": 9}
WORDS = {
    # en
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "fortyfive": 45, "ninety": 90,
    "half": 0.5,
    # it
    "uno": 1, "un": 1, "una": 1, "due": 2, "tre": 3, "quattro": 4, "cinque": 5, "sei": 6, "sette": 7, "otto": 8,
    "nove": 9, "dieci": 10, "quindici": 15, "venti": 20, "trenta": 30, "quaranta": 40, "quarantacinque": 45,
    "novanta": 90, "mezzo": 0.5,
    # es
    "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
    "quince": 15, "veinte": 20, "treinta": 30, "cuarenta": 40, "noventa": 90, "medio": 0.5,
    # fr
    "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "quinze": 15,
    "vingt": 20, "trente": 30, "quarante": 40, "demi": 0.5,
    # de
    "eins": 1, "ein": 1, "einen": 1, "zwei": 2, "drei": 3, "vier": 4, "funf": 5, "sechs": 6, "sieben": 7, "acht": 8,
    "neun": 9, "zehn": 10, "funfzehn": 15, "zwanzig": 20, "dreissig": 30, "vierzig": 40, "neunzig": 90, "halb": 0.5,
}
JA_DIGITS = {"いち": 1, "に": 2, "さん": 3, "よん": 4, "ご": 5, "ろく": 6, "なな": 7, "はち": 8, "きゅう": 9, "じゅう": 10}

MM = r"(mm|毫米|ミリ|millimet\w*|milimet\w*)"
CM = r"(cm|厘米|公分|センチ|centimet\w*|zentimet\w*)"
M = r"(m|米|メートル|metr[oie]s?|meters?|metres?)"
DEG = r"(°|度|deg\w*|grad\w*|degr\w*)"
REPEAT = [r"\bancora\b", r"\bagain\b", r"\bsame\b", r"\bdi nuovo\b", r"\botra vez\b", r"\bencore\b", r"\bnochmal\b",
          "再", "又", "同样", "もう一度", "もう少し"]


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").lower()
    t = "".join(c for c in unicodedata.normalize("NFKD", t) if not unicodedata.combining(c))
    return t.replace(",", ".")


def cn_number(s: str) -> float | None:
    """一 .. 九十九, 两, 半, 一点五, 三十五."""
    if not s:
        return None
    if s == "半":
        return 0.5
    if "点" in s:
        a, _, b = s.partition("点")
        ia = cn_number(a)
        if ia is None or not b or any(c not in CN_DIGITS for c in b):
            return None
        return ia + float("0." + "".join(str(CN_DIGITS[c]) for c in b))
    if "十" in s:
        a, _, b = s.partition("十")
        tens = CN_DIGITS.get(a, None) if a else 1
        ones = CN_DIGITS.get(b, None) if b else 0
        if tens is None or ones is None:
            return None
        return float(tens * 10 + ones)
    if len(s) == 1 and s in CN_DIGITS:
        return float(CN_DIGITS[s])
    return None


_NUM = r"(\d+(?:\.\d+)?|[零〇一壹二两兩三四五六七八九十点半]+|" + "|".join(
    sorted((re.escape(w) for w in list(WORDS) + list(JA_DIGITS)), key=len, reverse=True)) + r")"


def _value(tok: str) -> float | None:
    if re.fullmatch(r"\d+(?:\.\d+)?", tok):
        return float(tok)
    if tok in WORDS:
        return float(WORDS[tok])
    if tok in JA_DIGITS:
        return float(JA_DIGITS[tok])
    return cn_number(tok)


def length_cm(text: str) -> float | None:
    """Centimetres only when a length unit is written: 公分 and 厘米 are both centimetres. Degrees are not."""
    t = _norm(text)
    for unit, scale in ((MM, 0.1), (CM, 1.0), (M, 100.0)):
        m = re.search(r"(?<![a-z])" + _NUM + r"\s*(个)?\s*" + unit + r"(?![a-z])", t)
        if m:
            v = _value(m.group(1))
            if v is not None:
                return v * scale
    return None


def distance_cm(text: str) -> float | None:
    """'sposta a sinistra di 3 cm' -> 3.0; '往左五毫米' -> 0.5. A bare number counts as centimetres."""
    found = length_cm(text)
    if found is not None:
        return found
    if re.search(DEG, _norm(text)):
        return None
    return _bare(_norm(text))


def angle_deg(text: str) -> float | None:
    """'ruota la base di 30 gradi' -> 30; '底座左转四十五度' -> 45. A bare number counts as degrees.

    A length ('十公分', '十厘米', '10 cm') is never an angle, even when a joint is named.
    """
    t = _norm(text)
    m = re.search(r"(?<![a-z])" + _NUM + r"\s*" + DEG, t)
    if m:
        v = _value(m.group(1))
        if v is not None:
            return v
    if length_cm(text) is not None:
        return None
    return _bare(t)


ARTICLES = {"un", "una", "uno", "ein", "einen", "one"}   # "un po'", "ein bisschen": not an amount


def _bare(t: str) -> float | None:
    t = re.sub(r"一(下|点儿|点点|些|会儿)", " ", t)          # "a little" in Chinese, not "1"
    found = [v for v in (_value(m.group(1)) for m in re.finditer(r"(?<![a-z])" + _NUM + r"(?![a-z])", t)
                         if m.group(1) not in ARTICLES and m.group(1) not in JA_DIGITS) if v]
    if len(found) == 1:
        return found[0]
    return None


def amounts_in_order(text: str) -> list[tuple[str, float]]:
    """Every length and angle in the sentence, left to right: ('cm', 10) or ('deg', 5).

    Used when one utterance contains several moves. A bare number is not an amount here.
    """
    t = _norm(text)
    found: list[tuple[int, str, float]] = []
    for unit, scale in ((MM, 0.1), (CM, 1.0), (M, 100.0)):
        for m in re.finditer(r"(?<![a-z])" + _NUM + r"\s*(个)?\s*" + unit + r"(?![a-z])", t):
            v = _value(m.group(1))
            if v is not None:
                found.append((m.start(), "cm", v * scale))
    for m in re.finditer(r"(?<![a-z])" + _NUM + r"\s*" + DEG, t):
        v = _value(m.group(1))
        if v is not None:
            found.append((m.start(), "deg", v))
    found.sort(key=lambda item: item[0])
    return [(kind, value) for _, kind, value in found]


def is_nudge(text: str) -> bool:
    """'往上一点' names a direction and no amount. The gate supplies one fixed default."""
    if angle_deg(text) is not None or length_cm(text) is not None:
        return False
    return bool(re.search(r"一点|一点儿|稍微|a bit|a little|un po", _norm(text)))


def is_repeat(text: str) -> bool:
    t = _norm(text)
    return any(re.search(p, t) for p in REPEAT)
