import { useT } from "../i18n/strings";
import type { VoiceState } from "../services/voice";

export function MicPanel({ state, detail, level, onStart, onPause, onStop, available, wake }: {
  state: VoiceState; detail: string; level: number; available: boolean; wake?: "on" | "not supported" | "";
  onStart: () => void; onPause: (p: boolean) => void; onStop: () => void;
}) {
  const t = useT();
  const live = state === "live" || state === "paused" || state === "reconnecting";
  const pct = Math.min(100, Math.round(Math.sqrt(level) * 220));
  return (
    <section className="card mic">
      {!live ? (
        <button className="mic-start" onClick={onStart} disabled={!available || state === "starting"}>
          <span className="mic-icon" aria-hidden>●</span>
          {state === "starting" ? t("starting") : t("start_voice")}
        </button>
      ) : (
        <div className="mic-live">
          <div className={`mic-orb ${state}`} style={{ ["--lvl" as any]: `${pct}%` }} aria-hidden />
          <div className="mic-info">
            <div className="mic-title">
              {state === "paused" ? t("mic_paused") : state === "reconnecting" ? t("mic_reconnect") : t("mic_live")}
            </div>
            <div className="meter"><div className="meter-fill" style={{ width: `${state === "live" ? pct : 0}%` }} /></div>
            <div className="mic-actions">
              <button onClick={() => onPause(state !== "paused")}>{state === "paused" ? t("resume_mic") : t("pause_mic")}</button>
              <button onClick={onStop}>{t("close_voice")}</button>
            </div>
          </div>
        </div>
      )}
      {!available && <p className="hint">{t("grok_missing")}</p>}
      {state === "reconnecting" && detail && <p className="hint">{detail}</p>}
      {wake === "on" && <p className="hint">{t("wake_on")}</p>}
      {wake === "not supported" && <p className="hint">{t("wake_off")}</p>}
      {state === "error" && <p className="hint bad">{detail || "Errore del microfono"}</p>}
    </section>
  );
}
