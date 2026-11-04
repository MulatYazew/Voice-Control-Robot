"""End-to-end rehearsal of the jury demo against the real Grok Voice API.

Acts as the browser: synthesises Italian speech with macOS `say`, streams it
to the running server (python -m robot_voice.server) and checks what happens.

    python scripts/rehearse_demo.py            # server on 127.0.0.1:8080
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

import aiohttp

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
VOICE = "Alice"
TMP = Path(tempfile.mkdtemp(prefix="rehearse_"))


def tts(text: str) -> bytes:
    f = TMP / f"{abs(hash(text))}.aiff"
    w = f.with_suffix(".wav")
    subprocess.run(["say", "-v", VOICE, text, "-o", str(f)], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(f), str(w)], check=True)
    return wave.open(str(w)).readframes(10**9)


class Browser:
    def __init__(self, ws):
        self.ws = ws
        self.state: dict = {}
        self.tools: list[dict] = []
        self.said: list[str] = []
        self._cur = ""
        self.resp_done = asyncio.Event()
        self.audio_bytes = 0

    async def reader(self):
        async for m in self.ws:
            if m.type == aiohttp.WSMsgType.BINARY:
                self.audio_bytes += len(m.data)
                continue
            e = json.loads(m.data)
            t = e["type"]
            if t == "state":
                self.state = e
            elif t == "tool":
                self.tools.append(e)
                print(f"    🔧 {e['name']} {json.dumps(e['args'], ensure_ascii=False)} → "
                      f"{json.dumps({k: e['result'].get(k) for k in ('ok', 'error', 'status', 'message') if k in e['result']}, ensure_ascii=False)}")
            elif t == "transcript" and e["role"] == "user" and e.get("final"):
                print(f"    👂 heard: {e['text']!r}")
            elif t == "transcript" and e["role"] == "assistant":
                self._cur += e["delta"]
            elif t == "response_done":
                if self._cur.strip():
                    print(f"    🤖 {self._cur.strip()}")
                    self.said.append(self._cur.strip())
                self._cur = ""
                self.resp_done.set()
            elif t == "log" and e["level"] in ("stop", "report", "error"):
                print(f"    📋 [{e['level']}] {e['text']}")

    async def speak(self, text: str, tail_silence=1.2):
        print(f"\n🗣  operator: {text}")
        pcm = tts(text) + b"\0" * int(24000 * 2 * tail_silence)
        for i in range(0, len(pcm), 1920):  # 40 ms chunks, real time
            await self.ws.send_bytes(pcm[i:i + 1920])
            await asyncio.sleep(0.04)

    async def idle(self, quiet=2.5, timeout=25):
        """Wait until the agent stops producing responses/audio for `quiet` s."""
        t0 = time.monotonic()
        last = (self.audio_bytes, len(self.said), len(self.tools))
        lt = time.monotonic()
        while time.monotonic() - t0 < timeout:
            await asyncio.sleep(0.2)
            cur = (self.audio_bytes, len(self.said), len(self.tools))
            if cur != last:
                last, lt = cur, time.monotonic()
            elif time.monotonic() - lt > quiet and self.state.get("status") != "moving":
                return
        # agent audio is generated faster than real time; give playback-equivalent time
        await asyncio.sleep(0.5)

    async def wait_status(self, s, timeout=20):
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if self.state.get("status") == s:
                return True
            await asyncio.sleep(0.05)
        return False


async def main():
    results = {}
    async with aiohttp.ClientSession() as http:
        await http.post(f"{URL}/api/reset")
        async with http.ws_connect(f"{URL.replace('http', 'ws')}/ws") as ws:
            b = Browser(ws)
            asyncio.create_task(b.reader())
            await asyncio.sleep(2)

            # 1. ambiguous
            await b.speak("Solleva il vassoio.")
            await b.idle()
            results["ambiguous → clarification, no plan"] = b.state.get("pending") is None and b.state.get("status") != "moving"

            # 2. clarify
            await b.speak("Quello rosso.")
            results["plan proposed, awaiting confirmation"] = await b.wait_status("awaiting_confirmation", 15)
            await b.idle()

            # 3. confirm
            await b.speak("Sì, confermo.")
            moved = await b.wait_status("moving", 10)
            await b.wait_status("idle", 30)
            await b.idle()
            results["confirmed → executed → holding red"] = moved and b.state["world"]["holding"] == "red"

            # 4. sequence + stop mid-motion
            await b.speak("Portalo a destra e poi appoggialo nello slot B.")
            ok_pending = await b.wait_status("awaiting_confirmation", 15)
            await b.idle()
            await b.speak("Sì, vai.")
            started = await b.wait_status("moving", 10)
            await asyncio.sleep(0.3)
            t_stop = time.monotonic()
            await b.speak("Ferma!", tail_silence=1.0)
            stopped = await b.wait_status("stopped", 6)
            dt = time.monotonic() - t_stop
            await b.idle()
            results[f"sequence proposed + started"] = ok_pending and started
            results[f"spoken 'Ferma!' halted the arm ({dt:.1f}s from start of word)"] = stopped
            results["arm did not finish the plan after stop"] = b.state["world"]["holding"] == "red"

    print("\n================ RESULTS ================")
    for k, v in results.items():
        print(("✅" if v else "❌"), k)
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    asyncio.run(main())
