"""Grok Voice realtime relay:  browser ⇄ FastAPI ⇄ wss://api.x.ai/v1/realtime.

* API key used only here (Authorization header), never sent to the browser.
* Browser → PCM16 24 kHz mono binary frames → input_audio_buffer.append.
* Grok audio → binary frames to the browser; everything else → EventBus (/ws/events).
* Tool calls → conversation.tools.dispatch → safety gate.
* Operator transcripts → gate (STOP spotting, confirmation validation).
* Connection loss → gate.on_voice_disconnected() (pending cleared, never restored)
  → fresh session (reconnect with back-off) while the browser is still there.

Event names verified live against grok-voice-3.0 on 2026-11-04 (see PROJECT_STATUS.md).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

import websockets

from ..config import Settings
from ..conversation.tools import build_prompt, dispatch, tool_schemas
from ..monitoring.events import EventBus, Metrics, log_event
from ..safety import phrases
from ..safety.gate import Gate

log = logging.getLogger("voce.grok")
XAI_URL = "wss://api.x.ai/v1/realtime?model={model}"


class GrokSession:
    MAX_RECONNECTS = 5

    def __init__(self, settings: Settings, gate: Gate, bus: EventBus, metrics: Metrics, *, client=None,
                 mode: str = "voice", lang: str = "auto") -> None:
        self.lang = lang                  # it | en | zh | ja | am | auto (voice language, not the UI language)
        self.s, self.gate, self.bus, self.metrics = settings, gate, bus, metrics
        self.client = client              # FastAPI WebSocket (voice) or None (text)
        self.mode = mode                  # voice | text
        self.xai: Any = None
        self.status = "idle"
        self.resp_active = False
        self.want_response = False
        self.resp_created_at = 0.0
        self.t_speech_end: float | None = None
        self._intent_measured = True
        self._lock = asyncio.Lock()
        self._closing = False
        self.tool_calls: list[dict[str, Any]] = []     # for text-mode evaluation
        self.assistant_text: list[str] = []
        self._cur = ""
        self._idle = asyncio.Event()
        self._unsub: list = []

    # ---------------------------------------------------------- outbound ---
    def _set_status(self, st: str, detail: str = "") -> None:
        self.status = st
        self.bus.publish("voice", status=st, detail=detail, mode=self.mode)

    async def to_xai(self, ev: dict[str, Any]) -> None:
        if self.xai is None:
            return
        async with self._lock:
            await self.xai.send(json.dumps(ev))

    async def to_client(self, msg: dict[str, Any] | bytes) -> None:
        if self.client is None:
            return
        try:
            if isinstance(msg, bytes):
                await self.client.send_bytes(msg)
            else:
                await self.client.send_json(msg)
        except Exception:
            pass

    async def request_response(self) -> None:
        if self.resp_active:
            self.want_response = True
            return
        self.want_response = False
        self.resp_active = True
        self._idle.clear()
        await self.to_xai({"type": "response.create"})

    async def inject(self, text: str, *, respond: bool = True) -> None:
        await self.to_xai({"type": "conversation.item.create", "item": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})
        if respond:
            await self.request_response()

    def session_update(self) -> dict[str, Any]:
        from ..safety.phrases import LANGS, load_lexicon
        terms = ["stop", "fermati", "ferma", "basta", "alt", "blocca tutto", "vassoio", "sinistra", "centro", "destra",
                 "solleva", "appoggia", "spingi", "indica", "confermo", "annulla", "procedi",
                 "left", "right", "centre", "center", "lift", "push", "point", "confirm", "cancel", "yes", "stop now"]
        for name in self.gate.reg.selectable_poses():   # pose names + aliases ("home" was heard as "ON")
            terms += [name.replace("_", " ")] + list(self.s.poses[name].get("alias", []))
        lex = load_lexicon()
        wanted = [self.lang] if self.lang in LANGS else list(LANGS)
        for key in ("stop", "affirm", "zones"):
            block = lex.get(key) or {}
            if key == "zones":
                for per_lang in block.values():
                    for lang in wanted:
                        terms.extend(per_lang.get(lang) or [])
            else:
                for lang in wanted:
                    terms.extend(block.get(lang) or [])
        try:
            from ..perception.catalog import Catalog
            terms.extend(Catalog(self.s.data_dir / "catalog").keyterms())
        except Exception:
            pass
        tr: dict[str, Any] = {"keyterms": list(dict.fromkeys(t[:50] for t in terms if t))[:100]}
        # Amharic is not in the official STT language list: never send it as a hint.
        if self.lang in ("it", "en", "zh", "ja"):
            tr["language_hint"] = self.lang
        pref = {
            "it": "\nLingua preferita dall'operatore (selettore voce): italiano.",
            "en": "\nOperator's preferred language (voice selector): English. Reply in English unless the operator switches.",
            "zh": "\n操作员的语音语言：中文（普通话）。用中文回答。",
            "ja": "\nオペレーターの音声言語：日本語。日本語で答えてください。",
            "am": "\nየድምጽ ቋንቋ፡ አማርኛ። በአማርኛ መልስ። This language is experimental.",
            "auto": "",
        }.get(self.lang, "")
        return {"type": "session.update", "session": {
            "instructions": build_prompt(self.s, self.gate) + pref,
            "voice": self.s.xai_voice,
            "turn_detection": {"type": "server_vad"},
            "audio": {"input": {"format": {"type": "audio/pcm", "rate": 24000}, "transcription": tr},
                      "output": {"format": {"type": "audio/pcm", "rate": 24000}}},
            "tools": tool_schemas(self.s, self.gate.reg.selectable_poses()),
        }}

    # ------------------------------------------------------- gate hooks ---
    async def _on_report(self, ev: dict[str, Any]) -> None:
        if self.xai is not None:
            await self.inject(f"[RAPPORTO SISTEMA — non è l'operatore] {ev['text']} (EN: {ev.get('text_en', ev['text'])}) "
                              "Riferiscilo in una o due frasi, nella lingua dell'operatore.")

    async def _watch_bus(self) -> None:
        q = self.bus.subscribe()
        try:
            while True:
                ev = await q.get()
                if ev["type"] == "stop":
                    await self.to_client({"type": "flush"})     # stop local playback immediately
                    if self.resp_active:
                        await self.to_xai({"type": "response.cancel"})
        finally:
            self.bus.unsubscribe(q)

    # ------------------------------------------------------------ tools ---
    async def _function_call(self, ev: dict[str, Any]) -> None:
        name = ev.get("name", "")
        try:
            args = json.loads(ev.get("arguments") or "{}")
        except json.JSONDecodeError:
            args = {}
        now = time.monotonic()
        if self.t_speech_end and not self._intent_measured and name != "stop":
            self.metrics.record("intent_processing_ms", (now - self.t_speech_end) * 1000, tool=name)
            self._intent_measured = True
        if name == "stop" and self.t_speech_end:
            self.metrics.record("grok_stop_tool_ms", (now - self.t_speech_end) * 1000)
        lingua = str(args.get("lingua") or "").lower()
        if lingua in ("it", "en", "zh", "ja", "am"):
            self.gate.operator_lang = lingua
        elif self.lang in ("it", "en", "zh", "ja", "am"):
            self.gate.operator_lang = self.lang
        result = await dispatch(self.gate, name, args, source="grok" if self.mode == "voice" else "grok_text")
        self.tool_calls.append({"name": name, "args": args, "result": result})
        log_event(log, f"tool {name}", args=args, result={k: result.get(k) for k in ("ok", "esito", "action_id")})
        self.bus.publish("tool", name=name, args=args, result=result, mode=self.mode)
        await self.to_xai({"type": "conversation.item.create", "item": {
            "type": "function_call_output", "call_id": ev["call_id"], "output": json.dumps(result, ensure_ascii=False)}})
        if name == "save_pose" and result.get("ok"):
            await self.to_xai({"type": "session.update", "session": {
                "tools": tool_schemas(self.s, self.gate.reg.selectable_poses())}})
        self.want_response = True
        if not self.resp_active:
            await self.request_response()

    # -------------------------------------------------------- inbound ---
    async def _pump_xai(self) -> None:
        async for raw in self.xai:
            if isinstance(raw, bytes):
                await self.to_client(raw)
                continue
            ev = json.loads(raw)
            t = ev.get("type", "")
            if t in ("response.output_audio.delta", "response.audio.delta"):
                if self.mode == "voice":
                    await self.to_client(base64.b64decode(ev["delta"]))
            elif t == "input_audio_buffer.speech_started":
                await self.to_client({"type": "flush"})       # barge-in
                self.bus.publish("vad", speaking=True)
            elif t == "input_audio_buffer.speech_stopped":
                self.t_speech_end = time.monotonic()
                self._intent_measured = False
                self.bus.publish("vad", speaking=False)
            elif t == "input_audio_buffer.committed":
                self.gate.note_turn_committed("voice")
            elif t == "conversation.item.input_audio_transcription.updated":
                txt = ev.get("transcript", "")
                self.bus.publish("transcript", role="user", text=txt, final=False, source="voice")
                self.gate.note_partial(txt)
            elif t == "conversation.item.input_audio_transcription.completed":
                txt = ev.get("transcript", "")
                if phrases.is_stop(txt) and self.t_speech_end:
                    self.metrics.record("transcript_stop_detect_ms", (time.monotonic() - self.t_speech_end) * 1000)
                self.gate.note_transcript(txt, source="voice")
            elif t == "response.created":
                self.resp_active = True
                self.resp_created_at = self.gate.clock()
                self._idle.clear()
            elif t == "response.output_audio_transcript.delta":
                self._cur += ev.get("delta", "")
                self.bus.publish("transcript", role="assistant", delta=ev.get("delta", ""), item=ev.get("item_id"),
                                 mode=self.mode)
            elif t == "response.function_call_arguments.done":
                await self._function_call(ev)
            elif t == "response.done":
                self.resp_active = False
                if self._cur.strip():
                    self.assistant_text.append(self._cur.strip())
                    self.bus.publish("assistant_done", text=self._cur.strip(), mode=self.mode)
                self._cur = ""
                pa = self.gate.pending
                # the confirmation question is spoken in a response generated after the proposal existed
                if pa and pa.question_asked_at is None and self.resp_created_at >= pa.created:
                    self.gate.mark_question_asked(pa.id)
                if self.want_response:
                    await self.request_response()
                else:
                    self._idle.set()
            elif t == "error":
                log_event(log, "xai error", error=ev.get("error", ev))
                self.bus.publish("voice_error", error=ev.get("error", ev))
            elif t == "session.updated" and self.status != "connected":
                self._set_status("connected", f"Grok Voice ({self.s.xai_model})")

    async def _pump_client(self) -> None:
        while True:
            msg = await self.client.receive()
            if msg.get("type") == "websocket.disconnect":
                return
            if msg.get("bytes") is not None:
                await self.to_xai({"type": "input_audio_buffer.append", "audio": base64.b64encode(msg["bytes"]).decode()})
            elif msg.get("text"):
                try:
                    m = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if m.get("type") == "mic":
                    self.bus.publish("mic", on=bool(m.get("on")))
                    if not m.get("on"):
                        await self.to_xai({"type": "input_audio_buffer.clear"})
                elif m.get("type") == "audio_level":
                    pass

    # -------------------------------------------------------------- run ---
    async def connect(self) -> None:
        if not self.s.xai_api_key:
            raise RuntimeError("XAI_API_KEY non configurata")
        self._set_status("connecting")
        self.xai = await asyncio.wait_for(websockets.connect(
            XAI_URL.format(model=self.s.xai_model), additional_headers={"Authorization": f"Bearer {self.s.xai_api_key}"},
            max_size=None, ping_interval=20, open_timeout=10), 12)
        await self.to_xai(self.session_update())
        self.resp_active = False
        self.want_response = False
        self._idle.set()

    async def run_voice(self) -> None:
        """Serve one browser voice connection until it disconnects."""
        self._unsub.append(self.gate.on_report(self._on_report))
        watcher = asyncio.ensure_future(self._watch_bus())
        client_task = asyncio.ensure_future(self._pump_client())
        attempts = 0
        try:
            while not client_task.done():
                try:
                    await self.connect()
                    attempts = 0
                    xai_task = asyncio.ensure_future(self._pump_xai())
                    done, _ = await asyncio.wait({xai_task, client_task}, return_when=asyncio.FIRST_COMPLETED)
                    if client_task in done:
                        xai_task.cancel()
                        break
                    exc = xai_task.exception()
                    reason = f"connessione Grok chiusa ({exc or 'fine flusso'})"
                except (OSError, asyncio.TimeoutError, websockets.WebSocketException, RuntimeError) as e:
                    reason = f"connessione Grok fallita: {e}"
                await self._drop(reason)
                attempts += 1
                if attempts > self.MAX_RECONNECTS or not self.s.xai_api_key:
                    self._set_status("error", reason)
                    await client_task
                    break
                self._set_status("reconnecting", f"{reason} — nuovo tentativo {attempts}/{self.MAX_RECONNECTS}")
                await asyncio.sleep(min(2 ** (attempts - 1), 8))
        finally:
            watcher.cancel()
            client_task.cancel()
            await self._drop("browser disconnesso", final=True)
            for u in self._unsub:
                u()

    async def _drop(self, reason: str, *, final: bool = False) -> None:
        if self.xai is not None:
            try:
                await self.xai.close()
            except Exception:
                pass
        self.xai = None
        self.resp_active = False
        log_event(log, "voice session dropped", reason=reason, final=final)
        await self.gate.on_voice_disconnected(reason)
        if final:
            self._set_status("closed", reason)

    # ------------------------------------------------- text mode (eval) ---
    async def start_text(self) -> None:
        await self.connect()
        self._pump = asyncio.ensure_future(self._pump_xai())
        self._unsub.append(self.gate.on_report(self._on_report))

    async def say_text(self, text: str, *, timeout: float = 20.0) -> list[dict[str, Any]]:
        """Send an operator text turn to Grok; wait until it is idle. Returns tool calls made in this turn."""
        n0 = len(self.tool_calls)
        await self._idle_wait(timeout)
        self.gate.note_turn_committed("text")
        stop = self.gate.note_transcript(text, source="text")
        self.t_speech_end = time.monotonic()
        self._intent_measured = False
        _ = stop
        await self.inject(text)
        await asyncio.sleep(0.2)
        await self._idle_wait(timeout)
        return self.tool_calls[n0:]

    async def _idle_wait(self, timeout: float) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if not self.resp_active and not self.want_response and self._idle.is_set():
                await asyncio.sleep(0.3)  # follow-up responses may start right after
                if not self.resp_active and not self.want_response:
                    return
            await asyncio.sleep(0.05)

    async def close(self) -> None:
        if getattr(self, "_pump", None):
            self._pump.cancel()
        for u in self._unsub:
            u()
        if self.xai is not None:
            await self.xai.close()
            self.xai = None
