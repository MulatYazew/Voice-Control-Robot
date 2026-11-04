import { useEffect, useState } from "react";
import { fetchEval, resetSystem, stopRobot } from "../services/api";
import type { HistoryItem, MetricSummary, Status } from "../services/types";

const KIND: Record<string, string> = {
  proposta: "Proposta", confermata: "Confermata", completata: "Completata", annullata: "Annullata", scaduta: "Scaduta",
  stop: "STOP", interrotta: "Interrotta", guasto: "Guasto", ripristino: "Ripristino", conferma_rifiutata: "Conferma rifiutata",
  non_eseguita: "Non eseguita", posa_salvata: "Posa salvata",
};

export function History({ items }: { items: HistoryItem[] }) {
  const shown = items.filter((h) => h.kind !== "utterance").slice(0, 14);
  return (
    <section className="card history">
      <h2>Storico azioni</h2>
      {shown.length === 0 ? <div className="empty">Ancora nessuna azione.</div> : (
        <ul>
          {shown.map((h, i) => (
            <li key={i} className={`h-${h.kind}`}>
              <time>{new Date(h.ts * 1000).toLocaleTimeString("it-IT")}</time>
              <b>{KIND[h.kind] ?? h.kind}</b>
              <span>
                {[h.skill as string, h.params ? Object.values(h.params as object).join(" ") : "", h.source as string,
                  h.text ? `«${h.text}»` : "", h.reason as string, h.ack_ms ? `${h.ack_ms} ms` : ""].filter(Boolean).join(" · ")}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

const METRIC_IT: Record<string, string> = {
  intent_processing_ms: "Elaborazione intento (fine voce → tool)",
  confirmation_response_ms: "Risposta di conferma",
  robot_execution_ms: "Esecuzione robot",
  request_to_completion_ms: "Conferma → completamento",
  "stop_ack_ms[ui]": "STOP pulsante/spazio → ack robot",
  "stop_halt_ms[ui]": "STOP pulsante → robot fermo",
  "stop_ack_ms[transcript]": "STOP da trascrizione → ack robot",
  "stop_halt_ms[transcript]": "STOP da trascrizione → robot fermo",
  "stop_ack_ms[grok_tool]": "STOP tool Grok → ack robot",
  transcript_stop_detect_ms: "Fine voce → «stop» riconosciuto",
  grok_stop_tool_ms: "Fine voce → tool stop di Grok",
};

export function Metrics({ metrics }: { metrics: Record<string, MetricSummary> }) {
  const rows = Object.entries(metrics);
  return (
    <section className="card metrics">
      <h2>Latenze <span className="sub">questa sessione</span></h2>
      {rows.length === 0 ? <div className="empty">Nessuna misura ancora.</div> : (
        <table>
          <thead><tr><th>Misura</th><th>n</th><th>mediana</th><th>max</th></tr></thead>
          <tbody>
            {rows.map(([k, m]) => (
              <tr key={k}><td>{METRIC_IT[k] ?? k}</td><td>{m.n}</td><td>{m.median_ms} ms</td><td>{m.max_ms} ms</td></tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

export function StopBar({ status, onStopped }: { status: Status | null; onStopped: () => void }) {
  const [msg, setMsg] = useState("");
  const needsReset = status?.gate === "STOPPED" || status?.gate === "FAULT";
  const doStop = async (src: "button" | "space") => {
    onStopped();
    try { await stopRobot(src); } catch (e: any) { setMsg(`STOP non inviato: ${e.message}`); }
  };
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (e.code === "Space" && tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA") { e.preventDefault(); doStop("space"); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  });
  const reset = async () => {
    if (!window.confirm("Ripristinare il sistema? Verifica che l'area di lavoro sia sicura.")) return;
    const r = await resetSystem();
    setMsg(r.ok ? "" : r.messaggio ?? "Ripristino rifiutato");
  };
  return (
    <div className="stopbar">
      {needsReset && (
        <button className="reset" onClick={reset}>RIPRISTINA</button>
      )}
      <button className="stop" onClick={() => doStop("button")} title="Barra spaziatrice">
        STOP <small>spazio</small>
      </button>
      {msg && <div className="stop-msg">{msg}</div>}
      <div className="disclaimer">STOP software: non è un arresto di emergenza certificato. Usare il pulsante di emergenza fisico del robot.</div>
    </div>
  );
}

export function EvalPanel() {
  const [data, setData] = useState<any>(null);
  useEffect(() => { fetchEval().then(setData).catch(() => setData({ available: false })); }, []);
  if (!data) return <section className="card"><div className="empty">Caricamento…</div></section>;
  if (!data.available) return <section className="card"><h2>Valutazione</h2><div className="empty">Nessun risultato: esegui <code>python -m eval.run_eval</code>.</div></section>;
  const engines = Object.keys(data.summary ?? {});
  const metrics = engines.length ? Object.keys(data.summary[engines[0]].overall) : [];
  return (
    <section className="card eval">
      <h2>Valutazione <span className="sub">{data.generated_at} · {data.n_cases} casi</span></h2>
      {["overall", "external"].map((part) => (
        <div key={part}>
          <h3>{part === "overall" ? "Tutti i casi" : "Comandi scritti da persone esterne al team"}</h3>
          <table>
            <thead><tr><th>Metrica</th>{engines.map((e) => <th key={e}>{e}</th>)}</tr></thead>
            <tbody>
              {metrics.map((m) => (
                <tr key={m}><td>{m}</td>{engines.map((e) => {
                  const v = data.summary[e][part]?.[m];
                  return <td key={e}>{v ? `${(v.rate * 100).toFixed(0)}% (${v.ok}/${v.n})` : "—"}</td>;
                })}</tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
      {data.latency && (
        <>
          <h3>Latenze STOP <span className="sub">{data.latency.environment}</span></h3>
          <table>
            <thead><tr><th>Percorso</th><th>n</th><th>mediana</th><th>max</th></tr></thead>
            <tbody>
              {Object.entries(data.latency.stop as Record<string, any>).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td>{v.n}</td><td>{v.median_ms} ms</td><td>{v.max_ms} ms</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
      <p className="hint">Modello: {data.config?.grok_model} · prompt sha256 {String(data.config?.prompt_sha256 ?? "").slice(0, 12)}</p>
    </section>
  );
}
