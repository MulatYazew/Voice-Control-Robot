// Camera / inspection strings, adapted from grokarm-ur3e (Apache-2.0, see NOTICE).
// The language follows VOCE's it/en switch (LangCtx in ./strings).
import { createContext, useContext } from "react";
import { LangCtx } from "./strings";

export const LANGS: { code: string; label: string }[] = [
  { code: "it", label: "Italiano" }, { code: "en", label: "English" }, { code: "zh", label: "中文" },
  { code: "es", label: "Español" }, { code: "fr", label: "Français" }, { code: "de", label: "Deutsch" },
  { code: "ja", label: "日本語" },
];

const it = {
  tagline: "Parla naturalmente. Il robot capisce. Tu confermi.",
  mode_real: "ROBOT REALE", mode_ursim: "SIMULAZIONE · URSim", mode_sim: "SIMULAZIONE · interna",
  grok: "Grok Voice", robot: "Robot", not_configured: "non configurato", connected: "connesso", reconnecting: "riconnessione…",
  error: "errore", idle: "inattivo", not_connected: "non connesso", moving: "in movimento", language: "Lingua",
  st_DISCONNECTED: "DISCONNESSO", st_READY: "PRONTO", st_LISTENING: "IN ASCOLTO", st_PROCESSING: "ELABORAZIONE",
  st_CLARIFICATION: "CHIARIMENTO", st_AWAITING_CONFIRMATION: "ATTESA CONFERMA", st_EXECUTING: "IN ESECUZIONE",
  st_COMPLETED: "COMPLETATO", st_STOPPED: "FERMO", st_FAULT: "GUASTO", st_RECONNECTING: "RICONNESSIONE",
  tab_control: "Controllo", tab_eval: "Valutazione",
  mic_start: "AVVIA ASSISTENTE VOCALE", mic_starting: "AVVIO…", mic_paused: "Microfono in pausa",
  mic_reconnecting: "Riconnessione a Grok…", mic_live: "Microfono attivo · parla in {lang}", mic_resume: "Riprendi microfono",
  mic_pause: "Pausa microfono", mic_close: "Chiudi sessione vocale",
  mic_no_grok: "Grok non configurato: disponibile solo la modalità testo.", mic_error: "Errore del microfono",
  conv: "Conversazione", text_mode: "Modalità testo",
  conv_empty: "Avvia l'assistente e prova: «Inizia l'ispezione sul vassoio A, scarta i pezzi con graffi».",
  text_banner: "TEXT MODE — NOT VOICE · comandi scritti, stesse regole di sicurezza", text_ph: "Scrivi un comando…",
  rules: "regole", grok_text: "Grok (testo)", send: "Invia",
  action: "Azione", step: "passo", tool: "strumento", answer_hint: "Rispondi: «Sì, confermo» oppure «No».",
  waiting_question: " (in attesa della domanda)", to_confirm: "s per confermare",
  confirm_anytime: "Nessun limite di tempo: puoi confermare quando vuoi.", executing: "In esecuzione",
  move_done: "Movimento {done}/{total} completato", move_started: "Movimento avviato", say_stop: "di' «stop» per fermare",
  last_result: "Ultimo risultato", moves: "movimenti", no_action: "Nessuna azione in attesa.",
  out_completed: "completato", out_stopped: "interrotto da STOP", out_not_started: "non eseguito", out_failed: "fallito",
  out_unknown: "esito sconosciuto",
  history: "Storico azioni", no_history: "Ancora nessuna azione.", latency: "Latenze", this_session: "questa sessione",
  no_metrics: "Nessuna misura ancora.", measure: "Misura", median: "mediana",
  reset: "RIPRISTINA", reset_q: "Ripristinare il sistema? Verifica che l'area di lavoro sia sicura.",
  reset_refused: "Ripristino rifiutato", stop_space: "spazio", stop_failed: "STOP non inviato",
  disclaimer: "STOP software: non è un arresto di emergenza certificato. Usare il pulsante di emergenza fisico del robot.",
  known_pose: "Posa nota", mode: "Modo", safety: "Sicurezza", program: "Programma", joints: "Giunti (rad)", yes: "sì", no: "no",
  trays: "Vassoi", trays_sub: "presunti · nessun sensore", in_zone: "In zona", on_fork: "Sulla forca",
  yes_assumed: "sì (presunto)", none: "nessuno", fault: "GUASTO", tray: "vassoio",
  camera: "Telecamera di ispezione", cam_browser: "webcam di questo computer", cam_url: "URL snapshot",
  cam_start: "Accendi webcam", cam_stop: "Spegni webcam", cam_device: "Dispositivo", cam_none: "Nessuna immagine ancora.",
  cam_age: "ultimo fotogramma {age} s fa", cam_denied: "Accesso alla webcam negato o non disponibile",
  cam_url_hint: "Le immagini arrivano dall'URL configurato (CAMERA_URL).",
  phone_open: "Sul telefono, apri con Chrome e accetta l'avviso del certificato, poi accendi la webcam:",
  touch_hint: "Non c'è una pinza. Dopo il sì, la punta va sopra il bicchiere e lo tocca. Prima calibra il tavolo.",
  inspection: "Ispezione qualità", rule: "Regola", rule_ph: "es. Scarta i pezzi con graffi o anomalie",
  start_inspection: "Avvia ispezione", part_ready: "Pezzo pronto nel vassoio {tray}", analysing: "Analisi in corso…",
  last_inspection: "Ultimo esito", counts: "Conteggi", flow: "{a} ingresso → telecamera → {b} approvati / {c} scartati",
  inspector_mock: "ispettore simulato (nessuna chiave xAI)", inspector_grok: "Grok Vision",
  no_inspection: "Nessuna ispezione ancora. Dì: «Inizia l'ispezione sul vassoio A, scarta i pezzi con graffi».",
  status_PASS: "APPROVATO", status_FAIL: "SCARTATO", status_UNSURE: "INCERTO", status_NO_FRAME: "NESSUNA IMMAGINE",
  sk_vai_a_posa: "Vai a posa", sk_indica: "Indica", sk_solleva_vassoio: "Solleva vassoio",
  sk_appoggia_vassoio: "Appoggia vassoio", sk_spingi: "Spingi", sk_porta_in_ispezione: "Porta in ispezione",
  sk_smista: "Smista nel vassoio",
  cups: "Riconosci bicchieri", cups_none: "Nessun bicchiere colorato visto.", point: "Indica", touch: "Tocca", colour_red: "rosso", colour_orange: "arancione", colour_yellow: "giallo", colour_green: "verde", colour_blue: "blu", colour_purple: "viola", colour_pink: "rosa", colour_white: "bianco", colour_grey: "grigio", colour_black: "nero", cups_bg: "Foto tavolo vuoto", cups_bg_clear: "Cancella sfondo", det_colour: "solo colori vivaci", det_background: "tutti i colori (sfondo)", cups_bg_hint: "Nero, giallo e arancione li vede già; la pelle non conta come arancione. Per bianco e grigio, togli i bicchieri e scatta il tavolo vuoto.",
  calib: "Calibra tavolo", calib_hint: "Stampa il segno: il quadrato nero misura 50 mm, piatto sul tavolo. La freccia indica il centro del lato destro. Punta verticale: prima il centro, poi la freccia. Poi accendi il riconoscimento e di' il colore: la punta scende da sopra.", marker_center: "Punta al centro", marker_axis: "Punta sulla freccia", marker_seen: "segno visto", marker_missing: "segno non visto", table_reach: "Area del tavolo che il braccio può toccare, dalla sua lunghezza: {w} × {d} mm. Il bicchiere può stare ovunque lì dentro.", calib_undo: "Annulla punto", calib_clear: "Cancella", calib_points: "{n} / 4 punti", calib_ready: "calibrata", calib_err: "errore {mm} mm",
};
type Dict = typeof it;

const en: Dict = {
  tagline: "Speak naturally. The robot understands. You confirm.",
  mode_real: "REAL ROBOT", mode_ursim: "SIMULATION · URSim", mode_sim: "SIMULATION · internal",
  grok: "Grok Voice", robot: "Robot", not_configured: "not configured", connected: "connected", reconnecting: "reconnecting…",
  error: "error", idle: "idle", not_connected: "not connected", moving: "moving", language: "Language",
  st_DISCONNECTED: "DISCONNECTED", st_READY: "READY", st_LISTENING: "LISTENING", st_PROCESSING: "PROCESSING",
  st_CLARIFICATION: "CLARIFYING", st_AWAITING_CONFIRMATION: "AWAITING CONFIRMATION", st_EXECUTING: "EXECUTING",
  st_COMPLETED: "COMPLETED", st_STOPPED: "STOPPED", st_FAULT: "FAULT", st_RECONNECTING: "RECONNECTING",
  tab_control: "Control", tab_eval: "Evaluation",
  mic_start: "START VOICE ASSISTANT", mic_starting: "STARTING…", mic_paused: "Microphone paused",
  mic_reconnecting: "Reconnecting to Grok…", mic_live: "Microphone live · speak {lang}", mic_resume: "Resume microphone",
  mic_pause: "Pause microphone", mic_close: "Close voice session",
  mic_no_grok: "Grok is not configured: only text mode is available.", mic_error: "Microphone error",
  conv: "Conversation", text_mode: "Text mode",
  conv_empty: "Start the assistant and try: “Begin inspection on Tray A. Reject items with scratches or anomalies.”",
  text_banner: "TEXT MODE — NOT VOICE · typed commands, same safety rules", text_ph: "Type a command…",
  rules: "rules", grok_text: "Grok (text)", send: "Send",
  action: "Action", step: "step", tool: "tool", answer_hint: "Answer: “Yes, go ahead” or “No”.",
  waiting_question: " (waiting for the question)", to_confirm: "s to confirm",
  confirm_anytime: "No time limit. You can confirm whenever you are ready.", executing: "Executing",
  move_done: "Move {done}/{total} done", move_started: "Motion started", say_stop: "say “stop” to halt",
  last_result: "Last result", moves: "moves", no_action: "No action pending.",
  out_completed: "completed", out_stopped: "interrupted by STOP", out_not_started: "not executed", out_failed: "failed",
  out_unknown: "unknown outcome",
  history: "Action history", no_history: "No actions yet.", latency: "Latency", this_session: "this session",
  no_metrics: "No measurements yet.", measure: "Measure", median: "median",
  reset: "RESET", reset_q: "Reset the system? Make sure the work area is safe.", reset_refused: "Reset refused",
  stop_space: "space", stop_failed: "STOP not sent",
  disclaimer: "Software STOP: not a certified emergency stop. Use the robot's physical emergency button.",
  known_pose: "Known pose", mode: "Mode", safety: "Safety", program: "Program", joints: "Joints (rad)", yes: "yes", no: "no",
  trays: "Trays", trays_sub: "assumed · no tray sensor", in_zone: "In zone", on_fork: "On the fork",
  yes_assumed: "yes (assumed)", none: "none", fault: "FAULT", tray: "tray",
  camera: "Inspection camera", cam_browser: "this computer's webcam", cam_url: "snapshot URL",
  cam_start: "Turn webcam on", cam_stop: "Turn webcam off", cam_device: "Device", cam_none: "No image yet.",
  cam_age: "last frame {age} s ago", cam_denied: "Webcam access denied or unavailable",
  cam_url_hint: "Frames come from the configured URL (CAMERA_URL).",
  phone_open: "On the phone, open this in Chrome, accept the certificate warning, then turn the camera on:",
  touch_hint: "There is no gripper. After you confirm, the tip moves above the cup and touches it. Calibrate the table first.",
  inspection: "Quality inspection", rule: "Rule", rule_ph: "e.g. Reject items with scratches or anomalies",
  start_inspection: "Start inspection", part_ready: "Part ready in tray {tray}", analysing: "Analysing…",
  last_inspection: "Last result", counts: "Counts", flow: "{a} input → camera → {b} approved / {c} rejected",
  inspector_mock: "simulated inspector (no xAI key)", inspector_grok: "Grok Vision",
  no_inspection: "No inspection yet. Say: “Begin inspection on Tray A. Reject items with scratches or anomalies.”",
  status_PASS: "PASS", status_FAIL: "FAIL", status_UNSURE: "UNSURE", status_NO_FRAME: "NO IMAGE",
  sk_vai_a_posa: "Go to pose", sk_indica: "Point", sk_solleva_vassoio: "Lift tray", sk_appoggia_vassoio: "Put tray down",
  sk_spingi: "Push", sk_porta_in_ispezione: "Bring to camera", sk_smista: "Sort into tray",
  cups: "Detect cups", cups_none: "No coloured cup in view.", point: "Point", touch: "Touch", colour_red: "red", colour_orange: "orange", colour_yellow: "yellow", colour_green: "green", colour_blue: "blue", colour_purple: "purple", colour_pink: "pink", colour_white: "white", colour_grey: "grey", colour_black: "black", cups_bg: "Photo of empty table", cups_bg_clear: "Clear background", det_colour: "bright colours only", det_background: "all colours (background)", cups_bg_hint: "Black, yellow and orange are detected directly, and skin is not orange. For white and grey, remove the cups and take an empty-table photo.",
  calib: "Calibrate table", calib_hint: "Print the mark so the black square is 50 mm on a side, flat on the table. The arrow points at the middle of its right edge. Tip straight down: record the centre, then the arrow. Then turn on recognition and name a colour. The tip comes down from above.", marker_center: "Tip on the centre", marker_axis: "Tip on the arrow", marker_seen: "mark in view", marker_missing: "mark not in view", table_reach: "Table area this arm can touch, from its own reach: {w} × {d} mm. A cup can sit anywhere inside it.", calib_undo: "Undo point", calib_clear: "Clear", calib_points: "{n} / 4 points", calib_ready: "calibrated", calib_err: "error {mm} mm",
};

const zh: Dict = {
  tagline: "自然地说话，机器人理解，由你确认。",
  mode_real: "真实机器人", mode_ursim: "仿真 · URSim", mode_sim: "仿真 · 内置",
  grok: "Grok 语音", robot: "机器人", not_configured: "未配置", connected: "已连接", reconnecting: "重新连接中…",
  error: "错误", idle: "空闲", not_connected: "未连接", moving: "运动中", language: "语言",
  st_DISCONNECTED: "未连接", st_READY: "就绪", st_LISTENING: "聆听中", st_PROCESSING: "处理中", st_CLARIFICATION: "需要澄清",
  st_AWAITING_CONFIRMATION: "等待确认", st_EXECUTING: "执行中", st_COMPLETED: "已完成", st_STOPPED: "已停止",
  st_FAULT: "故障", st_RECONNECTING: "重新连接",
  tab_control: "控制", tab_eval: "评测",
  mic_start: "启动语音助手", mic_starting: "启动中…", mic_paused: "麦克风已暂停", mic_reconnecting: "正在重新连接 Grok…",
  mic_live: "麦克风已开启 · 请说{lang}", mic_resume: "恢复麦克风", mic_pause: "暂停麦克风", mic_close: "关闭语音会话",
  mic_no_grok: "未配置 Grok：只能使用文字模式。", mic_error: "麦克风错误",
  conv: "对话", text_mode: "文字模式",
  conv_empty: "启动助手后试试：“开始检测 A 托盘，有划痕或异常的判为不合格”。",
  text_banner: "文字模式 — 非语音 · 输入的指令同样经过安全确认", text_ph: "输入指令…",
  rules: "规则", grok_text: "Grok（文字）", send: "发送",
  action: "动作", step: "步骤", tool: "工具", answer_hint: "请回答：“是的，确认” 或 “不”。",
  waiting_question: "（等待提问）", to_confirm: "秒内确认",
  confirm_anytime: "没有时间限制，随时可以说「是的」。", executing: "执行中",
  move_done: "已完成第 {done}/{total} 段运动", move_started: "运动已开始", say_stop: "说“停”即可停止",
  last_result: "上次结果", moves: "段运动", no_action: "没有待确认的动作。",
  out_completed: "完成", out_stopped: "被 STOP 中断", out_not_started: "未执行", out_failed: "失败", out_unknown: "结果未知",
  history: "动作记录", no_history: "还没有动作。", latency: "延迟", this_session: "本次会话", no_metrics: "还没有测量数据。",
  measure: "测量项", median: "中位数",
  reset: "复位", reset_q: "确定复位系统？请确认工作区域安全。", reset_refused: "复位被拒绝", stop_space: "空格",
  stop_failed: "STOP 未发送",
  disclaimer: "软件 STOP 不是经过认证的急停，请使用机器人上的物理急停按钮。",
  known_pose: "已知姿态", mode: "模式", safety: "安全状态", program: "程序", joints: "关节 (rad)", yes: "是", no: "否",
  trays: "托盘", trays_sub: "推定 · 无托盘传感器", in_zone: "所在区域", on_fork: "叉上",
  yes_assumed: "是（推定）", none: "无", fault: "故障", tray: "托盘",
  camera: "检测相机", cam_browser: "本机摄像头", cam_url: "快照 URL", cam_start: "打开摄像头", cam_stop: "关闭摄像头",
  cam_device: "设备", cam_none: "还没有画面。", cam_age: "最新画面 {age} 秒前", cam_denied: "摄像头被拒绝或不可用",
  cam_url_hint: "画面来自配置的 URL（CAMERA_URL）。",
  phone_open: "手机用 Chrome 打开下面的地址，警告页点「高级 → 继续」，再打开摄像头：",
  touch_hint: "没有夹爪。确认后，末端会移到杯子上方再轻轻碰到。要先做完桌面标定。",
  inspection: "质量检测", rule: "规则", rule_ph: "例如：有划痕或异常的判为不合格",
  start_inspection: "开始检测", part_ready: "{tray} 托盘已放好零件", analysing: "分析中…",
  last_inspection: "最近结果", counts: "统计", flow: "{a} 来料 → 相机 → {b} 合格 / {c} 不合格",
  inspector_mock: "模拟检测（未配置 xAI 密钥）", inspector_grok: "Grok 视觉",
  no_inspection: "还没有检测。可以说：“开始检测 A 托盘，有划痕的判为不合格”。",
  status_PASS: "合格", status_FAIL: "不合格", status_UNSURE: "无法判断", status_NO_FRAME: "无画面",
  sk_vai_a_posa: "移动到姿态", sk_indica: "指向", sk_solleva_vassoio: "抬起托盘", sk_appoggia_vassoio: "放下托盘",
  sk_spingi: "推动", sk_porta_in_ispezione: "送到相机前", sk_smista: "分拣到托盘",
  cups: "识别纸杯", cups_none: "画面里没有彩色纸杯。", point: "指向", touch: "碰到", colour_red: "红色", colour_orange: "橙色", colour_yellow: "黄色", colour_green: "绿色", colour_blue: "蓝色", colour_purple: "紫色", colour_pink: "粉色", colour_white: "白色", colour_grey: "灰色", colour_black: "黑色", cups_bg: "拍空桌背景", cups_bg_clear: "清除背景", det_colour: "仅鲜艳颜色", det_background: "所有颜色（背景差分）", cups_bg_hint: "黑色、黄色、橘色直接认，皮肤不会当成橘色。白色和灰色仍要先拿走杯子，再拍空桌。",
  calib: "标定桌面", calib_hint: "打印下面的标记，黑方块边长量成 50 毫米，平放在桌上。箭头指着黑方块右边的中点。针尖竖直朝下，先点在中心，再点在箭头尖上。然后打开识别，说颜色：针尖会先到杯子上方，再慢慢落下。", marker_center: "针尖在标记中心", marker_axis: "针尖在箭头上", marker_seen: "画面里有标记", marker_missing: "画面里还没有标记", table_reach: "这台手臂按自身尺寸在桌面上够得到的范围：{w} × {d} 毫米。纸杯可以放在这片里的任何位置。", calib_undo: "撤销一点", calib_clear: "清除", calib_points: "{n} / 4 个点", calib_ready: "已标定", calib_err: "误差 {mm} 毫米",
};

const DICTS: Record<string, Dict> = { it, en, zh };
const SPOKEN: Record<string, string> = {
  it: "italiano", en: "English", zh: "中文", es: "español", fr: "français", de: "Deutsch", ja: "日本語",
};

export type T = (key: keyof Dict, vars?: Record<string, string | number>) => string;

export function makeT(lang: string): T {
  const d = DICTS[lang] ?? en;
  return (key, vars) => {
    let s: string = d[key] ?? en[key] ?? String(key);
    for (const [k, v] of Object.entries(vars ?? {})) s = s.replace(`{${k}}`, String(v));
    return s;
  };
}

export const spokenName = (lang: string) => SPOKEN[lang] ?? lang;
export const LangContext = createContext<{ lang: string; t: T }>({ lang: "it", t: makeT("it") });
export const useT = () => { const lang = useContext(LangCtx); return { lang, t: makeT(lang) }; };
void LangContext;
