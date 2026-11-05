import json

import httpx

import scene
from grokarm_vision.resolve import GrokResolver


def _transport(reply, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(json.loads(request.content))
        if isinstance(reply, Exception):
            raise reply
        content = reply if isinstance(reply, str) else json.dumps(reply)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return httpx.MockTransport(handler)


def _service(ready_service, reply, seen=None):
    ready_service.resolver = GrokResolver("test-key", "test-model", transport=_transport(reply, seen))
    return ready_service


def _put(service):
    service.store.put_image(scene.with_objects(scene.table(), [("red", 330, 120), ("blue", 260, -100)]))


def test_grok_picks_candidate_and_coordinates_come_from_calibration(ready_service):
    seen = []
    service = _service(ready_service, {"match": "one", "ids": [2], "label": "la tazza blu", "question": ""}, seen)
    _put(service)
    result = service.locate("indica la tazza", "it")
    assert result.ok
    assert result.label == "la tazza blu"
    assert abs(result.target_mm[0] - 260) < 5 and abs(result.target_mm[1] + 100) < 5
    prompt = seen[0]["messages"][0]["content"][1]["text"]
    assert "indica la tazza" in prompt and "id 1" in prompt and "id 2" in prompt
    assert seen[0]["messages"][0]["content"][0]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_grok_ambiguity_uses_its_question(ready_service):
    reply = {"match": "ambiguous", "ids": [1, 2], "label": "", "question": "Quale tazza, quella rossa o quella blu?"}
    service = _service(ready_service, reply)
    _put(service)
    result = service.locate("la tazza", "it")
    assert result.status == "ambiguous"
    assert result.message == reply["question"]


def test_invented_id_falls_back_to_offline_rules(ready_service):
    service = _service(ready_service, {"match": "one", "ids": [9], "label": "x"})
    _put(service)
    result = service.locate("the red one", "en")
    assert result.ok and result.target_mm[1] > 100
    assert result.resolution["source"] == "offline"
    assert "grok failed" in result.resolution["note"]


def test_network_error_falls_back(ready_service):
    service = _service(ready_service, httpx.ConnectError("down"))
    _put(service)
    result = service.locate("il blu", "it")
    assert result.ok and result.target_mm[1] < -50


def test_non_json_reply_falls_back(ready_service):
    service = _service(ready_service, "I think it's the red one")
    _put(service)
    assert service.locate("red", "en").resolution["source"] == "offline"


def test_grok_none(ready_service):
    service = _service(ready_service, {"match": "none", "ids": [], "label": ""})
    _put(service)
    assert service.locate("the banana", "en").status == "not_found"
