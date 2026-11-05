"""Voice fast path: the rules read a finished sentence without waiting for another model turn.

An incomplete sentence returns None, and Grok may still handle it. On the real robot the gate
starts a successful proposal itself. In simulation the proposal stays until a later yes.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from ..safety.gate import Gate, GateState
from ..skills.amounts import angle_deg, distance_cm, is_nudge
from .rules import Intent, _SPLIT_STEPS, earlier_utterance, parse, parse_cup, parse_inspection, parse_jog, parse_jog_batch

_BLOCKED = (GateState.STOPPED, GateState.FAULT, GateState.EXECUTING)


def _skill_ready(intent: Intent) -> bool:
    p = intent.params
    if intent.kind != "skill" or not intent.skill:
        return False
    if intent.skill == "vai_a_posa":
        return "posa" in p
    if intent.skill == "spingi":
        return "posizione" in p and "direzione" in p
    return "posizione" in p


def _jog_ready(text: str, intent: Intent) -> bool:
    p = intent.params
    if intent.kind in ("touch", "grab"):
        return "colore" in p
    if intent.kind == "place":
        return True
    if intent.kind == "rotate":
        return "giunto" in p and "verso" in p and (angle_deg(text) is not None or is_nudge(text))
    if intent.kind == "move":
        return "direzione" in p and (distance_cm(text) is not None or is_nudge(text))
    return False


def _plan(gate: Gate, text: str) -> Callable[[], Awaitable[dict[str, Any]]] | None:
    batch = parse_jog_batch(text)
    if batch is not None:
        parts = [p for p in _SPLIT_STEPS.split(text or "") if p.strip()]
        if len(parts) != len(batch) or not all(_jog_ready(part, jog) for part, jog in zip(parts, batch)):
            return None
        steps = []
        for jog in batch:
            p = jog.params
            if jog.kind == "rotate":
                steps.append({"kind": "rotate", "giunto": p["giunto"], "verso": p["verso"]})
            else:
                steps.append({"kind": "move", "direzione": p["direzione"]})

        async def run_batch(steps: list[dict[str, Any]] = steps) -> dict[str, Any]:
            return await gate.propose_batch(steps, source="rules")

        return run_batch

    jog = parse_jog(text)
    if jog is not None and jog.note != "cup_colour" and _jog_ready(text, jog):
        p = jog.params

        async def run_jog() -> dict[str, Any]:
            if jog.kind == "touch":
                return await gate.touch_cup(p["colore"], source="rules")
            if jog.kind == "grab":
                return await gate.grab_cup(p["colore"], source="rules")
            if jog.kind == "place":
                return await gate.place_cup(source="rules")
            if jog.kind == "rotate":
                return await gate.propose_rotate(p["giunto"], p["verso"], source="rules")
            return await gate.propose_move(p["direzione"], source="rules")

        return run_jog

    if gate.pending is None:
        cup = parse_cup(text)
        if cup is not None:
            colour = cup.params["colore"]

            async def run_cup() -> dict[str, Any]:
                return await gate.point_at_cup(colour, source="rules")

            return run_cup
        insp = parse_inspection(text, holding=gate.world.holding_tray)
        if insp is not None and insp.kind == "sort":
            zone = (gate.s.inspection or {}).get("pass_zone" if insp.params.get("esito") == "PASS" else "fail_zone")

            async def run_sort(zone: str | None = zone) -> dict[str, Any]:
                return gate.propose("smista", {"posizione": zone}, source="rules")

            return run_sort
        if insp is not None and insp.kind == "inspect" and insp.note:
            rule = insp.note

            async def run_inspect() -> dict[str, Any]:
                return gate.start_inspection(rule, source="rules")

            return run_inspect

    intent = parse(text, poses=gate.s.poses, pending=False,
                   prior=earlier_utterance(gate.history.items, text))
    if intent.kind == "transfer":
        origin, dest = intent.params["origine"], intent.params["destinazione"]

        async def run_transfer() -> dict[str, Any]:
            return await gate.propose_transfer(origin, dest, source="rules")

        return run_transfer
    if _skill_ready(intent):
        skill, params = intent.skill, dict(intent.params)

        async def run_skill() -> dict[str, Any]:
            return gate.propose(skill, params, source="rules")

        return run_skill
    return None


async def propose_if_complete(gate: Gate, text: str) -> dict[str, Any] | None:
    if gate.state in _BLOCKED:
        return None
    run = _plan(gate, text)
    if run is None:
        return None
    res = await run()
    if res.get("ok") and (res.get("action_id") or res.get("esito") == "esecuzione_avviata"):
        return res
    return None
