"""Deterministic skill registry loaded from config/skills.yaml + config/poses.yaml.

Grok (or the text-mode rules) can only *name* a skill and parameter values.
This module decides whether that is valid, and turns it into a fixed motion
sequence over recorded joint targets. It never accepts poses or joints from
the conversation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..i18n import skill_sentence
from ..safety.phrases import direction_aliases, normalize, zone_aliases
from .world import WorldModel

# result codes (Italian, as required by the spec)
SKILL_SCONOSCIUTA = "skill_sconosciuta"
RICHIESTA_AMBIGUA = "richiesta_ambigua"
PRECONDIZIONE = "precondizione_non_soddisfatta"
POSA_NON_VERIFICATA = "posa_non_verificata"


class SkillError(Exception):
    def __init__(self, code: str, message: str, *, param: str | None = None, options: list[str] | None = None):
        super().__init__(message)
        self.code, self.param, self.options = code, param, options or []

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"ok": False, "esito": self.code, "messaggio": str(self)}
        if self.param:
            d["parametro"] = self.param
        if self.options:
            d["opzioni_valide"] = self.options
        return d


def norm(s: Any) -> str:
    """Unicode-safe normaliser. Han, Kana and Ethiopic are kept; '_' becomes a space."""
    return normalize(s)


ZONE_ALIASES = zone_aliases()
DIRECTION_ALIASES = direction_aliases()
INSPECTION_PRECONDITIONS = {"zona_di_ingresso", "zona_di_uscita"}
TRAY_WORDS = ["vassoio", "tray", "bandeja", "plateau", "schale", "托盘", "盘", "トレイ"]


def tray_aliases(label: str) -> list[str]:
    lab = norm(label)
    return [lab] + [f"{w} {lab}" for w in TRAY_WORDS] + [f"{w}{lab}" for w in TRAY_WORDS] + [f"{lab}{w}" for w in TRAY_WORDS]


@dataclass(frozen=True)
class MotionStep:
    move: str                       # movej | movel | wait
    pose: str | None = None
    joints: tuple[float, ...] | None = None
    speed: float = 0.0
    accel: float = 0.0
    seconds: float = 0.0
    blend: float = 0.0

    def public(self) -> dict[str, Any]:
        return {"move": self.move, "posa": self.pose, "secondi": self.seconds or None}


@dataclass
class Skill:
    id: str
    descrizione: str
    strumento: str | None
    parametri: dict[str, str]           # name -> tipo
    precondizioni: list[str]
    sequenza: list[dict[str, Any]]
    timeout_s: float
    conferma: str
    risultato: str
    in_caso_di_errore: str = "fault"
    conferma_en: str = ""
    risultato_en: str = ""


@dataclass
class ValidatedAction:
    skill: str
    params: dict[str, str]
    motion: list[MotionStep]
    timeout_s: float
    conferma: str
    risultato: str
    strumento: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    conferma_en: str = ""
    risultato_en: str = ""

    def public(self) -> dict[str, Any]:
        return {"skill": self.skill, "parametri": self.params, "strumento": self.strumento,
                "conferma": self.conferma, "conferma_en": self.conferma_en, "sequenza": [m.public() for m in self.motion], "timeout_s": self.timeout_s}


_EN_VALUES = {"sinistra": "left", "centro": "centre", "destra": "right", "scarto": "the reject box", "attesa": "waiting",
              "sinistra sopra": "above left", "centro sopra": "above centre", "destra sopra": "above right"}


def _en(fmt: dict[str, str]) -> dict[str, str]:
    return {k: _EN_VALUES.get(v, v) for k, v in fmt.items()}


class Registry:
    def __init__(self, settings: Settings) -> None:
        self.s = settings
        self.skills: dict[str, Skill] = {}
        for sid, raw in settings.skills.items():
            self.skills[sid] = Skill(
                id=sid, descrizione=raw["descrizione"], strumento=raw.get("strumento"),
                parametri={k: v["tipo"] for k, v in (raw.get("parametri") or {}).items()},
                precondizioni=list(raw.get("precondizioni") or []), sequenza=list(raw["sequenza"]),
                timeout_s=float(raw.get("timeout_s", 30)), conferma=raw["conferma"], risultato=raw["risultato"],
                in_caso_di_errore=raw.get("in_caso_di_errore", "fault"),
                conferma_en=raw.get("conferma_en", raw["conferma"]), risultato_en=raw.get("risultato_en", raw["risultato"]))

    # ------------------------------------------------------------ domains ---
    def selectable_poses(self) -> list[str]:
        return [k for k, v in self.s.poses.items() if v.get("selezionabile")]

    def domain(self, tipo: str) -> list[str]:
        if tipo == "zona":
            return self.s.zones
        if tipo == "direzione":
            return self.s.directions
        if tipo == "posa":
            return self.selectable_poses()
        raise ValueError(tipo)

    def normalize(self, tipo: str, value: Any) -> str | None:
        """Map a spoken/typed value onto a canonical configured value, or None."""
        if value is None:
            return None
        n = norm(value).replace("_", " ")
        if not n:
            return None
        if tipo in ("zona", "direzione"):
            dom = self.domain(tipo)
            table = ZONE_ALIASES if tipo == "zona" else DIRECTION_ALIASES
            for canon, aliases in table.items():
                if canon in dom and (n == canon or n in aliases):
                    return canon
            if tipo == "zona":
                for z in dom:
                    aliases = tray_aliases(self.s.zone_label(z))
                    if n in aliases or n.replace(" ", "") in aliases:
                        return z
            return n if n in dom else None
        if tipo == "posa":
            for k in self.selectable_poses():
                if n == norm(k).replace("_", " ") or n in [norm(a) for a in self.s.poses[k].get("alias", [])]:
                    return k
            return None
        return None

    def catalogue(self) -> dict[str, Any]:
        return {sid: {"descrizione": sk.descrizione, "strumento": sk.strumento,
                      "parametri": {p: self.domain(t) for p, t in sk.parametri.items()}}
                for sid, sk in self.skills.items()}

    # --------------------------------------------------------- validation ---
    def validate(self, skill_id: Any, params: dict[str, Any] | None, world: WorldModel) -> ValidatedAction:
        sid = norm(skill_id).replace(" ", "_")
        if sid not in self.skills:
            raise SkillError(SKILL_SCONOSCIUTA, f"Abilità '{skill_id}' non disponibile.", options=list(self.skills))
        sk = self.skills[sid]
        params = dict(params or {})
        unknown = [k for k in params if k not in sk.parametri]
        for k in unknown:  # ignore empty extras the model sometimes adds, reject real ones
            if params[k] not in (None, ""):
                raise SkillError(RICHIESTA_AMBIGUA, f"Parametro '{k}' non previsto per {sid}.", param=k,
                                 options=list(sk.parametri))
        resolved: dict[str, str] = {}
        for pname, tipo in sk.parametri.items():
            raw = params.get(pname)
            options = self._options(sid, pname, tipo, world)
            if raw in (None, ""):
                raise SkillError(RICHIESTA_AMBIGUA, f"Manca il parametro '{pname}' per {sid}.", param=pname, options=options)
            val = self.normalize(tipo, raw)
            if val is None:
                raise SkillError(RICHIESTA_AMBIGUA, f"Valore '{raw}' non valido per '{pname}'.", param=pname, options=options)
            resolved[pname] = val
        self._check_preconditions(sk, resolved, world)
        motion = self.motion(sk, resolved)
        fmt = {k: v.replace("_", " ") for k, v in resolved.items()}
        conferma = sk.conferma.format(**fmt)
        conferma_en = sk.conferma_en.format(**_en(fmt))
        risultato = sk.risultato.format(**fmt)
        risultato_en = sk.risultato_en.format(**_en(fmt))
        conferme = {"it": conferma, "en": conferma_en}
        risultati = {"it": risultato, "en": risultato_en}
        for lang in ("zh", "ja", "am"):
            c_lang = skill_sentence(sid, "conferma", lang, fmt)
            r_lang = skill_sentence(sid, "risultato", lang, fmt)
            if c_lang:
                conferme[lang] = c_lang
            if r_lang:
                risultati[lang] = r_lang
        return ValidatedAction(skill=sid, params=resolved, motion=motion, timeout_s=sk.timeout_s,
                               conferma=conferma, risultato=risultato, strumento=sk.strumento,
                               conferma_en=conferma_en, risultato_en=risultato_en,
                               extra={"conferme": conferme, "risultati": risultati})

    def _io_zones(self) -> tuple[str | None, list[str]]:
        insp = self.s.inspection
        return insp.get("input_zone"), [z for z in (insp.get("pass_zone"), insp.get("fail_zone")) if z]

    def _options(self, sid: str, pname: str, tipo: str, world: WorldModel) -> list[str]:
        dom = self.domain(tipo)
        sk = self.skills[sid]
        if pname == "posizione":
            inp, outs = self._io_zones()
            if "zona_di_ingresso" in sk.precondizioni and inp:
                dom = [z for z in dom if z == inp]
            if "zona_di_uscita" in sk.precondizioni:
                dom = [z for z in dom if z in outs]
        if pname in ("posizione", "origine") and "vassoio_presunto_in_zona" in sk.precondizioni:
            return [z for z in dom if z in world.trays_at]
        if pname in ("posizione", "destinazione") and "zona_libera" in sk.precondizioni:
            return [z for z in dom if z not in world.trays_at]
        return dom

    def _check_preconditions(self, sk: Skill, p: dict[str, str], world: WorldModel) -> None:
        inp, outs = self._io_zones()
        for pc in sk.precondizioni:
            if pc == "zona_di_ingresso" and p.get("posizione") != inp:
                raise SkillError(PRECONDIZIONE, f"Il vassoio di ingresso è {self.s.zone_label(inp or '')} ({inp}).",
                                 param="posizione", options=[inp] if inp else [])
            if pc == "zona_di_uscita" and p.get("posizione") not in outs:
                raise SkillError(PRECONDIZIONE, "Il pezzo va depositato nel vassoio approvati o scartati: "
                                 + ", ".join(f"{self.s.zone_label(z)} ({z})" for z in outs) + ".",
                                 param="posizione", options=outs)
            if pc == "forca_libera" and world.holding_tray:
                raise SkillError(PRECONDIZIONE, "C'è già un vassoio sulla forca: va prima appoggiato.",
                                 options=[z for z in self.s.zones if z not in world.trays_at])
            if pc == "vassoio_sulla_forca" and not world.holding_tray:
                raise SkillError(PRECONDIZIONE, "Non c'è nessun vassoio sulla forca.")
            if pc == "zone_diverse" and p.get("origine") and p.get("origine") == p.get("destinazione"):
                raise SkillError(RICHIESTA_AMBIGUA, "Origine e destinazione devono essere zone diverse.",
                                 param="destinazione", options=[z for z in self.s.zones if z != p["origine"]])
            zone_from = p.get("origine") or p.get("posizione")
            zone_to = p.get("destinazione") or p.get("posizione")
            if pc == "vassoio_presunto_in_zona" and zone_from not in world.trays_at:
                raise SkillError(PRECONDIZIONE, f"Nella zona {zone_from} non è previsto nessun vassoio.",
                                 param="origine" if "origine" in p else "posizione", options=sorted(world.trays_at))
            if pc == "zona_libera" and zone_to in world.trays_at:
                raise SkillError(PRECONDIZIONE, f"La zona {zone_to} è già occupata.",
                                 param="destinazione" if "destinazione" in p else "posizione",
                                 options=[z for z in self.s.zones if z not in world.trays_at])

    def motion(self, sk: Skill, p: dict[str, str]) -> list[MotionStep]:
        m = self.s.motion
        out: list[MotionStep] = []
        for st in sk.sequenza:
            if st["move"] == "wait":
                out.append(MotionStep("wait", seconds=float(st.get("secondi", 1.0))))
                continue
            name = st["posa"].format(**p)
            pose = self.s.poses[name]  # existence checked at config load
            if self.s.mode == "real" and not pose.get("verificata_reale"):
                raise SkillError(POSA_NON_VERIFICATA, f"La posa {name} non è stata registrata sul robot reale.")
            lin = st["move"] == "movel"
            out.append(MotionStep(st["move"], name, tuple(float(x) for x in pose["joints"]),
                                  m["linear_speed"] if lin else m["joint_speed"],
                                  m["linear_accel"] if lin else m["joint_accel"]))
        return out
