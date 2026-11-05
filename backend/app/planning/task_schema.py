"""Strict task record. A field stays null unless the operator said it."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..config import LOG_DIR

LANGS = ("it", "en", "zh", "ja", "am")
INTENTS = (
    "move_skill", "locate_object", "describe_scene", "move_object_tray", "learn_product",
    "check_label_sheet", "inspect", "inspect_and_sort", "explain", "status", "cancel", "stop", "out_of_scope",
)

_TOOLS = {
    "propose_skill": "move_skill", "sposta_vassoio": "move_object_tray", "locate_object": "locate_object",
    "describe_scene": "describe_scene", "identify_zone": "describe_scene", "find_product": "describe_scene",
    "check_labels": "inspect", "learn_model_pack": "learn_product", "analyse_model_pack": "learn_product",
    "check_label_sheet": "check_label_sheet", "validate_label_sheet": "check_label_sheet",
    "inspect_work_area": "inspect", "request_inspection": "inspect", "propose_labelling_cycle": "inspect_and_sort",
    "explain_inspection": "explain", "status": "status", "cancel": "cancel", "stop": "stop",
}


class Oggetto(BaseModel):
    model_config = ConfigDict(extra="forbid")
    categoria: str | None = None
    colore: str | None = None
    testo: str | None = None
    marca: str | None = None
    prodotto_id: str | None = None
    zona: str | None = None


class Controlli(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fronte: bool | None = None
    retro: bool | None = None
    inci: bool | None = None
    testo_legale: bool | None = None


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal[
        "move_skill", "locate_object", "describe_scene", "move_object_tray", "learn_product",
        "check_label_sheet", "inspect", "inspect_and_sort", "explain", "status", "cancel", "stop", "out_of_scope"]
    lingua: Literal["it", "en", "zh", "ja", "am"] = "it"
    skill: str | None = None
    parametri: dict[str, Any] = Field(default_factory=dict)
    oggetto: Oggetto = Field(default_factory=Oggetto)
    destinazione: str | None = None
    controlli: Controlli = Field(default_factory=Controlli)
    richiede_chiarimento: bool = False
    motivo_chiarimento: str | None = None


def task_from_tool(name: str, args: dict[str, Any] | None, lingua: str | None) -> TaskRequest:
    args = args or {}
    lang = lingua if lingua in LANGS else "it"
    if isinstance(args.get("lingua"), str) and args["lingua"] in LANGS:
        lang = args["lingua"]
    params = args.get("parametri") if isinstance(args.get("parametri"), dict) else {}
    oggetto = Oggetto()
    for key in ("categoria", "colore", "testo", "marca", "prodotto_id", "zona"):
        if args.get(key) is not None:
            setattr(oggetto, key, args.get(key))
    return TaskRequest(intent=_TOOLS.get(name, "out_of_scope"), lingua=lang, skill=args.get("skill"),
                       parametri=params, oggetto=oggetto, destinazione=args.get("destinazione"))


def record_tool_call(name: str, args: dict[str, Any] | None, lingua: str | None, path=None) -> TaskRequest:
    task = task_from_tool(name, args, lingua)
    dest = path or (LOG_DIR / "voce.jsonl")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"tool": name, **task.model_dump()}, ensure_ascii=False) + "\n")
    return task


def parse_task(raw: dict[str, Any]) -> TaskRequest:
    try:
        return TaskRequest.model_validate(raw)
    except ValidationError:
        raise
