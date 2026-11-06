export type Gate = "IDLE" | "PENDING_CONFIRMATION" | "EXECUTING" | "STOPPED" | "FAULT";

export type UiState =
  | "DISCONNECTED" | "READY" | "LISTENING" | "PROCESSING" | "CLARIFICATION" | "AWAITING_CONFIRMATION"
  | "EXECUTING" | "COMPLETED" | "STOPPED" | "FAULT" | "RECONNECTING";

export interface Pending {
  id: string; skill: string; parametri: Record<string, string>; conferma: string; conferma_en?: string; strumento: string | null;
  expires_in_s: number; ttl_s: number; question_asked: boolean; step: number; steps: number;
  sequenza: { move: string; posa: string | null; secondi: number | null }[];
  frasi?: Record<string, string>;
}

export interface RobotStatus {
  connected: boolean; moving: boolean; robot_mode: string | null; safety: string | null; program: string | null;
  remote_control: boolean | null; joints: number[] | null; detail: string; ok_for_motion: boolean;
}

export interface World {
  robot_verificato: { posa_nota: string | null; giunti: number[] | null; skill_attiva: string | null; guasto: string | null };
  oggetti_presunti: { vassoi_in_zona: string[]; zone_libere: string[]; vassoio_sulla_forca: boolean; nota: string };
}

export interface MetricSummary { n: number; median_ms: number; max_ms: number; last_ms: number }

export interface HistoryItem { ts: number; kind: string; [k: string]: unknown }

export interface Status {
  gate: Gate; pending: Pending | null; world: World; robot: RobotStatus; robot_adapter: string; mode: string;
  fault: string | null; last_stop: Record<string, any> | null; last_result: Record<string, any> | null;
  metrics: Record<string, MetricSummary>; history: HistoryItem[];
  inspection?: InspectionState; camera?: CameraStatus | null;
}

export interface InspectionResult {
  status: "PASS" | "FAIL" | "UNSURE" | "NO_FRAME"; reason: string; source: string; rule: string; at: number;
  latency_ms?: number; error?: string; frame_id?: number | null;
}

export interface InspectionState {
  rule: string; running: boolean; last: InspectionResult | null; counts: Record<string, number>; has_image: boolean;
}

export interface CameraStatus {
  source: "browser" | "url"; frame: { id: number; age_s: number; size: [number, number] } | null; error: string;
}

export interface Config {
  mode: "sim" | "ursim" | "real"; robot_name: string; robot_model: string; polyscope: string; zones: string[];
  directions: string[]; ttl_s: number; grok_model: string; grok_configured: boolean; warnings: string[];
  camera_available?: boolean; camera_source?: string; camera_calibrated?: boolean; station?: string; delivery_zone?: string;
  cell_camera_source?: "browser" | "url"; inspector?: "grok" | "mock"; phone_urls?: string[];
  inspection?: { input_zone: string; pass_zone: string; fail_zone: string; labels: Record<string, string> } | null;
}

export interface Layout { zones: Record<string, number>; poses: Record<string, number[]> }

export interface ChatLine {
  id: string; role: "user" | "assistant" | "system"; text: string; final: boolean; source?: string; ts: number;
  kind?: string; lang?: string;
}

export type VoiceStatus = "off" | "connecting" | "connected" | "reconnecting" | "error" | "closed" | "disconnected" | "idle";
