import type { Config, RobotStatus, UiState, VoiceStatus } from "../services/types";

const STATE_LABEL: Record<UiState, string> = {
  DISCONNECTED: "DISCONNESSO", READY: "PRONTO", LISTENING: "IN ASCOLTO", PROCESSING: "ELABORAZIONE",
  CLARIFICATION: "CHIARIMENTO", AWAITING_CONFIRMATION: "ATTESA CONFERMA", EXECUTING: "IN ESECUZIONE",
  COMPLETED: "COMPLETATO", STOPPED: "FERMO", FAULT: "GUASTO", RECONNECTING: "RICONNESSIONE",
};

export function Header({ ui, config, robot, voice }: { ui: UiState; config: Config | null; robot?: RobotStatus;
  voice: VoiceStatus }) {
  const mode = config?.mode;
  const grok = !config?.grok_configured ? ["non configurato", "off"]
    : voice === "connected" ? ["connesso", "ok"]
    : voice === "reconnecting" || voice === "connecting" ? ["riconnessione…", "warn"]
    : voice === "error" ? ["errore", "bad"] : ["inattivo", "off"];
  const rob = !robot ? ["—", "off"] : !robot.connected ? ["non connesso", "bad"]
    : robot.safety && !["NORMAL", "REDUCED"].includes(robot.safety) ? [robot.safety, "bad"]
    : [robot.moving ? "in movimento" : `${robot.robot_mode ?? "connesso"}`, robot.moving ? "warn" : "ok"];
  return (
    <header className="header">
      <div className="brand">
        <div className="logo">VOCE</div>
        <div className="tagline">Parla naturalmente. Il robot capisce. Tu confermi.</div>
      </div>
      <div className="badges">
        {mode === "real"
          ? <span className="badge real">ROBOT REALE</span>
          : <span className="badge mode">{mode === "ursim" ? "SIMULAZIONE · URSim" : "SIMULAZIONE · interna"}</span>}
        <span className={`badge dot ${grok[1]}`}>Grok Voice: {grok[0]}</span>
        <span className={`badge dot ${rob[1]}`}>Robot: {rob[0]}</span>
        <span className={`state state-${ui}`}>{STATE_LABEL[ui]}</span>
      </div>
    </header>
  );
}
