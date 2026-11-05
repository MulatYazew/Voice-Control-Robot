import { useEffect, useRef, useState } from "react";
import { ActionPanel } from "./components/ActionPanel";
import { CellPanel } from "./components/CellPanel";
import { Conversation } from "./components/Conversation";
import { Header } from "./components/Header";
import { MicPanel } from "./components/MicPanel";
import { EvalPanel, History, Metrics, StopBar } from "./components/Panels";
import { PerceptionPanel } from "./components/Perception";
import { RobotPanel } from "./components/RobotPanel";
import { deriveUiState, useVoce } from "./hooks/useVoce";
import { LangCtx, type Lang, storeLang, storedLang, useT } from "./i18n/strings";
import { InspectionHistory, ProductSpecs, SmartLabelling } from "./labelling/Labelling";
import { VoiceClient, type VoiceState } from "./services/voice";

type Tab = "voice" | "label" | "specs" | "hist" | "robot" | "eval";

function Nav({ tab, setTab }: { tab: Tab; setTab: (t: Tab) => void }) {
  const t = useT();
  const items: [Tab, Parameters<typeof t>[0]][] = [["voice", "nav_voice"], ["label", "nav_label"], ["specs", "nav_specs"],
    ["hist", "nav_hist"], ["robot", "nav_robot"], ["eval", "nav_eval"]];
  return (
    <nav className="tabs">
      {items.map(([k, label]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{t(label)}</button>)}
    </nav>
  );
}

const VOICE_LANGS = ["auto", "it", "en", "zh", "ja", "am"] as const;
const VOICE_LABEL: Record<(typeof VOICE_LANGS)[number], string> = {
  auto: "Auto", it: "Italiano", en: "English", zh: "中文", ja: "日本語", am: "አማርኛ",
};

function Pipeline({ ui, metrics }: { ui: string; metrics: Record<string, { last_ms: number }> }) {
  const stages: [string, boolean, string?][] = [
    ["ASCOLTO", ui === "LISTENING"],
    ["CAPITO", ui === "PROCESSING", metrics.intent_processing_ms ? `${metrics.intent_processing_ms.last_ms} ms` : undefined],
    ["VISTO", ui === "CLARIFICATION"],
    ["CONFERMA", ui === "AWAITING_CONFIRMATION", metrics.confirmation_response_ms ? `${metrics.confirmation_response_ms.last_ms} ms` : undefined],
    ["ESECUZIONE", ui === "EXECUTING", metrics.robot_execution_ms ? `${metrics.robot_execution_ms.last_ms} ms` : undefined],
    ["RAPPORTO", ui === "COMPLETED", metrics.request_to_completion_ms ? `${metrics.request_to_completion_ms.last_ms} ms` : undefined],
  ];
  return (
    <div className="pipeline" aria-label="pipeline">
      {stages.map(([name, on, ms]) => <span key={name} className={on ? "on" : ""}>{name}{on && ms ? <small>{ms}</small> : null}</span>)}
    </div>
  );
}

export default function App() {
  const { store, setTextMode, setVoiceLang } = useVoce();
  const [lang, setLang] = useState<Lang>(storedLang());
  const [voiceState, setVoiceState] = useState<VoiceState>("off");
  const [voiceDetail, setVoiceDetail] = useState("");
  const [level, setLevel] = useState(0);
  const [wake, setWake] = useState<"" | "on" | "not supported">("");
  const [banner, setBanner] = useState("");
  const [tab, setTab] = useState<Tab>("voice");
  const voice = useRef<VoiceClient | null>(null);
  const langRef = useRef(lang);
  const voiceRef = useRef(store.voiceLang);
  const [, tick] = useState(0);
  useEffect(() => { const t = window.setInterval(() => tick((x) => x + 1), 1000); return () => window.clearInterval(t); }, []);
  useEffect(() => { langRef.current = lang; storeLang(lang); document.documentElement.lang = lang; }, [lang]);
  useEffect(() => { voiceRef.current = store.voiceLang; }, [store.voiceLang]);

  const startVoice = async () => {
    if (!voice.current) {
      voice.current = new VoiceClient({
        onState: (s, d) => { setVoiceState(s); setVoiceDetail(d ?? ""); },
        onLevel: (l) => setLevel(l),
        onWake: (w) => setWake(w),
        onBanner: (text) => setBanner(text),
      }, () => voiceRef.current);
    }
    await voice.current.start();
  };
  const switchLang = (l: Lang) => {
    setLang(l);
    // a live voice session must restart to apply the new language (fresh session, nothing pending is restored)
    if (voice.current && voiceState !== "off") { voice.current.stop(); window.setTimeout(startVoice, 300); }
  };

  const ui = deriveUiState(store, voiceState);
  const s = store.status;
  const langSel = (
    <div className="langsel" role="group" aria-label="language">
      <button className={lang === "it" ? "on" : ""} onClick={() => switchLang("it")}>ITALIANO</button>
      <button className={lang === "en" ? "on" : ""} onClick={() => switchLang("en")}>ENGLISH</button>
    </div>
  );
  return (
    <LangCtx.Provider value={lang}>
      <div className={`app ui-${ui}`}>
        <Header ui={ui} config={store.config} robot={s?.robot} voice={voiceState === "off" ? "off" : store.voice} right={langSel} />
        <div className="voicesel">
          <span className="sub">Voce</span>
          <select aria-label="lingua della voce" value={store.voiceLang} onChange={(e) => {
            setVoiceLang(e.target.value);
            if (voice.current && voiceState !== "off") { voice.current.stop(); window.setTimeout(startVoice, 300); }
          }}>
            {VOICE_LANGS.map((k) => <option key={k} value={k}>{VOICE_LABEL[k]}</option>)}
          </select>
          {store.voiceLang === "am" && <span className="hint">አማርኛ è sperimentale e non è stata provata su una registrazione. Se la voce non trascrive, usa TEXT MODE: lo STOP sul testo, il pulsante e la barra spaziatrice restano attivi.</span>}
        </div>
        <Pipeline ui={ui} metrics={s?.metrics ?? {}} />
        {store.textMode && <div className="global-textmode">TEXT MODE — NOT VOICE</div>}
        {banner && <div className="sim-banner">{banner}</div>}
        <Nav tab={tab} setTab={setTab} />
        {tab === "voice" && (
          <main className="grid">
            <div className="col">
              <MicPanel state={voiceState} detail={voiceDetail} level={level} wake={wake} available={!!store.config?.grok_configured}
                onStart={startVoice} onPause={(p) => voice.current?.setPaused(p)} onStop={() => voice.current?.stop()} />
              <ActionPanel status={s} progress={store.progress} execStart={store.execStart} voiceLang={store.voiceLang} />
              <Conversation chat={store.chat} textMode={store.textMode} setTextMode={setTextMode}
                grokAvailable={!!store.config?.grok_configured} voiceLang={store.voiceLang} />
            </div>
            <div className="col">
              <PerceptionPanel config={store.config} gate={s?.gate} />
              <RobotPanel status={s} layout={store.layout} joints={store.joints} />
              <History items={s?.history ?? []} />
            </div>
          </main>
        )}
        {tab === "label" && <SmartLabelling />}
        {tab === "specs" && <ProductSpecs />}
        {tab === "hist" && <InspectionHistory />}
        {tab === "robot" && (
          <main className="grid">
            <div className="col">
              <RobotPanel status={s} layout={store.layout} joints={store.joints} />
              <CellPanel />
            </div>
            <div className="col"><Metrics metrics={s?.metrics ?? {}} /><History items={s?.history ?? []} /></div>
          </main>
        )}
        {tab === "eval" && <main className="grid single"><EvalPanel /></main>}
        <StopBar status={s} onStopped={() => voice.current?.flush()} />
      </div>
    </LangCtx.Provider>
  );
}
