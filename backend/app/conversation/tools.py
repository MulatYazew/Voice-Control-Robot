"""The ONLY tools Grok receives, and their dispatch to the safety gate.

No tool can send URScript, joints, raw poses, open connections, change robot
configuration or bypass the gate. Every argument is validated by the gate /
registry; unknown tools are rejected.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import Settings
from ..safety.gate import Gate

PROMPT_FILE = Path(__file__).resolve().parents[1] / "voice" / "prompt_it.md"

SKILL_IDS = ["vai_a_posa", "indica", "solleva_vassoio", "appoggia_vassoio", "spingi"]


def _params_schema(s: Settings, poses: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "description": "Includi SOLO i parametri detti dall'operatore. Ometti quelli mancanti o incerti.",
        "properties": {
            "posizione": {"type": "string", "enum": s.zones},
            "direzione": {"type": "string", "enum": s.directions},
            "posa": {"type": "string", "enum": poses},
        },
    }


def tool_schemas(s: Settings, poses: list[str], *, stretch: bool = True) -> list[dict[str, Any]]:
    p = _params_schema(s, poses)
    tools = [
        {"type": "function", "name": "propose_skill",
         "description": "Proponi UNA abilità predefinita. Non muove il robot: crea un'azione in attesa di conferma.",
         "parameters": {"type": "object", "properties": {"skill": {"type": "string", "enum": SKILL_IDS}, "parametri": p},
                        "required": ["skill"]}},
        {"type": "function", "name": "confirm",
         "description": "Chiedi l'esecuzione dell'azione in attesa. SOLO dopo che l'operatore ha detto sì nel suo ultimo turno. "
                        "Il sistema verifica la trascrizione dell'operatore.",
         "parameters": {"type": "object", "properties": {"action_id": {"type": "string"}}, "required": ["action_id"]}},
        {"type": "function", "name": "cancel", "description": "Annulla l'azione in attesa.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "stop", "description": "Ferma subito il robot. Usala appena l'operatore dice stop.",
         "parameters": {"type": "object", "properties": {}}},
        {"type": "function", "name": "status", "description": "Stato del sistema, del robot e dei vassoi presunti.",
         "parameters": {"type": "object", "properties": {}}},
    ]
    if stretch:
        tools += [
            {"type": "function", "name": "propose_sequence",
             "description": "Proponi da 2 a 5 abilità in sequenza. Ogni movimento richiede la sua conferma.",
             "parameters": {"type": "object", "properties": {"passi": {"type": "array", "minItems": 2, "maxItems": 5,
                            "items": {"type": "object", "properties": {"skill": {"type": "string", "enum": SKILL_IDS},
                                                                       "parametri": p}, "required": ["skill"]}}},
                            "required": ["passi"]}},
            {"type": "function", "name": "save_pose",
             "description": "Salva la posizione attuale del robot con un nome, per vai_a_posa. Non muove il robot.",
             "parameters": {"type": "object", "properties": {"nome": {"type": "string"}}, "required": ["nome"]}},
        ]
    return tools


def build_prompt(s: Settings, gate: Gate) -> str:
    st = gate.world.snapshot()["oggetti_presunti"]
    state = json.dumps({"vassoi_presunti_in": st["vassoi_in_zona"], "zone_libere": st["zone_libere"],
                        "vassoio_sulla_forca": st["vassoio_sulla_forca"], "stato": gate.state.value}, ensure_ascii=False)
    wake = ""
    if s.wake_word:
        wake = (f"10. Parola di attivazione: proponi azioni solo se la frase contiene \"{s.wake_word}\". "
                "Stop e risposte sì/no valgono sempre anche senza.")
    return PROMPT_FILE.read_text().format(poses=", ".join(gate.reg.selectable_poses()), zones=", ".join(s.zones),
                                          directions=", ".join(s.directions), state=state, wake=wake)


async def dispatch(gate: Gate, name: str, args: dict[str, Any], *, source: str = "grok") -> dict[str, Any]:
    """Execute a tool call against the gate. Never raises."""
    if not isinstance(args, dict):
        return {"ok": False, "esito": "argomenti_non_validi"}
    try:
        if name == "propose_skill":
            return gate.propose(args.get("skill"), args.get("parametri") or args.get("params") or {}, source=source)
        if name == "confirm":
            return await gate.confirm(str(args.get("action_id") or ""), source=source)
        if name == "cancel":
            return gate.cancel(source=source)
        if name == "stop":
            return await gate.stop(f"grok_tool:{source}")
        if name == "status":
            st = gate.status()
            return {"ok": True, "gate": st["gate"], "pending": st["pending"], "world": st["world"],
                    "robot": {k: st["robot"][k] for k in ("connected", "moving", "safety", "robot_mode")}}
        if name == "propose_sequence":
            steps = [{"skill": p.get("skill"), "params": p.get("parametri") or {}} for p in (args.get("passi") or [])
                     if isinstance(p, dict)]
            return gate.propose_sequence(steps, source=source)
        if name == "save_pose":
            return await gate.save_pose(str(args.get("nome") or ""))
    except Exception as e:  # pragma: no cover - defensive
        return {"ok": False, "esito": "errore_interno", "messaggio": str(e)}
    return {"ok": False, "esito": "strumento_sconosciuto", "messaggio": f"Strumento {name!r} non disponibile."}
