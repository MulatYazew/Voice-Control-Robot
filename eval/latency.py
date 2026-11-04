"""STOP / execution latency trials against a RUNNING VOCE server (URSim or sim).

    python -m eval.latency --trials 10                 # UI + transcript(text) pathways
    python -m eval.latency --trials 10 --spoken        # + spoken "Fermati!" via /ws/voice (Grok, macOS `say`)

Each trial: start a real motion (rules text mode: propose + "sì"), wait until the robot is
moving, trigger STOP through one pathway, read the gate's measured ack / halt latency, reset.
Writes eval/results/latency_<stamp>.json and eval/results/latency_latest.json.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import aiohttp

from app.config import ROOT

RESULTS = ROOT / "eval" / "results"


def stats(xs: list[float]) -> dict[str, Any]:
    xs = [x for x in xs if x is not None]
    return {"n": len(xs), "median_ms": round(statistics.median(xs), 1), "max_ms": round(max(xs), 1)} if xs else {"n": 0}


class Client:
    def __init__(self, http: aiohttp.ClientSession, url: str) -> None:
        self.http, self.url = http, url

    async def status(self) -> dict[str, Any]:
        async with self.http.get(self.url + "/api/status") as r:
            return await r.json()

    async def text(self, t: str) -> dict[str, Any]:
        async with self.http.post(self.url + "/api/text-command", json={"text": t, "engine": "rules"}) as r:
            return await r.json()

    async def wait(self, cond, timeout: float = 20.0) -> dict[str, Any] | None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = await self.status()
            if cond(st):
                return st
            await asyncio.sleep(0.02)
        return None

    async def reset(self) -> None:
        async with self.http.post(self.url + "/api/reset", json={"operator_confirmed": True}) as r:
            await r.json()

    async def start_motion(self, i: int) -> bool:
        await self.reset()
        st = await self.status()
        q = st["robot"]["joints"] or [0.0] * 6
        poses = st["layout"]["poses"]
        dist = {k: max(abs(a - b) for a, b in zip(q, poses[k])) for k in ("home", "attesa")}
        target = "torna a casa" if dist["home"] >= dist["attesa"] else "vai in posizione di attesa"
        for attempt in range(3):
            await self.reset()
            await self.text(target)
            await self.text("sì")
            st = await self.wait(lambda s: s["gate"] == "EXECUTING" and s["robot"]["moving"], 6)
            if st is not None:
                await asyncio.sleep(0.6)  # mid-motion
                st = await self.status()
                if st["gate"] == "EXECUTING" and st["robot"]["moving"]:
                    return True
            await self.wait(lambda s: s["gate"] != "EXECUTING", 15)
            target = "vai in posizione di attesa" if "casa" in target else "torna a casa"
        return False


def tts(text: str) -> bytes:
    d = Path(tempfile.mkdtemp())
    subprocess.run(["say", "-v", "Alice", text, "-o", str(d / "a.aiff")], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@24000", "-c", "1", str(d / "a.aiff"), str(d / "a.wav")], check=True)
    return wave.open(str(d / "a.wav")).readframes(10**9)


async def trial_http(c: Client, i: int) -> dict[str, Any]:
    if not await c.start_motion(i):
        return {"ok": False, "error": "motion did not start"}
    t0 = time.monotonic()
    wall0 = time.time()
    async with c.http.post(c.url + "/api/stop", json={"source": "button"}) as r:
        await r.json()
    rtt = (time.monotonic() - t0) * 1000
    st = await c.wait(lambda s: (s["last_stop"] or {}).get("requested_at", 0) >= wall0, 5) or await c.status()
    ls = st["last_stop"]
    return {"ok": st["gate"] == "STOPPED", "client_rtt_ms": round(rtt, 1), "ack_ms": ls["ack_latency_ms"],
            "halt_ms": ls.get("halt_latency_ms"), "robot_moving_after": st["robot"]["moving"]}


async def trial_transcript_text(c: Client, i: int) -> dict[str, Any]:
    if not await c.start_motion(i):
        return {"ok": False, "error": "motion did not start"}
    t0 = time.monotonic()
    wall0 = time.time()
    await c.text("Fermati!")
    st = await c.wait(lambda s: s["gate"] == "STOPPED" and (s["last_stop"] or {}).get("requested_at", 0) >= wall0, 5)
    rtt = (time.monotonic() - t0) * 1000
    ls = (st or {}).get("last_stop") or {}
    return {"ok": bool(st), "client_ms": round(rtt, 1), "ack_ms": ls.get("ack_latency_ms"), "halt_ms": ls.get("halt_latency_ms")}


async def spoken_trials(c: Client, n: int) -> list[dict[str, Any]]:
    pcm = tts("Fermati!")
    silence = b"\0\0" * 960
    out = []
    async with c.http.ws_connect(c.url.replace("http", "ws", 1) + "/ws/voice") as vws, \
            c.http.ws_connect(c.url.replace("http", "ws", 1) + "/ws/events") as ews:
        stop_flag = {"t": None}

        async def pump_silence():
            while True:
                if not stop_flag.get("speaking"):
                    await vws.send_bytes(silence)
                await asyncio.sleep(0.04)

        async def watch():
            async for m in ews:
                ev = json.loads(m.data)
                if ev.get("type") == "stop" and stop_flag["t"] is None:
                    stop_flag["t"] = time.monotonic()
                    stop_flag["src"] = ev.get("source")

        async def drain_voice():
            async for _ in vws:
                pass

        tasks = [asyncio.ensure_future(x()) for x in (pump_silence, watch, drain_voice)]
        await asyncio.sleep(4)  # Grok session up
        for i in range(n):
            if not await c.start_motion(i):
                out.append({"ok": False, "error": "motion did not start"})
                continue
            stop_flag.update(t=None, src=None, speaking=True)
            for k in range(0, len(pcm), 1920):
                await vws.send_bytes(pcm[k:k + 1920])
                await asyncio.sleep(0.04)
            t_end = time.monotonic()
            stop_flag["speaking"] = False
            end = time.monotonic() + 8
            while stop_flag["t"] is None and time.monotonic() < end:
                await asyncio.sleep(0.01)
            st = await c.wait(lambda s: (s["last_stop"] or {}).get("requested_at", 0) >= time.time() - 6, 5) or await c.status()
            ls = st["last_stop"] or {}
            out.append({"ok": stop_flag["t"] is not None, "source": stop_flag.get("src"),
                        "speech_end_to_stop_ms": round((stop_flag["t"] - t_end) * 1000, 1) if stop_flag["t"] else None,
                        "ack_ms": ls.get("ack_latency_ms"), "halt_ms": ls.get("halt_latency_ms")})
            print(f"  spoken {i + 1}: {out[-1]}", flush=True)
            await asyncio.sleep(3)  # let Grok finish its spoken report
        for t in tasks:
            t.cancel()
    return out


async def main_async(a) -> None:
    async with aiohttp.ClientSession() as http:
        c = Client(http, a.url)
        st0 = await c.status()
        env = f"{st0['mode']} ({st0['robot']['detail']})"
        print("environment:", env)
        res: dict[str, Any] = {"environment": env, "trials": {}}
        for name, fn in (("ui_button", trial_http), ("transcript_text", trial_transcript_text)):
            rows = []
            for i in range(a.trials):
                rows.append(await fn(c, i))
                print(f"  {name} {i + 1}: {rows[-1]}", flush=True)
            res["trials"][name] = rows
        if a.spoken:
            res["trials"]["spoken"] = await spoken_trials(c, a.trials)
        await c.reset()
        st = await c.status()
        T = res["trials"]
        res["stop"] = {
            "UI STOP (pulsante → ack robot)": stats([r.get("ack_ms") for r in T["ui_button"] if r["ok"]]),
            "UI STOP (pulsante → robot fermo, RTDE)": stats([r.get("halt_ms") for r in T["ui_button"] if r["ok"]]),
            "UI STOP (round-trip HTTP client)": stats([r.get("client_rtt_ms") for r in T["ui_button"] if r["ok"]]),
            "STOP da trascrizione testo (→ robot fermo)": stats([r.get("halt_ms") for r in T["transcript_text"] if r["ok"]]),
        }
        if a.spoken:
            sp = T["spoken"]
            res["stop"]["STOP vocale (fine parlato → STOP, end-to-end)"] = stats([r.get("speech_end_to_stop_ms") for r in sp if r["ok"]])
            res["stop"]["STOP vocale (→ robot fermo dopo richiesta)"] = stats([r.get("halt_ms") for r in sp if r["ok"]])
            m = st["metrics"]
            for k, label in (("transcript_stop_detect_ms", "fine parlato → trascrizione con «stop»"),
                             ("grok_stop_tool_ms", "fine parlato → tool stop di Grok")):
                if k in m:
                    res["stop"][f"server: {label}"] = {"n": m[k]["n"], "median_ms": m[k]["median_ms"], "max_ms": m[k]["max_ms"]}
            res["stop_sources_first"] = {s: sum(1 for r in sp if r.get("source") == s) for s in {r.get("source") for r in sp}}
        res["execution"] = {k: st["metrics"][k] for k in ("robot_execution_ms", "request_to_completion_ms",
                                                           "confirmation_response_ms", "intent_processing_ms") if k in st["metrics"]}
        res["measured_at"] = time.strftime("%Y-%m-%d %H:%M")
        RESULTS.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (RESULTS / f"latency_{stamp}.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
        (RESULTS / "latency_latest.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
        print(json.dumps(res["stop"], ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000")
    ap.add_argument("--trials", type=int, default=10)
    ap.add_argument("--spoken", action="store_true")
    asyncio.run(main_async(ap.parse_args()))


if __name__ == "__main__":
    main()
