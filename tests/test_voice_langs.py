"""WP-2: five voice languages, without calling xAI."""

from app.conversation.text_mode import TextConversation
from app.monitoring.events import EventBus, History, Metrics
from app.safety.gate import GateState
from app.voice.grok_session import GrokSession

from .conftest import say, wait_state


def _session(settings, gate, lang: str) -> GrokSession:
    return GrokSession(settings, gate, EventBus(), Metrics(path=None), mode="voice", lang=lang)


def test_language_hint_zh_and_not_am(settings, gate):
    zh = _session(settings, gate, "zh").session_update()
    hint = zh["session"]["audio"]["input"]["transcription"]
    assert hint["language_hint"] == "zh"
    assert len(hint["keyterms"]) <= 100 and all(len(t) <= 50 for t in hint["keyterms"])
    am = _session(settings, gate, "am").session_update()
    tr = am["session"]["audio"]["input"]["transcription"]
    assert "language_hint" not in tr
    auto = _session(settings, gate, "auto").session_update()
    assert "language_hint" not in auto["session"]["audio"]["input"]["transcription"]


def test_ws_accepts_zh(settings, sim, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app
    captured = {}

    class Fake:
        def __init__(self, *a, lang="auto", **k):
            captured["lang"] = lang

        async def run_voice(self):
            return

    monkeypatch.setattr("app.main.GrokSession", Fake)
    app = create_app(settings, sim)
    with TestClient(app) as c, c.websocket_connect("/ws/voice?lang=zh"):
        pass
    assert captured["lang"] == "zh"


async def test_zh_rules_lift_confirm_and_cancel(gate):
    tc = TextConversation(gate)
    r = await tc.handle("左边的托盘抬起来", lang="zh")
    assert r["intent"]["skill"] == "solleva_vassoio" and r["intent"]["params"]["posizione"] == "sinistra"
    assert gate.state == GateState.PENDING_CONFIRMATION
    assert "确认" in r["reply"]
    r = await tc.handle("不要", lang="zh")
    assert gate.pending is None and gate.state == GateState.IDLE
    await tc.handle("左边的托盘抬起来", lang="zh")
    r = await tc.handle("是的", lang="zh")
    assert r["result"]["ok"]
    assert await wait_state(gate, "IDLE")


async def test_japanese_yes_confirms_any_question(gate):
    r = gate.propose("indica", {"posizione": "centro"})
    gate.mark_question_asked(r["action_id"])
    await say(gate, "はい")
    out = await gate.confirm(r["action_id"])
    assert out["ok"] and gate.state == GateState.EXECUTING
    assert await wait_state(gate, "IDLE")


async def test_shelf_is_out_of_reach(gate):
    tc = TextConversation(gate)
    r = await tc.handle("Take the tray from the shelf", lang="en")
    assert r["intent"]["kind"] == "out_of_scope" and gate.pending is None


async def test_cycle_confirm_in_zh_and_stop(gate):
    started = []

    async def starter(product_id, operator):
        started.append((product_id, operator))
        return {"ok": True, "cycle_id": "Ctest"}

    gate.cycle_starter = starter
    prop = gate.propose_cycle("crema", {
        "it": "Avvio il ciclo. Confermi?", "en": "Start the cycle. Do you confirm?",
        "zh": "开始循环。确认吗？", "ja": "開始します。確認しますか？", "am": "እጀምራለሁ። ታረጋግጣለህ?"})
    gate.mark_question_asked(prop["action_id"])
    await say(gate, "也许")
    blocked = await gate.confirm(prop["action_id"])
    assert not blocked["ok"] and started == []
    await say(gate, "是的")
    ok = await gate.confirm(prop["action_id"])
    assert ok["ok"] and started and started[0][0] == "crema"
    assert gate.note_transcript("止まって", source="voice")
    assert await wait_state(gate, "STOPPED")
    assert gate.propose("indica", {"posizione": "centro"})["esito"] == "sistema_fermo"
