export interface BrEvent {
  seq: number;
  ts: string;
  source: string;
  type: string;
  run_id?: string;
  agent?: string;
  case_id?: string;
  data: Record<string, unknown>;
}

export interface CitedCall {
  tool: string;
  service: string;
  detail: string;
}

export interface Finding {
  category: string;
  claim: string;
  reason: string;
  verdict: string;
  confidence: number | null;
  evidence_span_ids: string[];
}

export interface PatchInfo {
  sha: string;
  paths: string[];
  checkpoint: string;
  line: string | null;
  why: string | null;
}

export interface ConfirmInfo {
  rerun_clean: boolean;
  control_clean: boolean;
  watchdog_clean: boolean;
}

export interface Trip {
  at: number;
  service: string;
  tool: string;
  planted: boolean;
  forbidden: boolean;
}

export interface AgentState {
  id: string;
  index: number;
  trail: "ink" | "red";
  redAt: number | null;
  frozenAt: number | null;
  cagedAt: number | null;
  releasedAt: number | null;
  ringAt: number | null;
  plantedAt: number | null;
  blockedAt: number | null;
  trips: Trip[];
  calls: { tool: string; service: string; path: string; planted: boolean; at: number }[];
  rerun: RerunInfo | null;
}

export interface RerunInfo {
  kind: "confirm" | "control";
  runId: string;
  parent: string;
  parentIndex: number;
  slot: number;
  spawnAt: number;
  doneAt: number | null;
}

export interface CaseState {
  id: string;
  agent: string;
  status: string;
  traceId: string;
  traceUrl: string;
  source: string;
  ruleHits: string[];
  cited: CitedCall[];
  findings: Finding[];
  verdicts: Finding[];
  writeback: { feedback: string | null; spanId: string | null } | null;
  patch: PatchInfo | null;
  confirmingNote: string | null;
  confirm: ConfirmInfo | null;
  remembered: string | null;
  failure: { step: string; reason: string } | null;
  openedAt: number;
}

export interface ViewState {
  agents: Record<string, AgentState>;
  cases: Record<string, CaseState>;
  order: string[];
  barrier: { sha: string; at: number } | null;
  plantedPulseAt: number | null;
}

export interface ReplayFile {
  run_id: string;
  label: string;
  recorded: string;
  agents: string[];
  note: string;
  cases: Record<string, { agent: string; trace_url: string; source: string }>;
  events: BrEvent[];
}

export const PIPELINE = [
  "flagged",
  "paused",
  "investigating",
  "investigated",
  "verified",
  "quarantined",
  "patching",
  "patched",
  "confirming",
  "fixed",
] as const;

export const FAILURES = new Set(["needs_review", "not_fixed", "dismissed"]);

export function stepIndex(status: string): number {
  return PIPELINE.indexOf(status as (typeof PIPELINE)[number]);
}
