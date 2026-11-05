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
from ..safety.gate import Gate, GateState

log = logging.getLogger("voce.grok")
XAI_URL = "wss://api.x.ai/v1/realtime?model={model}"


class GrokSession:
    """Reconnects for as long as the browser stays. Only the operator closes the assistant."""

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
        self._operator_closed = False
        self._ending = False
        self._last_user = ""
        self._audio_until = 0.0
        self._wake = asyncio.Event()
        self.finished = asyncio.Event()
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
                elif ev["type"] == "stop_ack" and self._end_voice_on_stop() and self._operator_stop(str(ev.get("source") or "")):
                    await self.end_session("operator_stop", source=str(ev.get("source") or ""), transcript=str(ev.get("detail") or ""))
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
                self._audio_until = time.monotonic() + 5
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
                self.note_partial(txt)
            elif t == "conversation.item.input_audio_transcription.completed":
                await self.on_final_transcript(ev.get("transcript", ""))
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
                self._audio_until = time.monotonic() + 0.7
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
                if m.get("type") == "ping":
                    await self.to_client({"type": "pong", "t": m.get("t")})
                elif m.get("type") == "end_session":
                    await self.end_session("ui")
                    return
                elif m.get("type") == "mic":
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

    def _backoff(self, attempts: int) -> float:
        return float(min(2 ** max(attempts - 1, 0), 15))

    async def _pause(self, seconds: float, client_task: asyncio.Task) -> bool:
        """True when the browser left or the operator closed the assistant."""
        if self._closing or client_task.done():
            return True
        sleep = asyncio.ensure_future(asyncio.sleep(seconds))
        wake = asyncio.ensure_future(self._wake.wait())
        await asyncio.wait({sleep, client_task, wake}, return_when=asyncio.FIRST_COMPLETED)
        sleep.cancel()
        wake.cancel()
        return self._closing or client_task.done()

    async def supersede(self) -> None:
        """A newer browser connection takes the slot. Close this socket with 4000."""
        self._closing = True
        self._wake.set()
        if self.client is not None:
            try:
                await self.client.close(code=4000)
            except Exception:
                pass

    def _end_voice_on_stop(self) -> bool:
        return bool((self.s.robot.get("safety") or {}).get("end_voice_on_stop", True))

    @staticmethod
    def _operator_stop(source: str) -> bool:
        return source == "ui" or source.startswith(("ui:", "transcript:", "grok_tool"))

    def note_partial(self, txt: str) -> bool:
        """Partials heard while the speaker is playing are the assistant, not the operator."""
        if time.monotonic() < self._audio_until:
            return False
        return self.gate.note_partial(txt)

    def _is_echo(self, txt: str) -> bool:
        spoken = self.assistant_text[-1] if self.assistant_text else ""
        if not spoken or not txt:
            return False
        from ..labelling.ocr import ratio
        return ratio(txt, spoken) >= 80

    async def end_session(self, reason: str, *, source: str = "", transcript: str = "") -> None:
        """Close the assistant. A moving robot is stopped first, except when this follows an operator STOP."""
        if self._ending:
            return
        self._ending = True
        self._closing = True
        self._operator_closed = True
        log_event(log, "session end", source=source or reason, reason=reason, transcript=transcript or self._last_user)
        moving = self.gate.state == GateState.EXECUTING
        robot = getattr(self.gate, "robot_status", None)
        if reason not in ("spoken_stop", "operator_stop") and (moving or (robot is not None and getattr(robot, "moving", False))):
            await self.gate.stop("assistant_closed", detail=reason)
        banner = ""
        if reason in ("spoken_stop", "operator_stop"):
            banner = f'Stopped because: {source or reason} — “{transcript or self._last_user}”'
            await self.to_client({"type": "notice", "banner": banner})
        await self.to_client({"type": "session_end", "reason": reason, "banner": banner,
                              "message": "Assistente chiuso / Assistant closed"})
        self._set_status("closed", reason)
        if self.client is not None:
            try:
                await self.client.close(code=1000)
            except Exception:
                pass
        self._wake.set()

    async def on_final_transcript(self, txt: str) -> None:
        """STOP wins, unless the mic is hearing the assistant. A close phrase is final-only."""
        self._last_user = txt or ""
        if phrases.is_stop(txt) and self.t_speech_end:
            self.metrics.record("transcript_stop_detect_ms", (time.monotonic() - self.t_speech_end) * 1000)
        if phrases.is_stop(txt) and self._is_echo(txt):
            log_event(log, "echo ignored", source="transcript:voice", reason="echo", transcript=txt)
            self.gate.note_transcript(txt, source="voice", stop_allowed=False)
            return
        if phrases.is_terminate(txt):
            if phrases.is_stop(txt):
                await self.gate.stop("transcript:voice", detail=txt or "")
            self.gate.note_transcript(txt, source="voice", stop_allowed=False)
            await self.end_session("spoken", source="transcript:voice", transcript=txt)
            return
        if phrases.is_stop(txt):
            await self.gate.stop("transcript:voice", detail=txt or "")
            self.gate.note_transcript(txt, source="voice", stop_allowed=False)
            if self._end_voice_on_stop():
                await self.end_session("spoken_stop", source="transcript:voice", transcript=txt)
            return
        self.gate.note_transcript(txt, source="voice")

    async def run_voice(self) -> None:
        """Serve one browser voice connection until the operator closes it or the socket ends."""
        self._unsub.append(self.gate.on_report(self._on_report))
        watcher = asyncio.ensure_future(self._watch_bus())
        client_task = asyncio.ensure_future(self._pump_client())
        attempts = 0
        try:
            while not client_task.done() and not self._closing:
                reason = "connessione Grok chiusa"
                try:
                    await self.connect()
                    attempts = 0
                    self._set_status("connected", f"Grok Voice ({self.s.xai_model})")
                    xai_task = asyncio.ensure_future(self._pump_xai())
                    done, _ = await asyncio.wait({xai_task, client_task}, return_when=asyncio.FIRST_COMPLETED)
                    if client_task in done or self._closing:
                        xai_task.cancel()
                        break
                    exc = xai_task.exception()
                    reason = f"connessione Grok chiusa ({exc or 'fine flusso'})"
                except (OSError, asyncio.TimeoutError, websockets.WebSocketException, RuntimeError) as e:
                    reason = f"connessione Grok fallita: {e}"
                if self._closing or client_task.done():
                    break
                await self._drop(reason)
                if not self.s.xai_api_key:
                    self._set_status("error", reason)
                    break
                attempts += 1
                delay = self._backoff(attempts)
                log_event(log, "voice reconnect", source="grok", reason=reason, transcript=self._last_user)
                detail = f"reconnecting (attempt {attempts}, next in {delay:g}s)"
                self._set_status("reconnecting", detail)
                await self.to_client({"type": "voice_status", "status": "reconnecting", "detail": detail})
                if await self._pause(delay, client_task):
                    break
        finally:
            watcher.cancel()
            client_task.cancel()
            await self._drop("assistente chiuso" if self._operator_closed else "browser disconnesso", final=True)
            for u in self._unsub:
                u()
            self.finished.set()

    async def _drop(self, reason: str, *, final: bool = False) -> None:
        if self.xai is not None:
            try:
                await self.xai.close()
            except Exception:
                pass
        self.xai = None
        self.resp_active = False
        log_event(log, "voice session dropped", source="browser" if final else "grok", reason=reason,
                  transcript=self._last_user, final=final)
        if not final:
            await self.to_client({"type": "notice", "banner": "voice STOP temporarily unavailable — use the STOP button"})
        await self.gate.on_voice_disconnected(reason, stop_motion=False if not final else None)
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
