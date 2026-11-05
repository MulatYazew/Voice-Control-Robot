import { useState } from "react";
import { grabCup, partReady, placeCup, pointAtCup, scanCups, startInspection } from "../services/api";

const COLOURS = ["red", "orange", "yellow", "green", "blue", "purple"];

/** Cup gripper, pointing and quality inspection from the grokarm cell. Every motion still waits for yes. */
export function CellPanel() {
  const [colour, setColour] = useState("blue");
  const [rule, setRule] = useState("Reject items with scratches");
  const [msg, setMsg] = useState("");
  const [cups, setCups] = useState<string>("");

  const run = async (fn: () => Promise<any>) => {
    setMsg("");
    try {
      const r = await fn();
      if (r?.ok) setMsg(r.frase_conferma || r.messaggio || "In attesa di conferma.");
      else setMsg(r?.messaggio || r?.esito || "Rifiutato");
    } catch (e: any) {
      setMsg(e.message || "Errore");
    }
  };

  return (
    <section className="card">
      <h2>Cella <span className="sub">pinza · bicchieri · ispezione</span></h2>
      <p className="hint">Ogni movimento resta in attesa di un sì. Il pulsante rosso ferma il braccio.</p>
      <label>
        Colore
        <select aria-label="colore del bicchiere" value={colour} onChange={(e) => setColour(e.target.value)}>
          {COLOURS.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
      </label>
      <div className="row" style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
        <button type="button" onClick={() => run(async () => {
          const r = await scanCups();
          setCups(r.ok ? (r.cups || []).map((c: { colore: string; zona: string }) => `${c.colore} ${c.zona}`).join(", ") : (r.messaggio || ""));
          return r;
        })}>Vedi bicchieri</button>
        <button type="button" onClick={() => run(() => pointAtCup(colour))}>Indica</button>
        <button type="button" onClick={() => run(() => grabCup(colour))}>Prendi</button>
        <button type="button" onClick={() => run(() => placeCup())}>Posa</button>
      </div>
      {cups && <p className="mono">{cups}</p>}
      <form onSubmit={(e) => { e.preventDefault(); run(() => startInspection(rule.trim())); }}>
        <label htmlFor="insp-rule">Regola di ispezione</label>
        <input id="insp-rule" value={rule} onChange={(e) => setRule(e.target.value)} />
        <div className="row" style={{ display: "flex", gap: 8, marginTop: 8 }}>
          <button type="submit">Avvia ispezione</button>
          <button type="button" onClick={() => run(partReady)}>Pezzo pronto</button>
        </div>
      </form>
      {msg && <p className="hint">{msg}</p>}
    </section>
  );
}
