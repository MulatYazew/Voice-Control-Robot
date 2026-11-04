"""TEXT MODE — NOT VOICE.

A conversation driven by typed text and the deterministic rules parser, so the
whole pipeline (validation, clarification, confirmation, execution, STOP) can
be exercised when Grok is unavailable. Development and evaluation only.
"""

from __future__ import annotations

from typing import Any

from ..safety.gate import Gate, GateState
from .rules import Intent, parse

TEXT_MODE_LABEL = "TEXT MODE — NOT VOICE"


class TextConversation:
    def __init__(self, gate: Gate) -> None:
        self.gate = gate
        self.context: dict[str, Any] | None = None   # last incomplete intent (for clarifications/corrections)

    async def handle(self, text: str) -> dict[str, Any]:
        g = self.gate
        g.note_turn_committed("text")
        stopped = g.note_transcript(text, source="text")
        if stopped:
            self.context = None
            return self._out(text, Intent("stop"), "Fermato. Nuovi movimenti bloccati fino a RIPRISTINA.", None)

        intent = parse(text, poses=g.s.poses, pending=g.pending is not None)

        if intent.kind == "confirm":
            if intent.verdict != "valida":
                return self._out(text, intent, "Serve una conferma esplicita: sì o no?", None)
            res = await g.confirm(g.pending.id if g.pending else None, source="rules")
            reply = "Procedo." if res.get("ok") else res.get("messaggio", "Conferma non valida.")
            return self._out(text, intent, reply, res)

        if intent.kind == "cancel":
            res = g.cancel(source="rules")
            self.context = None
            return self._out(text, intent, "Annullato. Il robot non si muove." if res.get("annullata") else
                             "Non c'è nulla da annullare.", res)

        if intent.kind == "out_of_scope":
            self.context = None
            return self._out(text, intent, "Non posso farlo. So solo: andare in una posa, indicare una zona, sollevare, "
                                           "appoggiare o spingere un vassoio.", None)

        if intent.kind == "clarify":
            if intent.params and self.context:     # answer to the previous question: merge
                skill = self.context["skill"]
                params = {**self.context["params"], **self._map_param(skill, intent.params)}
                return await self._propose(text, Intent("skill", skill=skill, params=params))
            self.context = None
            return self._out(text, intent, "Non ho capito cosa spostare e dove. Vuoi sollevare, appoggiare o spingere un "
                                           "vassoio, oppure indicare una zona? Zone: sinistra, centro, destra.", None)

        if intent.kind == "skill":
            if g.pending:  # a new command while one is pending = correction: replace it
                g.cancel(source="rules:correzione")
            return await self._propose(text, intent)

        return self._out(text, intent, "Non ho capito. Puoi ripetere?", None)

    def _map_param(self, skill: str, p: dict[str, str]) -> dict[str, str]:
        z = p.get("posizione")
        if skill == "vai_a_posa" and z:
            return {"posa": f"{z}_sopra"}
        if skill == "spingi" and z and "posizione" in (self.context or {}).get("params", {}):
            return {"direzione": z}
        return p

    async def _propose(self, text: str, intent: Intent) -> dict[str, Any]:
        g = self.gate
        res = g.propose(intent.skill, intent.params, source="rules")
        if res.get("ok"):
            self.context = None
            g.mark_question_asked(res["action_id"])
            return self._out(text, intent, res["frase_conferma"], res)
        if res.get("esito") in ("richiesta_ambigua", "precondizione_non_soddisfatta"):
            self.context = {"skill": intent.skill, "params": intent.params}
            opts = res.get("opzioni_valide") or []
            q = res["messaggio"] + (f" Opzioni: {', '.join(opts)}." if opts else "")
            return self._out(text, intent, q, res)
        return self._out(text, intent, res.get("messaggio", "Richiesta non valida."), res)

    def _out(self, text: str, intent: Intent, reply: str, result: dict[str, Any] | None) -> dict[str, Any]:
        self.gate.bus.publish("assistant_done", text=reply, mode=TEXT_MODE_LABEL)
        return {"mode": TEXT_MODE_LABEL, "input": text, "intent": intent.to_dict(), "reply": reply, "result": result,
                "gate": self.gate.state.value}

    @property
    def blocked(self) -> bool:
        return self.gate.state in (GateState.STOPPED, GateState.FAULT)
