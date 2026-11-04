import { useEffect, useRef, useState } from "react";
import { ActionPanel } from "./components/ActionPanel";
import { Conversation } from "./components/Conversation";
import { Header } from "./components/Header";
import { MicPanel } from "./components/MicPanel";
import { EvalPanel, History, Metrics, StopBar } from "./components/Panels";
import { RobotPanel } from "./components/RobotPanel";
import { deriveUiState, useVoce } from "./hooks/useVoce";
import { VoiceClient, type VoiceState } from "./services/voice";

export default function App() {
  const { store, setTextMode } = useVoce();
  const [voiceState, setVoiceState] = useState<VoiceState>("off");
  const [voiceDetail, setVoiceDetail] = useState("");
  const [level, setLevel] = useState(0);
  const [tab, setTab] = useState<"control" | "eval">("control");
  const voice = useRef<VoiceClient | null>(null);
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((x) => x + 1), 1000); return () => window.clearInterval(t); }, []);

  const startVoice = async () => {
    if (!voice.current) {
      voice.current = new VoiceClient({
        onState: (s, d) => { setVoiceState(s); setVoiceDetail(d ?? ""); },
        onLevel: (l) => setLevel(l),
      });
    }
    await voice.current.start();
  };

  const ui = deriveUiState(store, voiceState);
  const s = store.status;
  return (
    <div className={`app ui-${ui}`}>
      <Header ui={ui} config={store.config} robot={s?.robot} voice={voiceState === "off" ? "off" : store.voice} />
      {store.textMode && <div className="global-textmode">TEXT MODE — NOT VOICE</div>}
      <nav className="tabs">
        <button className={tab === "control" ? "on" : ""} onClick={() => setTab("control")}>Controllo</button>
        <button className={tab === "eval" ? "on" : ""} onClick={() => setTab("eval")}>Valutazione</button>
      </nav>
      {tab === "control" ? (
        <main className="grid">
          <div className="col">
            <MicPanel state={voiceState} detail={voiceDetail} level={level} available={!!store.config?.grok_configured}
              onStart={startVoice} onPause={(p) => voice.current?.setPaused(p)} onStop={() => voice.current?.stop()} />
            <ActionPanel status={s} progress={store.progress} execStart={store.execStart} />
            <Conversation chat={store.chat} textMode={store.textMode} setTextMode={setTextMode}
              grokAvailable={!!store.config?.grok_configured} />
          </div>
          <div className="col">
            <RobotPanel status={s} layout={store.layout} joints={store.joints} />
            <History items={s?.history ?? []} />
            <Metrics metrics={s?.metrics ?? {}} />
          </div>
        </main>
      ) : (
        <main className="grid single"><EvalPanel /></main>
      )}
      <StopBar status={s} onStopped={() => voice.current?.flush()} />
    </div>
  );
}
