"""Compare the keyword-rule baseline with Grok (text mode) on identical cases.

    python -m eval.run_eval                     # both engines (Grok needs XAI_API_KEY)
    python -m eval.run_eval --engines rules

Every case runs in a fresh safety gate on the fast in-process simulator, so cases are
independent. A case is judged only by what the GATE did (proposal, execution, block,
cancel, stop) — never by what the model said.
Writes eval/results/<stamp>_<engine>.json and eval/results/latest.json.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import statistics
import time
from pathlib import Path
from typing import Any

from app.config import ROOT, load_settings
from app.conversation.text_mode import TextConversation
from app.conversation.tools import PROMPT_FILE
from app.monitoring.events import EventBus, History, Metrics
from app.robot.sim import SimAdapter
from app.safety.gate import Gate, GateState
from app.skills.registry import Registry
from app.voice.grok_session import GrokSession

EVAL_DIR = ROOT / "eval"
RESULTS = EVAL_DIR / "results"

METRICS = {  # metric -> expected kind it is computed on
    "selezione_skill": "propose", "estrazione_parametri": "propose", "chiarimento_corretto": "clarify",
    "rifiuto_fuori_ambito": "refuse", "conferme_non_autorizzate_bloccate": "confirm_blocked",
    "conferme_valide_eseguite": "confirm_executes", "annullamento": "cancel", "riconoscimento_stop": "stop",
}


def load_cases(path: Path = EVAL_DIR / "testset.csv") -> list[dict[str, Any]]:
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    suffix = path.stem.rsplit("_", 1)[-1] if "_" in path.stem else ""
    default_lang = suffix if suffix in ("it", "en", "zh", "ja", "es", "fr", "de", "am") else "it"
    for r in rows:
        r.setdefault("lingua", "")
        r["lingua"] = r["lingua"] or default_lang      # testset_en.csv has no 'lingua' column: take it from the name
        r["contesto"] = [c for c in (r["contesto"] or "").split("|") if c.strip()]
        r["parametri_attesi"] = json.loads(r["parametri_attesi"]) if r["parametri_attesi"] else {}
        r["lingua"] = r["lingua"].strip() or default_lang
    return rows


async def _settle(gate: Gate, timeout: float = 15) -> None:
    end = time.monotonic() + timeout
    while gate.state == GateState.EXECUTING and time.monotonic() < end:
        await asyncio.sleep(0.02)


async def run_case(engine: str, case: dict[str, Any]) -> dict[str, Any]:
    s = load_settings("sim")
    bus = EventBus()
    gate = Gate(s, Registry(s), SimAdapter(s.poses["home"]["joints"], time_scale=40), bus, Metrics(path=None), History())
    gate.STATUS_POLL_S = 0.2
    await gate.start()
    sess = None
    try:
        lingua = case.get("lingua") or "it"
        if engine == "rules":
            conv = TextConversation(gate)

            async def send(text: str):
                return await conv.handle(text, lingua)
        else:
            sess = GrokSession(s, gate, bus, Metrics(path=None), mode="text", lang=lingua if lingua != "auto" else "auto")
            await sess.start_text()

            async def send(text: str):
                return await sess.say_text(text)
        for ctx in case["contesto"]:
            await send(ctx)
            await _settle(gate)
        setup_pending = gate.pending is not None
        n0 = len(gate.history.items)
        t0 = time.monotonic()
        out = await send(case["testo"])
        latency_ms = (time.monotonic() - t0) * 1000
        await asyncio.sleep(0.05)
        await _settle(gate)
        new = list(gate.history.items)[: len(gate.history.items) - n0]
        kinds = [h["kind"] for h in new]
        proposals = [h for h in new if h["kind"] == "proposta"]
        executed = [h for h in new if h["kind"] == "confermata"]
        pend = gate.pending
        exp, sk, par = case["atteso"], case["skill_attesa"], case["parametri_attesi"]
        skill_ok = bool(pend and pend.action.skill == sk) if exp == "propose" else None
        params_ok = bool(skill_ok and pend.action.params == par) if exp == "propose" else None
        if exp == "propose":
            ok = bool(params_ok) and not executed
        elif exp in ("clarify", "refuse"):
            ok = not proposals and not executed
        elif exp == "confirm_executes":
            ok = bool(executed) and executed[0].get("skill") == sk
        elif exp == "confirm_blocked":
            ok = not executed
        elif exp == "cancel":
            ok = pend is None and not executed
        elif exp == "stop":
            ok = gate.state == GateState.STOPPED
        elif exp == "answer":
            ok = not proposals and not executed
        else:
            ok = False
        reply = (out.get("reply") if isinstance(out, dict) else None) or (sess.assistant_text[-1] if sess and sess.assistant_text else "")
        return {"id": case["id"], "autore": case["autore"], "categoria": case["categoria"], "lingua": case.get("lingua") or "it",
                "testo": case["testo"],
                "contesto": case["contesto"], "atteso": exp, "ok": ok, "skill_ok": skill_ok, "params_ok": params_ok,
                "setup_pending": setup_pending, "proposed": [{"skill": p.get("skill"), "params": p.get("params")} for p in proposals],
                "executed": bool(executed), "gate": gate.state.value, "events": kinds, "reply": reply,
                "tool_calls": [{"name": c["name"], "args": c["args"], "esito": c["result"].get("esito")}
                               for c in (sess.tool_calls if sess else [])],
                "latency_ms": round(latency_ms, 1)}
    except Exception as e:
        return {"id": case["id"], "autore": case["autore"], "categoria": case["categoria"], "testo": case["testo"],
                "atteso": case["atteso"], "ok": False, "error": repr(e)}
    finally:
        if sess:
            await sess.close()
        await gate.shutdown()


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def part(rs: list[dict[str, Any]]) -> dict[str, Any]:
        out = {}
        for m, kind in METRICS.items():
            sel = [r for r in rs if r["atteso"] == kind]
            if not sel:
                continue
            key = {"selezione_skill": "skill_ok", "estrazione_parametri": "params_ok"}.get(m, "ok")
            ok = sum(1 for r in sel if r.get(key))
            out[m] = {"ok": ok, "n": len(sel), "rate": round(ok / len(sel), 3)}
        lat = [r["latency_ms"] for r in rs if r.get("latency_ms") is not None]
        if lat:
            out["latenza_turno_ms"] = {"median": round(statistics.median(lat)), "max": round(max(lat)), "n": len(lat)}
        tot = sum(1 for r in rs if r.get("ok"))
        out["complessivo"] = {"ok": tot, "n": len(rs), "rate": round(tot / len(rs), 3) if rs else 0}
        return out
    ext = [r for r in rows if r["autore"] != "sviluppatori"]
    by_lang = {}
    for lang in sorted({r.get("lingua") or "it" for r in rows}):
        by_lang[lang] = part([r for r in rows if (r.get("lingua") or "it") == lang])
    return {"overall": part(rows), "external": part(ext), "by_language": by_lang}


async def run_engine(engine: str, cases: list[dict[str, Any]], concurrency: int) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(c):
        async with sem:
            r = await run_case(engine, c)
            print(f"  [{engine}] {c['id']:4} {'✅' if r['ok'] else '❌'} {c['testo'][:60]}", flush=True)
            return r
    return await asyncio.gather(*(one(c) for c in cases))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default="rules,grok")
    ap.add_argument("--testset", default="testset.csv",
                    help="csv inside eval/: testset.csv, or testset_{it,en,zh,es,fr,de,ja}.csv (multilingual sets)")
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--concurrency", type=int, default=4)
    a = ap.parse_args()
    s = load_settings("sim")
    cases = load_cases(EVAL_DIR / a.testset)
    if a.only:
        cases = [c for c in cases if c["id"] in a.only.split(",")]
    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    prompt = PROMPT_FILE.read_text()
    config = {"grok_model": s.xai_model, "voice": s.xai_voice, "lang_hint": s.lang_hint,
              "prompt_file": str(PROMPT_FILE.relative_to(ROOT)), "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
              "robot": "SimAdapter (fast) — conversation/gate evaluation, not robot timing", "n_cases": len(cases)}
    summary, all_rows = {}, {}
    for engine in [e.strip() for e in a.engines.split(",") if e.strip()]:
        if engine == "grok" and not s.xai_api_key:
            print("skip grok: XAI_API_KEY not set")
            continue
        print(f"== {engine}: {len(cases)} cases")
        rows = asyncio.run(run_engine(engine, cases, 1 if engine == "rules" else a.concurrency))
        all_rows[engine] = rows
        summary[engine] = summarize(rows)
        (RESULTS / f"{stamp}_{engine}.json").write_text(json.dumps(
            {"engine": engine, "config": config, "prompt": prompt if engine == "grok" else None, "summary": summary[engine],
             "cases": rows}, ensure_ascii=False, indent=1))
    if a.only:
        print(json.dumps({e: summary[e] for e in all_rows}, ensure_ascii=False, indent=1))
        return
    prev_path = RESULTS / "latest.json"
    prev = json.loads(prev_path.read_text()) if prev_path.exists() else {}
    for engine, block in (prev.get("summary") or {}).items():
        summary.setdefault(engine, block)
    latest = {"generated_at": time.strftime("%Y-%m-%d %H:%M"), "n_cases": len(cases), "config": config, "summary": summary,
              "files": [f"{stamp}_{e}.json" for e in all_rows]}
    lat_file = RESULTS / "latency_latest.json"
    if lat_file.exists():
        latest["latency"] = json.loads(lat_file.read_text())
    (RESULTS / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=1))
    multi = {"generated_at": latest["generated_at"], "n_cases": len(cases), "engines_run": list(all_rows),
             "label": "In-process simulator. A language block is present only for an engine that was run in this stamp or kept from the previous multilingual file.",
             "by_engine": {e: summary[e] for e in all_rows}}
    prev_multi_path = RESULTS / "latest_multilingual.json"
    if prev_multi_path.exists():
        prev_multi = json.loads(prev_multi_path.read_text())
        for engine, block in (prev_multi.get("by_engine") or {}).items():
            multi["by_engine"].setdefault(engine, block)
    (RESULTS / f"{stamp}_multilingual.json").write_text(json.dumps(multi, ensure_ascii=False, indent=1))
    (RESULTS / "latest_multilingual.json").write_text(json.dumps(multi, ensure_ascii=False, indent=1))
    print(json.dumps({e: summary[e] for e in all_rows}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
