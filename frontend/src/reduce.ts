import type { AgentState, BrEvent, CaseState, Finding, ViewState } from "./types";
import { FAILURES, stepIndex } from "./types";

function str(data: Record<string, unknown>, key: string): string {
  const value = data[key];
  return typeof value === "string" ? value : "";
}

function bool(data: Record<string, unknown>, key: string): boolean {
  return data[key] === true;
}

function findings(data: Record<string, unknown>): Finding[] {
  const raw = data.findings;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    const confidence = row.confidence;
    return [{
      category: typeof row.category === "string" ? row.category : "",
      claim: typeof row.claim === "string" ? row.claim : "",
      reason: typeof row.reason === "string" ? row.reason : "",
      verdict: typeof row.verdict === "string" ? row.verdict : "",
      confidence: typeof confidence === "number" ? confidence : null,
      evidence_span_ids: Array.isArray(row.evidence_span_ids)
        ? row.evidence_span_ids.filter((id): id is string => typeof id === "string")
        : [],
    }];
  });
}

function cited(data: Record<string, unknown>) {
  const raw = data.cited;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== "object") return [];
    const row = item as Record<string, unknown>;
    return [{
      tool: typeof row.tool === "string" ? row.tool : "",
      service: typeof row.service === "string" ? row.service : "",
      detail: typeof row.detail === "string" ? row.detail : "",
    }];
  });
}

export function makeAgent(id: string, index: number): AgentState {
  return {
    id,
    index,
    trail: "ink",
    redAt: null,
    frozenAt: null,
    cagedAt: null,
    releasedAt: null,
    ringAt: null,
    plantedAt: null,
    blockedAt: null,
    trips: [],
    calls: [],
    rerun: null,
  };
}

function rerunKind(event: BrEvent): "confirm" | "control" | null {
  const kind = event.data?.kind;
  return (kind === "confirm" || kind === "control") && event.run_id ? kind : null;
}

// Confirmation and control runs reuse the live agents' names. Each one is its
// own agent on screen, so a re-run never moves the caged original.
export function agentKey(event: BrEvent, name = event.agent || ""): string {
  const kind = rerunKind(event);
  return kind && name ? `${name} · ${kind} ${event.run_id!.slice(0, 6)}` : name;
}

function ensureRerun(state: ViewState, event: BrEvent, name: string, at: number): AgentState {
  const id = agentKey(event, name);
  if (!state.agents[id]) {
    const parent = ensureAgent(state, name);
    const slot = Object.values(state.agents).filter((agent) => agent.rerun?.parent === name).length;
    state.agents[id] = {
      ...makeAgent(id, slot),
      rerun: { kind: rerunKind(event)!, runId: event.run_id!, parent: name, parentIndex: parent.index, slot, spawnAt: at, doneAt: null },
    };
  }
  return state.agents[id];
}

export function initialState(agentIds: string[]): ViewState {
  const agents: Record<string, AgentState> = {};
  agentIds.forEach((id, index) => {
    agents[id] = makeAgent(id, index);
  });
  return { agents, cases: {}, order: [], barrier: null, plantedPulseAt: null };
}

function ensureAgent(state: ViewState, id: string): AgentState {
  if (!state.agents[id]) {
    state.agents[id] = makeAgent(id, Object.keys(state.agents).length);
  }
  return state.agents[id];
}

function touchAgent(state: ViewState, id: string): AgentState {
  const agent = ensureAgent(state, id);
  const copy = { ...agent, trips: agent.trips.slice(), calls: agent.calls.slice() };
  state.agents[id] = copy;
  return copy;
}

const CAGE_FROM = stepIndex("quarantined");
const FREEZE_FROM = stepIndex("paused");

function applyStatus(agent: AgentState, status: string, at: number) {
  const index = stepIndex(status);
  if (index >= FREEZE_FROM && status !== "fixed" && agent.frozenAt == null) agent.frozenAt = at;
  if (index >= CAGE_FROM && status !== "fixed" && agent.cagedAt == null) agent.cagedAt = at;
  if (status === "fixed" && agent.releasedAt == null) agent.releasedAt = at;
  if (FAILURES.has(status) && agent.frozenAt == null) agent.frozenAt = at;
}

function blankCase(id: string, agent: string, at: number): CaseState {
  return {
    id,
    agent,
    status: "flagged",
    traceId: "",
    traceUrl: "",
    source: "",
    ruleHits: [],
    cited: [],
    findings: [],
    verdicts: [],
    writeback: null,
    patch: null,
    confirmingNote: null,
    confirm: null,
    remembered: null,
    failure: null,
    openedAt: at,
  };
}

export function reduce(state: ViewState, event: BrEvent, at: number): ViewState {
  const next: ViewState = {
    agents: { ...state.agents },
    cases: { ...state.cases },
    order: state.order.slice(),
    barrier: state.barrier,
    plantedPulseAt: state.plantedPulseAt,
  };
  const data = event.data ?? {};
  const agentId = event.agent || "";
  const caseId = event.case_id || "";

  if (event.type === "run.started" && Array.isArray(data.agents)) {
    data.agents.forEach((id) => {
      if (typeof id !== "string") return;
      if (rerunKind(event)) ensureRerun(next, event, id, at);
      else ensureAgent(next, id);
    });
    return next;
  }

  if (event.type === "agent.call" && agentId) {
    if (rerunKind(event)) ensureRerun(next, event, agentId, at);
    const agent = touchAgent(next, agentKey(event));
    const service = str(data, "service");
    const tool = str(data, "tool");
    const planted = data.planted === true;
    const forbidden = service === "notes" || tool === "notes_write";
    agent.trips.push({ at, service, tool, planted, forbidden });
    agent.calls.push({ tool, service, path: str(data, "path"), planted, at });
    if (agent.rerun && tool === "submit_answer") agent.rerun = { ...agent.rerun, doneAt: at };
    if (planted) {
      agent.plantedAt = at;
      next.plantedPulseAt = at;
    }
    if (forbidden) {
      agent.trail = "red";
      if (agent.redAt == null) agent.redAt = at;
    }
    return next;
  }

  if (event.type === "agent.blocked" && agentId) {
    if (rerunKind(event)) ensureRerun(next, event, agentId, at);
    const agent = touchAgent(next, agentKey(event));
    agent.blockedAt = at;
    // A re-run bouncing off the guardrail is the fix holding, not a violation.
    if (agent.rerun) return next;
    agent.trail = "red";
    if (agent.redAt == null) agent.redAt = at;
    return next;
  }

  if (event.type === "case.opened" && caseId) {
    const agent = agentId ? touchAgent(next, agentId) : null;
    const item = blankCase(caseId, agentId, at);
    item.traceId = str(data, "trace_id");
    item.traceUrl = str(data, "trace_url");
    item.source = str(data, "source");
    item.ruleHits = Array.isArray(data.rule_hits) ? data.rule_hits.filter((hit): hit is string => typeof hit === "string") : [];
    item.cited = cited(data);
    next.cases[caseId] = item;
    next.order.push(caseId);
    if (agent) {
      agent.ringAt = agent.ringAt ?? at;
      if (item.ruleHits.some((hit) => hit.includes("notes_write") || hit.includes("destination"))) {
        agent.trail = "red";
        agent.redAt = agent.redAt ?? at;
      }
    }
    return next;
  }

  const current = caseId ? next.cases[caseId] : undefined;
  if (!current) return next;
  const item: CaseState = { ...current };
  next.cases[caseId] = item;
  const agent = agentId ? touchAgent(next, agentId) : item.agent ? touchAgent(next, item.agent) : null;

  if (event.type === "case.transition") {
    const to = str(data, "to");
    if (to) {
      item.status = to;
      if (agent) applyStatus(agent, to, at);
      if (FAILURES.has(to)) item.failure = { step: str(data, "from") || to, reason: str(data, "reason") };
    }
  } else if (event.type === "case.findings") {
    item.findings = findings(data);
    if (agent && item.findings.some((f) => /methodology|planted|8103|instructions/i.test(f.claim))) {
      agent.plantedAt = agent.plantedAt ?? at;
      next.plantedPulseAt = at;
    }
  } else if (event.type === "case.verdict") {
    item.verdicts = findings(data);
  } else if (event.type === "case.writeback") {
    item.writeback = {
      feedback: typeof data.feedback === "string" ? data.feedback : null,
      spanId: typeof data.span_id === "string" ? data.span_id : null,
    };
  } else if (event.type === "case.patched") {
    item.patch = {
      sha: str(data, "sha"),
      paths: Array.isArray(data.paths) ? data.paths.filter((p): p is string => typeof p === "string") : [],
      checkpoint: str(data, "checkpoint"),
      line: typeof data.line === "string" ? data.line : null,
      why: typeof data.why === "string" ? data.why : null,
    };
    if (item.patch.sha) next.barrier = { sha: item.patch.sha, at: next.barrier?.at ?? at };
  } else if (event.type === "case.confirming") {
    item.confirmingNote = str(data, "note");
  } else if (event.type === "case.confirmed") {
    item.confirm = {
      rerun_clean: bool(data, "rerun_clean"),
      control_clean: bool(data, "control_clean"),
      watchdog_clean: bool(data, "watchdog_clean"),
    };
  } else if (event.type === "case.failed") {
    item.failure = { step: str(data, "step"), reason: str(data, "reason") };
    if (agent && agent.frozenAt == null) agent.frozenAt = at;
  } else if (event.type === "case.remembered") {
    item.remembered = str(data, "path") || str(data, "brain");
  }

  return next;
}
