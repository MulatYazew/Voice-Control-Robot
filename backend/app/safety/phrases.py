"""Deterministic phrase checks on the operator's own transcript.

Used independently of Grok for: STOP detection, confirmation validation,
cancellation. Conservative by design: anything uncertain is NOT a yes.
"""

from __future__ import annotations

import re
import unicodedata

STOP_TOKENS = {
    "stop", "stoppa", "fermati", "ferma", "fermo", "fermatevi", "fermare", "fermalo", "basta", "alt", "arresta",
    "arrestati", "arresto", "blocca", "bloccati", "bloccalo", "emergenza",
    "halt", "stopp", "arrete", "arretez", "detente",
}
STOP_PHRASES = ["arresta il robot", "blocca tutto", "fermati immediatamente", "fermo cosi"]

AFFIRM_TOKENS = {
    "si", "confermo", "conferma", "confermato", "confermata", "confermare", "confermi", "confermiamo", "procedi",
    "procedere", "prosegui", "vai", "ok", "okay", "certo", "esatto", "fallo", "fai", "d'accordo",
    "perfetto", "fallo", "avanti", "affermativo", "accordo", "corretto", "giusto", "certamente", "assolutamente",
    "yes", "yeah", "yep", "confirm", "confirmed", "proceed", "go", "sure", "oui", "ja",
}
AFFIRM_PHRASES = ["va bene", "puoi farlo", "puoi procedere", "fai pure", "vai pure", "dai pure", "d accordo"]
NEGATIVE_TOKENS = {"no", "non", "annulla", "annullare", "annullato", "annulliamo", "negativo", "cancella", "cancel",
                   "nope", "nein", "niente", "lascia", "dont", "not", "never", "mai"}
UNCERTAIN_TOKENS = {"forse", "magari", "credo", "penso", "boh", "dubbio", "aspetta", "aspettate", "attimo", "momento",
                    "sicuro", "sicura", "probabilmente", "mah", "vediamo", "dipende", "maybe", "perhaps", "wait",
                    "possibly", "quasi", "ehm"}
CANCEL_PHRASES = ["annulla", "lascia perdere", "non farlo", "cancella", "lascia stare", "non importa", "cancel"]

# verdicts
VALID = "valida"
NEGATIVE = "negativa"
UNCERTAIN = "incerta"
ABSENT = "assente"
STOP = "stop"


def normalize(text: str | None) -> str:
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("'", " ").replace("’", " ")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9? ]", " ", t)).strip()


def tokens(text: str | None) -> list[str]:
    return re.findall(r"[a-z0-9]+", normalize(text))


def _has_phrase(norm_text: str, phrases: list[str]) -> bool:
    padded = f" {norm_text.replace('?', ' ')} "
    return any(f" {p} " in padded for p in phrases)


def is_stop(text: str | None) -> bool:
    if not text:
        return False
    n = normalize(text)
    return bool(set(tokens(text)) & STOP_TOKENS) or _has_phrase(n, STOP_PHRASES)


def is_cancel(text: str | None) -> bool:
    n = normalize(text)
    return bool(n) and (_has_phrase(n, CANCEL_PHRASES) or tokens(text) == ["no"] or n.startswith("no "))


def confirmation_verdict(text: str | None) -> str:
    """valida | negativa | incerta | assente | stop — only 'valida' authorises motion."""
    if not text or not text.strip():
        return ABSENT
    if is_stop(text):
        return STOP
    n = normalize(text)
    toks = set(tokens(text))
    if toks & NEGATIVE_TOKENS:
        return NEGATIVE
    if toks & UNCERTAIN_TOKENS or "?" in n:
        return UNCERTAIN
    if toks & AFFIRM_TOKENS or _has_phrase(n, AFFIRM_PHRASES):
        return VALID
    return ABSENT
