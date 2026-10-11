import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

// vite build empties ../site, so everything the page fetches has to live in public/.
const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));
const app = readFileSync(here("App.tsx"), "utf-8");
const fetched = [...app.matchAll(/fetch\("\.\/([^"]+)"\)/g)].map((match) => match[1]);

describe("public data", () => {
  it("finds the files the page fetches", () => {
    expect(fetched.length).toBeGreaterThan(0);
    for (const path of fetched) expect(existsSync(here(`../public/${path}`)), path).toBe(true);
  });

  it("ships a replay with agent calls, guardrail blocks and every case fixed", () => {
    const replay = JSON.parse(readFileSync(here("../public/replay/efd78263e6d2.json"), "utf-8"));
    const types = new Set(replay.events.map((event: { type: string }) => event.type));
    for (const type of ["run.started", "agent.call", "agent.blocked", "case.opened", "case.confirmed", "case.remembered"]) {
      expect(types.has(type), type).toBe(true);
    }
    const fixed = replay.events.filter((event: { type: string; data: { to?: string } }) => event.type === "case.transition" && event.data.to === "fixed");
    expect(fixed).toHaveLength(Object.keys(replay.cases).length);
  });
});
