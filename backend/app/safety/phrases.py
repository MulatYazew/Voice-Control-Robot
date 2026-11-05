"""Deterministic phrase checks on the operator's own transcript.

Used independently of Grok for: STOP detection, confirmation validation,
cancellation. Conservative by design: anything uncertain is NOT a yes.

Verdict order is frozen: STOP → NEGATIVE → UNCERTAIN → AFFIRM → ABSENT.
Negative is checked before affirm so that 不好 / 不可以 / 不对 / いいえ
do not authorise motion.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ..config import ROOT

LEXICON_PATH = ROOT / "config" / "lexicon.yaml"
LANGS = ("it", "en", "zh", "ja", "am")
LIST_KEYS = ("stop", "affirm", "negative", "uncertain", "cancel", "terminate")

# verdicts
VALID = "valida"
NEGATIVE = "negativa"
UNCERTAIN = "incerta"
ABSENT = "assente"
STOP = "stop"

_ETH_SPACE = set("።፣፤")
_TOKEN_RE = re.compile(r"[a-z0-9]+|[\u1200-\u135F]+")


class LexiconError(RuntimeError):
    pass


def _is_latin_base(ch: str) -> bool:
    return len(ch) == 1 and (("a" <= ch <= "z") or ("A" <= ch <= "Z"))


def _in_letter_block(ch: str) -> bool:
    o = ord(ch)
    if 0x4E00 <= o <= 0x9FFF:          # Han
        return True
    if 0x3040 <= o <= 0x309F:          # Hiragana
        return True
    if 0x30A0 <= o <= 0x30FF:          # Katakana, including ー
        return True
    if 0x1200 <= o <= 0x135F:          # Ethiopic letters (punctuation U+1360+ excluded)
        return True
    return False


def _is_cjk(s: str) -> bool:
    for ch in s:
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF:
            return True
    return False


def normalize(text: str | None) -> str:
    """NFKC + lowercase. Combining marks are stripped only from Latin letters.

    Japanese is not NFKD-stripped: that would split dakuten (ど → と + ゙) and
    break words such as どうぞ. Ethiopic ። ፣ ፤ become spaces. ? is kept.
    """
    if not text:
        return ""
    t = unicodedata.normalize("NFKC", str(text)).lower()
    t = t.replace("'", " ").replace("’", " ").replace("ʼ", " ")
    out: list[str] = []
    for ch in t:
        if unicodedata.combining(ch):
            if out and _is_latin_base(out[-1]):
                continue
            if out and not _is_latin_base(out[-1]):
                out.append(ch)
            continue
        if ch in _ETH_SPACE or ch == "፧":
            out.append(" ")
            continue
        folded = _fold_latin(ch)
        if folded is not None:
            out.append(folded)
            continue
        if _in_letter_block(ch) or ch.isdigit() or ch == "?":
            out.append(ch)
            continue
        out.append(" ")
    return re.sub(r"\s+", " ", "".join(out)).strip()


def _fold_latin(ch: str) -> str | None:
    if not ch.isalpha():
        return None
    decomp = unicodedata.normalize("NFKD", ch)
    if not decomp or not _is_latin_base(decomp[0]):
        return None
    return "".join(c for c in decomp if _is_latin_base(c))


def tokens(text: str | None) -> list[str]:
    return _TOKEN_RE.findall(normalize(text))


def _for_uncertainty(text: str | None, normalized: str) -> str:
    """Map sentence-final 吗 / か and Ethiopic ፧ onto ? for the uncertainty check only."""
    if not text:
        return normalized
    raw = unicodedata.normalize("NFKC", str(text)).lower().strip()
    core = raw.rstrip(" 。.!！ \t")
    mark = ("፧" in str(text)) or core.endswith("吗") or core.endswith("か") or core.endswith("？")
    if mark and "?" not in normalized:
        return (normalized + " ?").strip()
    return normalized


def _has_phrase(norm_text: str, phrases: list[str]) -> bool:
    padded = f" {norm_text.replace('?', ' ')} "
    return any(f" {p} " in padded for p in phrases)


def _substring_hit(norm_text: str, entries: list[str]) -> bool:
    return any(e in norm_text for e in entries)


@lru_cache(maxsize=1)
def load_lexicon(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else LEXICON_PATH
    if not p.exists():
        raise LexiconError(f"missing {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    validate_lexicon(data)
    return data


def validate_lexicon(data: dict[str, Any]) -> None:
    if not isinstance(data, dict):
        raise LexiconError("lexicon root must be a mapping")
    reviewed = data.get("reviewed_by")
    if not isinstance(reviewed, dict):
        raise LexiconError("lexicon.reviewed_by is required")
    for lang in LANGS:
        if lang not in reviewed:
            raise LexiconError(f"lexicon.reviewed_by.{lang} missing")
    for key in LIST_KEYS:
        block = data.get(key)
        if not isinstance(block, dict):
            raise LexiconError(f"lexicon.{key} must be a mapping of language → list")
        for lang in LANGS:
            words = block.get(lang)
            if not isinstance(words, list) or not words:
                raise LexiconError(f"lexicon.{key}.{lang} must be a non-empty list")
            if any(not isinstance(w, str) or not w.strip() for w in words):
                raise LexiconError(f"lexicon.{key}.{lang} has an empty entry")
    for table in ("zones", "directions"):
        block = data.get(table)
        if not isinstance(block, dict) or not block:
            raise LexiconError(f"lexicon.{table} must be a mapping")
        for name, per_lang in block.items():
            if not isinstance(per_lang, dict):
                raise LexiconError(f"lexicon.{table}.{name} must map languages to word lists")
            for lang in LANGS:
                words = per_lang.get(lang)
                if not isinstance(words, list) or not words:
                    raise LexiconError(f"lexicon.{table}.{name}.{lang} must be a non-empty list")


def reload_lexicon() -> dict[str, Any]:
    load_lexicon.cache_clear()
    _compiled.cache_clear()
    return load_lexicon()


def lexicon_review_status() -> dict[str, Any]:
    data = load_lexicon()
    reviewed = {k: data["reviewed_by"].get(k) for k in LANGS}
    unverified = [k for k, v in reviewed.items() if not v]
    return {"reviewed_by": reviewed, "unverified_languages": unverified}


class _Compiled:
    def __init__(self) -> None:
        data = load_lexicon()
        self.stop_tokens: set[str] = set()
        self.stop_phrases: list[str] = []
        self.stop_cjk: list[str] = []
        self.affirm_tokens: set[str] = set()
        self.affirm_phrases: list[str] = []
        self.affirm_cjk: list[str] = []
        self.negative_tokens: set[str] = set()
        self.negative_cjk: list[str] = []
        self.uncertain_tokens: set[str] = set()
        self.uncertain_cjk: list[str] = []
        self.negative_phrases: list[str] = []
        self.uncertain_phrases: list[str] = []
        self.cancel_phrases: list[str] = []
        self.cancel_cjk: list[str] = []
        self.terminate_phrases: list[str] = []
        self.terminate_cjk: list[str] = []
        for lang in LANGS:
            self._absorb(data["stop"][lang], self.stop_tokens, self.stop_phrases, self.stop_cjk)
            self._absorb(data["affirm"][lang], self.affirm_tokens, self.affirm_phrases, self.affirm_cjk)
            self._absorb(data["negative"][lang], self.negative_tokens, self.negative_phrases, self.negative_cjk)
            self._absorb(data["uncertain"][lang], self.uncertain_tokens, self.uncertain_phrases, self.uncertain_cjk)
            # Single-word cancels ("annulla", "cancel") match as phrases, same as before.
            self._absorb(data["cancel"][lang], set(), self.cancel_phrases, self.cancel_cjk, singles_as_phrases=True)
            self._absorb(data["terminate"][lang], set(), self.terminate_phrases, self.terminate_cjk, singles_as_phrases=True)
        # Longer CJK entries first so a caller that inspects the first hit is stable.
        for bucket in (self.stop_cjk, self.affirm_cjk, self.negative_cjk, self.uncertain_cjk, self.cancel_cjk,
                       self.terminate_cjk):
            bucket.sort(key=len, reverse=True)

    @staticmethod
    def _absorb(words: list[str], tokens: set[str], phrases: list[str], cjk: list[str], *,
                singles_as_phrases: bool = False) -> None:
        for raw in words:
            n = normalize(raw)
            if not n:
                continue
            if _is_cjk(n):
                if n not in cjk:
                    cjk.append(n)
            elif " " in n or singles_as_phrases:
                if n not in phrases:
                    phrases.append(n)
            else:
                tokens.add(n)


@lru_cache(maxsize=1)
def _compiled() -> _Compiled:
    return _Compiled()


def _alias_table(section: str) -> dict[str, list[str]]:
    data = load_lexicon()
    out: dict[str, list[str]] = {}
    for name, per_lang in data[section].items():
        aliases: list[str] = []
        for words in per_lang.values():
            for raw in words:
                n = normalize(raw)
                if n and n not in aliases:
                    aliases.append(n)
        out[name] = aliases
    return out


def zone_aliases() -> dict[str, list[str]]:
    return _alias_table("zones")


def direction_aliases() -> dict[str, list[str]]:
    return _alias_table("directions")


def skill_triggers() -> dict[str, list[str]]:
    data = load_lexicon()
    block = data.get("skill_triggers") or {}
    out: dict[str, list[str]] = {}
    for sid, per_lang in block.items():
        words: list[str] = []
        for raws in per_lang.values():
            for raw in raws:
                n = normalize(raw)
                if n and n not in words:
                    words.append(n)
        words.sort(key=len, reverse=True)
        out[sid] = words
    return out


def is_terminate(text: str | None) -> bool:
    """Close the assistant. Every phrase names the assistant or the session, so bare 'stop' does not match."""
    n = normalize(text)
    if not n:
        return False
    c = _compiled()
    if _has_phrase(n, c.terminate_phrases):
        return True
    return _substring_hit(n, c.terminate_cjk)


def is_stop(text: str | None) -> bool:
    if not text:
        return False
    n = normalize(text)
    if not n:
        return False
    c = _compiled()
    if set(tokens(text)) & c.stop_tokens:
        return True
    if _has_phrase(n, c.stop_phrases):
        return True
    return _substring_hit(n, c.stop_cjk)


def is_cancel(text: str | None) -> bool:
    n = normalize(text)
    if not n:
        return False
    c = _compiled()
    if _has_phrase(n, c.cancel_phrases) or tokens(text) == ["no"] or n.startswith("no "):
        return True
    return _substring_hit(n, c.cancel_cjk)


def confirmation_verdict(text: str | None) -> str:
    """valida | negativa | incerta | assente | stop — only 'valida' authorises motion."""
    if not text or not str(text).strip():
        return ABSENT
    if is_stop(text):
        return STOP
    base = normalize(text)
    n = _for_uncertainty(text, base)
    c = _compiled()
    toks = set(tokens(text))
    if toks & c.negative_tokens or _has_phrase(base, c.negative_phrases) or _substring_hit(base, c.negative_cjk):
        return NEGATIVE
    if (toks & c.uncertain_tokens or _has_phrase(base, c.uncertain_phrases) or "?" in n
            or _substring_hit(base, c.uncertain_cjk)):
        return UNCERTAIN
    if toks & c.affirm_tokens or _has_phrase(base, c.affirm_phrases) or _substring_hit(base, c.affirm_cjk):
        return VALID
    return ABSENT
