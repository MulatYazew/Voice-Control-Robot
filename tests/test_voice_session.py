"""The assistant stays up until the operator closes it. No xAI, no network."""

import asyncio

import pytest

from app.monitoring.events import EventBus, History, Metrics
from app.safety.phrases import is_stop, is_terminate
from app.voice.grok_session import GrokSession

from .conftest import say, wait_state


class ScriptXAI:
    def __init__(self, ticks: int) -> None:
        self.ticks = ticks
        self.sent: list = []

    async def send(self, data) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.ticks = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.ticks <= 0:
            raise StopAsyncIteration
        self.ticks -= 1
        await asyncio.sleep(0.2)
        return '{"type":"ping"}'


class Client:
    def __init__(self) -> None:
        self.q: asyncio.Queue = asyncio.Queue()
        self.sent: list = []
        self.code = None

    async def receive(self):
        msg = await self.q.get()
        if msg is None:
            return {"type": "websocket.disconnect"}
        return msg

    async def send_json(self, msg) -> None:
        self.sent.append(msg)

    async def send_bytes(self, data) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000) -> None:
        self.code = code
        await self.q.put(None)


def _sess(settings, gate, client=None) -> GrokSession:
    sess = GrokSession(settings, gate, EventBus(), Metrics(path=None), client=client, mode="voice", lang="it")
    sess._backoff = lambda _n: 0
    return sess


@pytest.mark.parametrize("text", ["chiudi l'assistente", "close the assistant", "关闭助手", "アシスタントを終了"])
def test_terminate_phrases(text):
    assert is_terminate(text)


@pytest.mark.parametrize("text", ["stop", "basta", "fine", "chiudi", "停止"])
def test_bare_words_do_not_close_the_assistant(text):
    assert not is_terminate(text)
    if text != "fine" and text != "chiudi":
        assert is_stop(text)


async def test_ten_drops_and_seven_failed_connects_still_reconnect(settings, gate):
    client = Client()
    sess = _sess(settings, gate, client)
    calls = {"n": 0}

    async def connect() -> None:
        calls["n"] += 1
        if calls["n"] <= 7:
            raise OSError("down")
        sess.xai = ScriptXAI(0 if calls["n"] <= 17 else 50)
        sess._idle.set()

    sess.connect = connect  # type: ignore[method-assign]
    task = asyncio.ensure_future(sess.run_voice())
    for _ in range(200):
        if calls["n"] >= 18 and sess.status == "connected":
            break
        await asyncio.sleep(0.02)
    assert calls["n"] >= 18 and sess.status == "connected"
    await client.close()
    await asyncio.wait_for(task, 2)


async def test_ping_keeps_a_quiet_session(settings, gate):
    client = Client()
    sess = _sess(settings, gate, client)
    calls = {"n": 0}

    async def connect() -> None:
        calls["n"] += 1
        sess.xai = ScriptXAI(50)
        sess._idle.set()

    sess.connect = connect  # type: ignore[method-assign]
    task = asyncio.ensure_future(sess.run_voice())
    for _ in range(50):
        if sess.status == "connected":
            break
        await asyncio.sleep(0.02)
    await client.q.put({"text": '{"type":"ping","t":1}'})
    for _ in range(50):
        if any(isinstance(m, dict) and m.get("type") == "pong" for m in client.sent):
            break
        await asyncio.sleep(0.02)
    assert calls["n"] == 1 and sess.status == "connected"
    await client.close()
    await asyncio.wait_for(task, 2)


def test_new_browser_replaces_the_old_socket(settings, sim, monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import create_app

    made = []

    class Fake:
        def __init__(self, *a, client=None, **k):
            self.client = client
            self.finished = asyncio.Event()
            self.superseded = False
            made.append(self)

        async def supersede(self):
            self.superseded = True
            if self.client is not None:
                await self.client.close(code=4000)
            self.finished.set()

        async def run_voice(self):
            await self.finished.wait()

    monkeypatch.setattr("app.main.GrokSession", Fake)
    app = create_app(settings, sim)
    with TestClient(app) as c:
        with c.websocket_connect("/ws/voice?lang=it") as first:
            for _ in range(50):
                if made:
                    break
            assert made and made[0].client is not None
            with c.websocket_connect("/ws/voice?lang=en") as second:
                assert made[0].superseded is True
                assert len(made) == 2
                second.close()
            first.close()


async def test_end_session_stops_a_moving_robot_and_does_not_reconnect(settings, gate, sim):
    sim.time_scale = 0.1
    proposed = gate.propose("solleva_vassoio", {"posizione": "sinistra"})
    gate.mark_question_asked(proposed["action_id"])
    await say(gate, "sì, confermo")
    started = await gate.confirm(proposed["action_id"])
    assert started["ok"] and gate.state.value == "EXECUTING"
    client = Client()
    sess = _sess(settings, gate, client)

    async def connect() -> None:
        raise AssertionError("reconnect")

    sess.connect = connect  # type: ignore[method-assign]
    await sess.end_session("ui")
    assert gate.state.value == "STOPPED"
    assert sess.status == "closed"
    assert any(m.get("type") == "session_end" for m in client.sent if isinstance(m, dict))
    await asyncio.wait_for(sess.run_voice(), 2)


async def test_spoken_close_and_stop(settings, gate):
    client = Client()
    sess = _sess(settings, gate, client)
    await sess.on_final_transcript("stop")
    assert await wait_state(gate, "STOPPED")
    assert sess.status != "closed"
    await sess.on_final_transcript("chiudi l'assistente")
    assert sess.status == "closed" and sess._operator_closed


async def test_stop_and_close_in_one_sentence_stops_first(settings, gate, sim):
    sim.time_scale = 0.1
    proposed = gate.propose("solleva_vassoio", {"posizione": "sinistra"})
    gate.mark_question_asked(proposed["action_id"])
    await say(gate, "sì, confermo")
    assert (await gate.confirm(proposed["action_id"]))["ok"]
    client = Client()
    sess = _sess(settings, gate, client)
    await sess.on_final_transcript("stop, chiudi l'assistente")
    assert gate.state.value == "STOPPED" and sess.status == "closed"


async def test_grok_drop_clears_pending_and_does_not_restore_it(settings, gate):
    proposed = gate.propose("indica", {"posizione": "centro"})
    assert proposed["ok"] and gate.pending is not None
    client = Client()
    sess = _sess(settings, gate, client)
    calls = {"n": 0}

    async def connect() -> None:
        calls["n"] += 1
        sess.xai = ScriptXAI(0 if calls["n"] == 1 else 30)
        sess._idle.set()

    sess.connect = connect  # type: ignore[method-assign]
    task = asyncio.ensure_future(sess.run_voice())
    for _ in range(100):
        if calls["n"] >= 2 and gate.pending is None:
            break
        await asyncio.sleep(0.02)
    assert calls["n"] >= 2 and gate.pending is None
    await client.close()
    await asyncio.wait_for(task, 2)
    assert gate.pending is None


async def test_stop_leaves_the_session_able_to_answer_status(settings, gate):
    from app.conversation.tools import dispatch
    client = Client()
    sess = _sess(settings, gate, client)

    async def connect() -> None:
        sess.xai = ScriptXAI(40)
        sess._idle.set()

    sess.connect = connect  # type: ignore[method-assign]
    task = asyncio.ensure_future(sess.run_voice())
    for _ in range(50):
        if sess.status == "connected":
            break
        await asyncio.sleep(0.02)
    await gate.stop("ui")
    assert gate.state.value == "STOPPED" and sess.status == "connected"
    out = await dispatch(gate, "status", {})
    assert out["ok"] and out["gate"] == "STOPPED"
    await client.close()
    await asyncio.wait_for(task, 2)
