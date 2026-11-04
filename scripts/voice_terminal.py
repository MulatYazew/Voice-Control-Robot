"""VOCE from the terminal: talk to the arm with the laptop microphone (no browser).

Same path as the web app: mic (PCM16 24 kHz) -> /ws/voice -> FastAPI -> Grok Voice -> safety gate -> robot.
The VOCE server must be running (./start.sh). Transcripts, proposals and reports are printed here.

    .venv/bin/python scripts/voice_terminal.py              # laptop speakers: mic muted while VOCE talks
    .venv/bin/python scripts/voice_terminal.py --headset    # full duplex (you can interrupt VOCE)

Keys while running:  ENTER = STOP the robot (POST /api/stop)   ·   r + ENTER = RIPRISTINA   ·   q + ENTER = quit
Saying «fermati» / «stop» also stops the robot at any time.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time

import aiohttp
import sounddevice as sd

RATE, BLOCK = 24000, 960       # 40 ms chunks
ECHO_TAIL = 0.35               # keep mic muted this long after VOCE's audio ends (echo guard)


class Player:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.lock = threading.Lock()
        self.last = 0.0
        self.stream = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=self._cb)

    def _cb(self, out, frames, _t, _s):
        n = frames * 2
        with self.lock:
            chunk = bytes(self.buf[:n]); del self.buf[:n]
        if chunk:
            self.last = time.monotonic()
        out[:len(chunk)] = chunk
        out[len(chunk):] = b"\0" * (n - len(chunk))

    def add(self, b: bytes) -> None:
        with self.lock:
            self.buf.extend(b)

    def flush(self) -> None:
        with self.lock:
            self.buf.clear()

    def speaking(self) -> bool:
        with self.lock:
            busy = bool(self.buf)
        return busy or time.monotonic() - self.last < ECHO_TAIL


def show(ev: dict, state: dict) -> None:
    t = ev.get("type")
    if t == "transcript" and ev.get("role") == "user" and ev.get("final"):
        print(f"\n🧑  {ev['text']}")
    elif t == "assistant_done" and ev.get("text", "").strip() not in ("", "…"):
        print(f"🤖  {ev['text']}")
    elif t == "tool":
        r = ev["result"]
        print(f"    🔧 {ev['name']} {json.dumps(ev['args'], ensure_ascii=False)} → {r.get('esito', 'ok' if r.get('ok') else 'errore')}")
    elif t == "report":
        print(f"    📋 {ev['text']}")
    elif t == "state" and ev.get("gate") != state.get("gate"):
        state["gate"] = ev["gate"]
        p = ev.get("pending")
        extra = f" — {p['conferma']}" if p else ""
        print(f"    ── stato: {ev['gate']}{extra}")
    elif t == "voice" and ev.get("status") in ("connected", "reconnecting", "error"):
        print(f"    🎙  Grok Voice: {ev['status']} {ev.get('detail', '')}")
    elif t == "vad" and ev.get("speaking"):
        print("    …ascolto", end="", flush=True)


async def main(url: str, headset: bool) -> None:
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
    player = Player()
    silence = b"\0" * (BLOCK * 2)

    def mic_cb(indata, _f, _t, _s):
        data = silence if (not headset and player.speaking()) else bytes(indata)
        try:
            loop.call_soon_threadsafe(q.put_nowait, data)
        except (asyncio.QueueFull, RuntimeError):
            pass

    ws_url = url.replace("http", "ws", 1)
    async with aiohttp.ClientSession() as http:
        async with http.get(url + "/api/status") as r:
            st = await r.json()
        mode = st["config"]["mode"]
        print(f"VOCE {url} · modalità {'🔴 ROBOT REALE' if mode == 'real' else mode} · robot: "
              f"{st['robot']['robot_mode'] or 'non connesso'} / {st['robot']['safety'] or '-'}")
        if not st["robot"]["ok_for_motion"]:
            print("⚠️  Robot non pronto al movimento (accendilo e rilascia i freni in URSim). La voce funziona comunque.")
        async with http.ws_connect(ws_url + "/ws/voice") as vws, http.ws_connect(ws_url + "/ws/events") as ews:
            state = {"gate": st["gate"]}

            async def send_mic():
                while True:
                    await vws.send_bytes(await q.get())

            async def recv_voice():
                async for m in vws:
                    if m.type == aiohttp.WSMsgType.BINARY:
                        player.add(m.data)
                    elif m.type == aiohttp.WSMsgType.TEXT:
                        ev = json.loads(m.data)
                        if ev.get("type") == "flush":
                            player.flush()
                        elif ev.get("type") == "error":
                            print("⚠️ ", ev.get("message")); return

            async def recv_events():
                async for m in ews:
                    ev = json.loads(m.data)
                    if ev.get("type") == "stop":
                        player.flush()
                    if not ev.get("replay"):
                        show(ev, state)

            async def keyboard():
                while True:
                    raw = await loop.run_in_executor(None, sys.stdin.readline)
                    if raw == "":          # stdin closed (not a terminal): keyboard controls disabled
                        await asyncio.Event().wait()
                    line = raw.strip().lower()
                    if line == "q":
                        return
                    if line == "r":
                        async with http.post(url + "/api/reset", json={"operator_confirmed": True}) as r:
                            res = await r.json()
                        print("    RIPRISTINA →", res.get("esito"), res.get("messaggio", ""))
                    else:  # ENTER (or anything else) = STOP
                        player.flush()
                        async with http.post(url + "/api/stop", json={"source": "button"}) as r:
                            res = await r.json()
                        print(f"    🛑 STOP → {res.get('esito')} ({res.get('ack_latency_ms')} ms)")

            mic = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=BLOCK, callback=mic_cb)
            print(f"🎤 Microfono: {sd.query_devices(kind='input')['name']} · parla in italiano "
                  f"({'cuffie: puoi interrompere' if headset else 'microfono muto mentre VOCE parla'})")
            print("   INVIO = STOP · r+INVIO = ripristina · q+INVIO = esci\n")
            tasks = [asyncio.ensure_future(c()) for c in (send_mic, recv_voice, recv_events, keyboard)]
            with mic, player.stream:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in tasks:
                t.cancel()
    print("Sessione vocale chiusa. (Se il braccio si stava muovendo, VOCE lo ha fermato.)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Talk to VOCE from the terminal")
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--headset", action="store_true")
    a = ap.parse_args()
    try:
        asyncio.run(main(a.url, a.headset))
    except KeyboardInterrupt:
        print("\nUscita.")
