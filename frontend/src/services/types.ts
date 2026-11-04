export type Gate = "IDLE" | "PENDING_CONFIRMATION" | "EXECUTING" | "STOPPED" | "FAULT";

export type UiState =
  | "DISCONNECTED" | "READY" | "LISTENING" | "PROCESSING" | "CLARIFICATION" | "AWAITING_CONFIRMATION"
  | "EXECUTING" | "COMPLETED" | "STOPPED" | "FAULT" | "RECONNECTING";

export interface Pending {
  id: string; skill: string; parametri: Record<string, string>; conferma: string; strumento: string | null;
  expires_in_s: number; ttl_s: number; question_asked: boolean; step: number; steps: number;
  sequenza: { move: string; posa: string | null; secondi: number | null }[];
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
}

export interface Config {
  mode: "sim" | "ursim" | "real"; robot_name: string; robot_model: string; polyscope: string; zones: string[];
  directions: string[]; ttl_s: number; grok_model: string; grok_configured: boolean; warnings: string[];
}

export interface Layout { zones: Record<string, number>; poses: Record<string, number[]> }

export interface ChatLine {
  id: string; role: "user" | "assistant" | "system"; text: string; final: boolean; source?: string; ts: number;
  kind?: string;
}

export type VoiceStatus = "off" | "connecting" | "connected" | "reconnecting" | "error" | "closed" | "disconnected" | "idle";
