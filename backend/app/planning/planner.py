"""Turn a validated task into steps. Motion steps still need the gate."""

from __future__ import annotations

from typing import Any

from .task_schema import TaskRequest


def plan(task: TaskRequest) -> dict[str, Any]:
    if task.intent in ("stop", "cancel", "status", "explain", "out_of_scope"):
        steps = [{"kind": "report", "name": task.intent, "needs_confirmation": False}]
        return {"steps": steps, "moves": False}
    if task.intent == "inspect_and_sort":
        return {"steps": [
            {"kind": "perception", "name": "identify_pack", "needs_confirmation": False},
            {"kind": "cycle", "name": "labelling_cycle", "needs_confirmation": True},
        ], "moves": True}
    if task.intent in ("move_skill", "move_object_tray"):
        return {"steps": [{"kind": "skill", "name": task.skill or task.intent, "needs_confirmation": True}], "moves": True}
    return {"steps": [{"kind": "perception", "name": task.intent, "needs_confirmation": False}], "moves": False}
