"""Rehearse the five jury tests end to end through the REAL browser UI and Grok Voice.

Chromium's fake microphone plays synthesized Italian speech (macOS `say`), so audio follows
exactly the production path: AudioWorklet -> /ws/voice -> FastAPI -> Grok -> gate -> robot.
Saves screenshots + a screen recording (video, no audio) as backup demo material.

    pip install playwright && playwright install chromium     # once (not a project dependency)
    ./start.sh ursim                                          # in another terminal
    python scripts/rehearse_jury.py [--out recordings/]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

URL = "http://localhost:8000"
RATE = 24000

# (seconds of silence BEFORE the line, line, label)
TIMELINE = [
    (3.0, "Solleva il vassoio a sinistra.", "T1 richiesta"),
    (9.0, "Sì, confermo.", "T1 conferma"),
    (16.0, "Potresti indicarmi la zona centrale?", "T2 richiesta"),
    (9.0, "Sì.", "T2 conferma"),
    (14.0, "Spostalo dall'altra parte.", "T3 richiesta ambigua"),
    (10.0, "Appoggialo al centro.", "T3 chiarimento"),
    (9.0, "Sì, procedi.", "T3 conferma"),
    (16.0, "Disegna un cerchio.", "T4 fuori ambito"),
    (9.0, "Torna a casa.", "T5 movimento lungo"),
    (9.0, "Sì.", "T5 conferma"),
    (3.0, "Fermati immediatamente!", "T5 STOP durante il movimento"),
    (8.0, "Vai nella posizione di attesa.", "T5 dopo STOP: deve essere rifiutato"),
    (12.0, None, "fine"),
]


def build_wav(path: Path) -> list[tuple[float, str]]:
    tmp = Path(tempfile.mkdtemp())
    out, marks = bytearray(), []
    for i, (gap, text, label) in enumerate(TIMELINE):
        out += b"\0\0" * int(gap * RATE)
        if text:
            subprocess.run(["say", "-v", "Alice", text, "-o", str(tmp / f"{i}.aiff")], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", f"LEI16@{RATE}", "-c", "1", str(tmp / f"{i}.aiff"),
                            str(tmp / f"{i}.wav")], check=True)
            marks.append((len(out) / 2 / RATE, label))
            out += wave.open(str(tmp / f"{i}.wav")).readframes(10**9)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(RATE); f.writeframes(bytes(out))
    return marks + [(len(out) / 2 / RATE, "end")]


def status() -> dict:
    return json.loads(urllib.request.urlopen(URL + "/api/status").read())


def reset() -> None:
    req = urllib.request.Request(URL + "/api/reset", data=b'{"operator_confirmed": true}',
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req).read()


async def main(out: Path) -> None:
    from playwright.async_api import async_playwright
    out.mkdir(parents=True, exist_ok=True)
    wav = out / "jury_mic.wav"
    marks = build_wav(wav)
    total = marks[-1][0]
    reset()
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                          f"--use-file-for-fake-audio-capture={wav}%noloop",
                                          "--autoplay-policy=no-user-gesture-required"])
        ctx = await b.new_context(viewport={"width": 1400, "height": 1000}, permissions=["microphone"],
                                  record_video_dir=str(out), record_video_size={"width": 1400, "height": 1000})
        page = await ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto(URL)
        await page.wait_for_timeout(1500)
        await page.get_by_text("AVVIA ASSISTENTE VOCALE").click()
        t0 = time.time()
        timeline, shot_at = [], {round(t + 4): lbl for t, lbl in marks}
        while time.time() - t0 < total + 2:
            el = round(time.time() - t0)
            if el in shot_at:
                await page.screenshot(path=str(out / f"{el:03d}_{shot_at.pop(el).replace(' ', '_')}.png"))
            g = status()["gate"]
            if not timeline or timeline[-1][1] != g:
                timeline.append((round(time.time() - t0, 1), g))
            await asyncio.sleep(0.25)
        st = status()
        video = await page.video.path() if page.video else None
        await ctx.close()
        await b.close()
    print("speech marks:", [(round(t, 1), l) for t, l in marks])
    print("gate timeline:", timeline)
    for h in reversed(st["history"][:40]):
        if h["kind"] != "utterance":
            print(f"  {h['kind']:18} {json.dumps({k: v for k, v in h.items() if k not in ('ts', 'kind')}, ensure_ascii=False)[:150]}")
    print("final gate:", st["gate"], "| page errors:", errors[:3], "| video:", video)


# Ten jury beats. Spoken rehearsal above stays Italian (macOS `say`). `--text` posts the
# same intents through TEXT MODE so a laptop without a microphone can walk the gate.
TEXT_SCRIPT = [
    ("it", "Solleva il vassoio a sinistra.", "propose"),
    ("it", "Sì, confermo.", "confirm"),
    ("en", "Bring me the red cup.", "no camera: no motion"),
    ("zh", "把它放到那边", "clarify, no motion"),
    ("ja", "シャンプーはどこ？", "locate, no motion"),
    ("it", "Disegna un cerchio.", "refuse"),
    ("it", "Prendi il vassoio sullo scaffale.", "refuse"),
    ("en", "Learn this product.", "approval stays in the UI"),
    ("am", "በግራ በኩል ያለውን ትሪ አንሳ", "experimental"),
    ("am", "አዎ", "confirm only if a proposal is pending"),
]


def rehearse_text() -> None:
    import urllib.error
    try:
        status()
    except Exception as e:
        print(f"TEXT rehearsal NOT TESTED: {URL} is not up ({e}).")
        return
    reset()
    for lang, text, note in TEXT_SCRIPT:
        req = urllib.request.Request(URL + "/api/text-command",
                                     data=json.dumps({"text": text, "engine": "rules", "lang": lang}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            body = json.loads(urllib.request.urlopen(req).read())
        except urllib.error.HTTPError as e:
            print(f"  {lang} FAIL {text} {e}")
            continue
        print(f"  {lang:3} {note:28} gate={body.get('gate')} esito={(body.get('result') or {}).get('esito')} «{text}»")
    print("final gate:", status()["gate"])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="recordings")
    ap.add_argument("--text", action="store_true", help="walk the jury script through TEXT MODE; no microphone")
    args = ap.parse_args()
    if args.text:
        rehearse_text()
    else:
        asyncio.run(main(Path(args.out)))
