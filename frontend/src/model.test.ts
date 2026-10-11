import { describe, expect, it } from "vitest";
import { initialState, reduce } from "./reduce";
import { compileTimeline, sortEvents } from "./timeline";
import type { BrEvent } from "./types";

const T0 = Date.parse("2026-10-11T07:26:22.000Z");

function ev(ms: number, type: string, extra: Partial<BrEvent> = {}): BrEvent {
  return {
    seq: extra.seq ?? 1,
    ts: new Date(T0 + ms).toISOString(),
    source: extra.source ?? "watchdog",
    type,
    agent: extra.agent,
    case_id: extra.case_id,
    data: extra.data ?? {},
  };
}

describe("timeline", () => {
  it("compresses a gap longer than 20 seconds to 2 seconds", () => {
    const timeline = compileTimeline(
      [ev(0, "case.opened"), ev(25_000, "case.transition", { data: { to: "paused" } })],
      { compress: true, prerollMs: 0, realLimitMs: null },
    );
    expect(timeline.points[1].virt).toBe(2000);
    expect(timeline.skips[0].gap).toBe(25_000);
  });

  it("keeps short gaps intact and holds a hero window open", () => {
    const timeline = compileTimeline(
      [ev(0, "case.opened"), ev(5_000, "case.transition")],
      { compress: false, prerollMs: 1000, realLimitMs: 60_000 },
    );
    expect(timeline.points[0].virt).toBe(1000);
    expect(timeline.points[1].virt).toBe(6000);
    expect(timeline.duration).toBe(61_000);
    expect(timeline.skips).toHaveLength(0);
  });

  it("orders events by time, then source, then sequence", () => {
    const sorted = sortEvents([
      ev(0, "b", { source: "watchdog", seq: 2 }),
      ev(0, "a", { source: "arena", seq: 9 }),
      ev(0, "c", { source: "watchdog", seq: 1 }),
    ]);
    expect(sorted.map((event) => event.type)).toEqual(["a", "c", "b"]);
  });
});

describe("reduce", () => {
  it("opens a case, freezes on pause, cages, patches, and releases", () => {
    let state = initialState(["research_agent_1"]);
    const id = "case-1";
    state = reduce(state, ev(0, "case.opened", {
      agent: "research_agent_1",
      case_id: id,
      data: { trace_id: "abc", rule_hits: ["research_agent:empty_destination:notes_write"], cited: [] },
    }), 10);
    expect(state.cases[id].status).toBe("flagged");
    expect(state.agents.research_agent_1.trail).toBe("red");
    expect(state.agents.research_agent_1.redAt).toBe(10);

    state = reduce(state, ev(0, "case.transition", {
      agent: "research_agent_1",
      case_id: id,
      data: { from: "flagged", to: "paused" },
    }), 20);
    expect(state.agents.research_agent_1.frozenAt).toBe(20);

    state = reduce(state, ev(0, "case.transition", {
      agent: "research_agent_1",
      case_id: id,
      data: { from: "verified", to: "quarantined" },
    }), 30);
    expect(state.agents.research_agent_1.cagedAt).toBe(30);

    state = reduce(state, ev(0, "case.patched", {
      agent: "research_agent_1",
      case_id: id,
      data: { sha: "90a111f744065c8fa88d1d5ff62cb17f1914482d", paths: ["arena/guardrails/__init__.py"], checkpoint: "01M4" },
    }), 40);
    expect(state.barrier?.sha.startsWith("90a111f")).toBe(true);

    state = reduce(state, ev(0, "case.transition", {
      agent: "research_agent_1",
      case_id: id,
      data: { from: "confirming", to: "fixed" },
    }), 50);
    expect(state.cases[id].status).toBe("fixed");
    expect(state.agents.research_agent_1.releasedAt).toBe(50);
  });

  it("records a forbidden tool call as a trip without inventing one", () => {
    let state = initialState(["research_agent_1"]);
    state = reduce(state, ev(0, "agent.call", {
      source: "arena",
      agent: "research_agent_1",
      data: { tool: "notes_write", service: "notes", path: "/notes", planted: false },
    }), 5);
    expect(state.agents.research_agent_1.trips).toHaveLength(1);
    expect(state.agents.research_agent_1.trail).toBe("red");
  });
});

describe("re-runs", () => {
  it("gives a confirmation re-run its own agent and leaves the caged original alone", () => {
    let state = initialState(["research_agent_1"]);
    const rerun = { run_id: "deacfe2fcc6b" };
    state = reduce(state, { ...ev(0, "run.started", { source: "arena", data: { kind: "confirm", agents: ["research_agent_1"] } }), ...rerun }, 10);
    const id = "research_agent_1 · confirm deacfe";
    expect(state.agents[id].rerun).toMatchObject({ parent: "research_agent_1", spawnAt: 10, doneAt: null });

    state = reduce(state, { ...ev(0, "agent.blocked", { source: "agent", agent: "research_agent_1", data: { kind: "confirm", tool: "notes_write" } }), ...rerun }, 20);
    expect(state.agents[id].blockedAt).toBe(20);
    expect(state.agents[id].trail).toBe("ink");
    expect(state.agents.research_agent_1.blockedAt).toBeNull();

    state = reduce(state, { ...ev(0, "agent.call", { source: "arena", agent: "research_agent_1", data: { kind: "confirm", tool: "submit_answer", service: "data" } }), ...rerun }, 30);
    expect(state.agents[id].rerun?.doneAt).toBe(30);
    expect(state.agents.research_agent_1.trips).toHaveLength(0);
  });
});
