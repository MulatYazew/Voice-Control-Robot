import type { VoiceState } from "../services/voice";

export function MicPanel({ state, detail, level, onStart, onPause, onStop, available }: {
  state: VoiceState; detail: string; level: number; available: boolean;
  onStart: () => void; onPause: (p: boolean) => void; onStop: () => void;
}) {
  const live = state === "live" || state === "paused" || state === "reconnecting";
  const pct = Math.min(100, Math.round(Math.sqrt(level) * 220));
  return (
    <section className="card mic">
      {!live ? (
        <button className="mic-start" onClick={onStart} disabled={!available || state === "starting"}>
          <span className="mic-icon" aria-hidden>●</span>
          {state === "starting" ? "AVVIO…" : "AVVIA ASSISTENTE VOCALE"}
        </button>
      ) : (
        <div className="mic-live">
          <div className={`mic-orb ${state}`} style={{ ["--lvl" as any]: `${pct}%` }} aria-hidden />
          <div className="mic-info">
            <div className="mic-title">
              {state === "paused" ? "Microfono in pausa" : state === "reconnecting" ? "Riconnessione a Grok…" : "Microfono attivo · parla in italiano"}
            </div>
            <div className="meter"><div className="meter-fill" style={{ width: `${state === "live" ? pct : 0}%` }} /></div>
            <div className="mic-actions">
              <button onClick={() => onPause(state !== "paused")}>{state === "paused" ? "Riprendi microfono" : "Pausa microfono"}</button>
              <button onClick={onStop}>Chiudi sessione vocale</button>
            </div>
          </div>
        </div>
      )}
      {!available && <p className="hint">Grok non configurato: disponibile solo la modalità testo.</p>}
      {state === "error" && <p className="hint bad">{detail || "Errore del microfono"}</p>}
    </section>
  );
}
