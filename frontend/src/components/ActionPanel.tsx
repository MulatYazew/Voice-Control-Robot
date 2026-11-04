import { useEffect, useState } from "react";
import type { Pending, Status } from "../services/types";

const OUTCOME_IT: Record<string, string> = {
  completed: "completato", stopped: "interrotto da STOP", not_started: "non eseguito", failed: "fallito",
  unknown: "esito sconosciuto",
};

const SKILL_IT: Record<string, string> = {
  vai_a_posa: "Vai a posa", indica: "Indica", solleva_vassoio: "Solleva vassoio", appoggia_vassoio: "Appoggia vassoio",
  spingi: "Spingi",
};

function Countdown({ p }: { p: Pending }) {
  const [left, setLeft] = useState(p.expires_in_s);
  useEffect(() => {
    const end = Date.now() + p.expires_in_s * 1000;
    setLeft(p.expires_in_s);
    const t = window.setInterval(() => setLeft(Math.max(0, (end - Date.now()) / 1000)), 200);
    return () => window.clearInterval(t);
  }, [p.id, p.expires_in_s]);
  return (
    <div className="countdown">
      <div className="countdown-bar" style={{ width: `${(left / p.ttl_s) * 100}%` }} />
      <span>{left.toFixed(0)} s per confermare</span>
    </div>
  );
}

export function ActionPanel({ status, progress, execStart }: {
  status: Status | null; progress: { done: number; total: number } | null; execStart: number | null;
}) {
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((x) => x + 1), 250); return () => window.clearInterval(t); }, []);
  if (!status) return null;
  const p = status.pending;
  const executing = status.gate === "EXECUTING";
  const last = status.last_result;
  return (
    <section className={`card action ${p ? "pending" : ""} ${executing ? "executing" : ""}`}>
      <h2>Azione</h2>
      {p ? (
        <>
          <div className="action-title">{SKILL_IT[p.skill] ?? p.skill}{p.steps > 1 && <span className="step"> · passo {p.step}/{p.steps}</span>}</div>
          <div className="params">
            {Object.entries(p.parametri).map(([k, v]) => <span key={k} className="param"><b>{k}</b> {v.replace("_", " ")}</span>)}
            {p.strumento && <span className="param"><b>strumento</b> {p.strumento}</span>}
          </div>
          <div className="confirm-q">«{p.conferma}»</div>
          <div className="hint">Rispondi a voce: «Sì, confermo» oppure «No».{!p.question_asked && " (in attesa della domanda)"}</div>
          <Countdown p={p} />
        </>
      ) : executing ? (
        <>
          <div className="action-title">In esecuzione: {SKILL_IT[status.world.robot_verificato.skill_attiva ?? ""] ?? status.world.robot_verificato.skill_attiva}</div>
          <div className="progress"><div className="progress-fill" style={{ width: progress ? `${(progress.done / progress.total) * 100}%` : "8%" }} /></div>
          <div className="hint">
            {progress ? `Movimento ${progress.done}/${progress.total} completato` : "Movimento avviato"}
            {execStart && ` · ${((Date.now() - execStart) / 1000).toFixed(1)} s`} · di' «stop» per fermare
          </div>
        </>
      ) : last ? (
        <>
          <div className={`action-title ${last.success ? "ok" : "bad"}`}>
            Ultimo risultato: {SKILL_IT[last.skill] ?? last.skill} — {OUTCOME_IT[last.outcome] ?? last.outcome}
          </div>
          <div className="hint">
            {Object.entries(last.parameters ?? {}).map(([k, v]) => `${k}: ${v}`).join(" · ")} · {Number(last.duration).toFixed(1)} s
            · movimenti {last.steps_completed}/{last.steps_total}{last.error ? ` · ${last.error}` : ""}
          </div>
        </>
      ) : (
        <div className="empty">Nessuna azione in attesa.</div>
      )}
    </section>
  );
}
