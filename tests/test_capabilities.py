"""Capability report and the strict task record."""

import json

import pytest
from pydantic import ValidationError

from app.capabilities import build_capabilities
from app.planning.planner import plan
from app.planning.task_schema import TaskRequest, record_tool_call


def test_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        TaskRequest.model_validate({"intent": "stop", "inventato": True})


def test_arm3_reports_no_camera(settings):
    cap = build_capabilities(settings, robot_connected=True, frame_age_s=0.1, catalog_size=0)
    assert cap["station"] == "arm3"
    assert cap["camera"]["available"] is False
    assert cap["camera"]["connected"] is False
    assert cap["tool"]["gripper"] is False
    assert "forca" in cap["tool"]["features"]
    assert cap["robot"]["model"] == "UNKNOWN"
    assert all(v is False for v in cap["poses"].values())


def test_capabilities_follow_a_changed_config(settings):
    settings.robot["robot"]["station"] = "arm1"
    settings.camera["available"] = True
    settings.camera["zones_px"] = {z: [[0, 0], [1, 0], [1, 1]] for z in ("sinistra", "centro", "destra")}
    cap = build_capabilities(settings, robot_connected=False, frame_age_s=0.2, catalog_size=3)
    assert cap["station"] == "arm1" and cap["camera"]["available"] is True
    assert cap["camera"]["zones_calibrated"] is True and cap["catalogue_size"] == 3


def test_task_is_logged_and_plans_a_cycle(tmp_path):
    task = record_tool_call("propose_labelling_cycle", {"product_id": "crema", "lingua": "zh"}, "it",
                            path=tmp_path / "voce.jsonl")
    assert task.intent == "inspect_and_sort" and task.lingua == "zh"
    saved = json.loads((tmp_path / "voce.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert saved["intent"] == "inspect_and_sort"
    steps = plan(task)
    assert steps["moves"] is True and steps["steps"][-1]["needs_confirmation"] is True
    assert plan(TaskRequest(intent="locate_object"))["moves"] is False
