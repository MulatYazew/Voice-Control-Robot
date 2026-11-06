import { useEffect, useState } from "react";
import { useT } from "../i18n/vision";
import { partReady, startInspection } from "../services/api";
import type { Config, Status } from "../services/types";

export function InspectionPanel({ status, config }: { status: Status | null; config: Config | null }) {
  const { lang, t } = useT();
  const insp = status?.inspection;
  const cfg = config?.inspection;
  const [rule, setRule] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (insp?.rule && !rule) setRule(insp.rule); }, [insp?.rule]); // eslint-disable-line

  if (!status || !cfg || !insp) return null;
  const label = (z: string) => cfg.labels[z] ?? z;
  const gateBusy = ["EXECUTING", "STOPPED", "FAULT"].includes(status.gate) || insp.running;
  const holding = status.world.oggetti_presunti.vassoio_sulla_forca;
  const inputHasPart = status.world.oggetti_presunti.vassoi_in_zona.includes(cfg.input_zone);
  const last = insp.last;

  const run = async (fn: () => Promise<any>) => {
    setBusy(true); setMsg("");
    try {
      const r = await fn();
      if (!r.ok) setMsg(r.messaggio ?? r.esito ?? "");
    } catch (e: any) { setMsg(e.message); }
    finally { setBusy(false); }
  };

  return (
    <section className={`card inspection ${insp.running ? "running" : ""}`}>
      <div className="card-head">
        <h2>{t("inspection")} <span className="sub">{config?.inspector === "grok" ? t("inspector_grok") : t("inspector_mock")}</span></h2>
      </div>
      <div className="flow">
        {t("flow", { a: label(cfg.input_zone), b: label(cfg.pass_zone), c: label(cfg.fail_zone) })}
      </div>
      <form className="rule-form" onSubmit={(e) => { e.preventDefault(); run(() => startInspection(rule.trim())); }}>
        <label htmlFor="rule">{t("rule")}</label>
        <input id="rule" value={rule} onChange={(e) => setRule(e.target.value)} placeholder={t("rule_ph")} maxLength={200} />
        <div className="rule-actions">
          <button type="submit" className="primary" disabled={busy || gateBusy || (!rule.trim() && !insp.rule)}>
            {t("start_inspection")}
          </button>
          <button type="button" disabled={busy || gateBusy || status.gate !== "IDLE" || holding || inputHasPart}
            onClick={() => run(partReady)}>{t("part_ready", { tray: label(cfg.input_zone) })}</button>
        </div>
      </form>
      {msg && <div className="hint bad">{msg}</div>}
      {insp.running && <div className="analysing"><span className="spinner" aria-hidden /> {t("analysing")}</div>}
      <h3>{t("last_inspection")}</h3>
      {last ? (
        <div className="verdict">
          {insp.has_image && <img src={`/api/inspection/last.jpg?f=${last.frame_id ?? last.at}`} alt={t("last_inspection")} />}
          <div>
            <div className={`verdict-badge v-${last.status}`}>{t(`status_${last.status}` as any)}</div>
            <p className="verdict-reason">{last.reason}</p>
            <div className="hint">«{last.rule}» · {last.source}{last.latency_ms ? ` · ${Math.round(last.latency_ms)} ms` : ""}
              {" · "}{new Date(last.at * 1000).toLocaleTimeString(lang)}</div>
          </div>
        </div>
      ) : <div className="empty">{t("no_inspection")}</div>}
      <div className="counts">
        <span className="v-PASS">{t("status_PASS")} {insp.counts.PASS ?? 0}</span>
        <span className="v-FAIL">{t("status_FAIL")} {insp.counts.FAIL ?? 0}</span>
        <span className="v-UNSURE">{t("status_UNSURE")} {insp.counts.UNSURE ?? 0}</span>
      </div>
    </section>
  );
}
