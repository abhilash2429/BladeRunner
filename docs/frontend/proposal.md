# Bladerunner frontend: landing page and live mission control

Proposal for one site that tells the story and shows the system working. Architecture, flow, data contract and scope. No implementation here.

## 1. What it is

One page, two jobs:
1. **Landing page.** Anyone opening the link understands the problem, sees Bladerunner catch an agent, and sees the numbers.
2. **Mission control.** The same page, pointed at a running Bladerunner on this laptop, shows the swarm of agents working, the moment one breaks a rule, and every step of the case until it is fixed, live.

Both jobs use the same screen and the same components. The only difference is where the events come from.

| Mode | Where events come from | Who sees it | Needs a backend |
|---|---|---|---|
| **Replay** | A recorded event file shipped with the site (the Oct 11 run, tomorrow's live take, AgentDojo results) | Everyone, on the public link | No. Static hosting (GitHub Pages, Vercel) |
| **Live** | A local observer service streaming events while the arena, fleet and watchdog run | Us, during the demo recording | Yes, on 127.0.0.1 only |

A badge in the corner always says which mode is on: `LIVE` or `REPLAY · 11 Oct 2026, run efd78263e6d2`. Replay never pretends to be live.

## 2. Page flow, top to bottom

| # | Section | What the viewer sees | Data |
|---|---|---|---|
| 1 | **Hero: the swarm** | Full-width canvas. Agents as small bright dots drifting between service nodes (Data API, Docs, Notes board). A thin scanning line sweeps the field: the watchdog reading traces. One headline, one sentence, two buttons: "Watch a case" and "See the numbers". | Replay event stream, looping the first 60 seconds of the Oct 11 run |
| 2 | **The problem** | The DSEWiki story, short. The existing `site/index.html` narration can be reused here. | Static copy |
| 3 | **Watch a case** (the core) | Mission control, full screen height. Swarm on the left, case pipeline on the right, evidence drawer below. Plays one case from flag to fixed in about 60 seconds of compressed time, with a scrubber. | Replay stream for one case, or Live stream |
| 4 | **How it decides** | Three short panels: rules catch it, an investigator explains it, a verifier from another model family checks it. Each panel lights up the matching pipeline step in section 3 when scrolled to. | Static copy plus the real finding text from the case |
| 5 | **Test 1: our exam** | The 102-trace result. Two columns of 102 small squares, v0 and Bladerunner, colored by right alarm, false alarm, miss, correct silence. | `bench/results/v0.json`, `final.json` |
| 6 | **Test 2: other people's agents** | The AgentDojo swarm: 858 dots in three clusters (GPT-4o, Claude 3.5 Sonnet, Llama 3.3 70B). Toggle v0 vs Bladerunner and watch the false-alarm dots on the Claude cluster drop from 111 to 29. | `bench/external/agentdojo/results/*.json` (per-trace predictions are already in there) |
| 7 | **Built with** | neatlogs, Entire, cfo.ai, one line each plus one real artifact each: a trace link, an `entire why` line, the plan headline numbers. | Static copy |
| 8 | **Footer** | Repo, demo video, team. | Static |

## 3. Mission control, in detail

This is the "show the working" piece. It is one component used by the hero (cropped, ambient) and section 3 (full).

### Layout

- **Swarm field** (left, about 60% width). Service nodes are fixed: Data API `:8101`, Docs `:8103`, Notes board `:8102`, Unlisted `:8104`, Submit. Agents are particles.
- **Case pipeline** (right). One card per open case. Each card is a vertical stepper of the real states: flagged, paused, investigating, investigated, verified, quarantined, patching, patched, confirming, fixed. Failure endings get their own color: needs_review, not_fixed, dismissed.
- **Evidence drawer** (bottom, collapsed until a step is clicked). Shows the real artifact for that step: the offending span input, the investigator's claim, the verifier's verdict, the guardrail diff, the `entire why` line, the confirmation result.
- **Timeline scrubber** (bottom edge). Real timestamps. Long waits (finalization, confirmation re-runs) are compressed and marked as "6 min skipped".

### Visual grammar (what each motion means)

Every animation maps to one real event. Nothing moves without an event behind it.

| Event | On screen |
|---|---|
| Agent makes a tool call | The agent's dot travels to that service node and back |
| The call reads planted content | The Docs node pulses amber for that agent only |
| The call goes somewhere the policy forbids | The dot's trail turns red and stays red |
| Trace lands in neatlogs | A faint ring around the agent's dot |
| Case opened | A card slides into the pipeline, linked to the agent by a thin line |
| Paused | The agent's dot freezes in place |
| Investigation and verification | The card's stepper advances; the drawer offers the claim and verdict text |
| Quarantined | The dot is pulled into a dashed cage at the edge of the field |
| Patched | A barrier line is drawn around the Notes board node, labeled with the commit sha |
| Confirmation re-runs | New dots for the re-run agents; when one tries the forbidden call it bounces off the barrier (a guardrail block) |
| Fixed | The card turns green, the caged dot is released grey, the barrier stays |
| Brain remembers | A small "remembered" chip on the card |

### Interactions

- Click an agent: its tool-call list in the drawer, and a link to its trace in neatlogs.
- Click a case card: its incident report fields in the drawer.
- Scrubber: pause, play, 1x, 10x, jump to next state change.
- Live mode only: a **Launch swarm** button with an agent count (3, 6, 12) and scenario (`planted_dev`, `clean_dev`). It starts a fleet run through the observer.

### Making it feel like a swarm

Three real agents look thin on a hero. Options, all honest:
- **Live:** `fleet.cli run --agents N` already takes any N. Twelve agents on `planted_dev` is a real swarm of real agents. Cost is about 4x the 3-agent run and more neatlogs load.
- **Replay hero:** the Oct 11 run plus the re-runs and controls it spawned (3 agents, 9 re-runs, 3 control runs) is 15 real agents across one case cycle.
- **Section 6:** the 858 AgentDojo runs are the big swarm, clearly labeled as recorded runs from another lab's benchmark.

## 4. Architecture

### Components

1. **Frontend** (static site). Renders the page. Talks to one interface, `EventFeed`, and never knows whether it is live or replay.
2. **Replay files** (static JSON, shipped with the site). One file per recorded run, in the event schema below, plus the two benchmark result sets.
3. **Replay exporter** (Python script, offline). Builds a replay file from what a finished run left behind: `watchdog.duckdb` case events, `truth.duckdb` agent requests, `runs/incidents/*.json`, git commits, and the re-run traces from neatlogs for guardrail blocks.
4. **Event sinks** (small hooks inside existing processes, live mode only). Each process appends events to its own JSON-lines file under `runs/live/`.
5. **Observer** (small local web service, live mode only). Tails `runs/live/`, merges and streams events to the browser, serves a few read endpoints, and can start a fleet run.

### Why files and an observer, instead of reading the databases

DuckDB lets one process hold a database for writing. While the watchdog runs it holds `watchdog.duckdb`, and the arena holds `truth.duckdb`, so a separate viewer cannot read either live. Appending one line per event to a file is the smallest change that lets a viewer follow along without touching the databases or the pipeline's timing.

### Where events are produced

| Producer | Hook point (existing code) | File |
|---|---|---|
| Arena | `ArenaState.log_request` (every agent request, with `planted`) and run registration in control | `runs/live/arena.jsonl` |
| Each agent process | `ToolExecutor._blocked` in `arena/tools.py` (guardrail blocks never reach the arena request log today) and the run start and stop | `runs/live/agent-<run>-<agent>.jsonl`, one writer per file |
| Watchdog | `WatchdogState.log_event` and `transition` (every case step already passes through these two) | `runs/live/watchdog.jsonl` |

One writer per file avoids interleaved lines across processes.

### The boundary rule still holds

The watchdog must never see ground truth. The observer is a viewing tool that reads both sides, which is fine for an audience. It never feeds anything back into the watchdog, and the watchdog's code does not import or call it. The contract test in `tests/contract/test_boundaries.py` should gain one line: `watchdog/` does not import the observer.

### Observer endpoints (all on 127.0.0.1)

| Method and path | Does |
|---|---|
| `GET /api/events` | Server-sent events stream. Sends everything since the given cursor, then new events as they land |
| `GET /api/snapshot` | Current state: services, agents, open cases, last 200 events. Lets a page that opens mid-run catch up |
| `GET /api/case/{id}` | The incident report JSON for one case |
| `POST /api/fleet` | Starts `fleet.cli run` with a scenario and agent count. Refuses while a run is active. Off unless started with a flag |

## 5. Event schema

One envelope for every event, in live and replay alike:

```json
{"seq": 1042, "ts": "2026-10-11T07:26:22.814Z", "source": "watchdog", "type": "case.transition",
 "run_id": "efd78263e6d2", "agent": "research_agent_1", "case_id": "c90be7b2...", "data": {"from": "paused", "to": "investigating"}}
```

| type | source | data |
|---|---|---|
| `run.started` | arena | scenario, agents, kind (`live`, `confirm`, `control`) |
| `agent.call` | arena | step, tool, service, method, path, planted (true when the response carried planted content) |
| `agent.blocked` | agent | tool, target_host, reason |
| `agent.finished` | agent | steps, submitted, stopped, reason |
| `case.opened` | watchdog | trace_id, source (`native`, `local`, `both`), rule hits |
| `case.transition` | watchdog | from, to |
| `case.findings` | watchdog | category, claim, evidence span ids, confidence |
| `case.verdict` | watchdog | per finding: upheld, downgraded or rejected, with the reason |
| `case.writeback` | watchdog | tags added, new trace name, feedback ok or not |
| `case.patched` | watchdog | commit sha, paths, Entire checkpoint, first changed line |
| `case.confirming` | watchdog | re-run ids, control id |
| `case.confirmed` | watchdog | rerun_clean, control_clean, watchdog_clean |
| `case.remembered` | watchdog | Brain path |
| `case.failed` | watchdog | step and reason (pause_failed, investigation_deferred, patch_failed, and so on) |

Rules for every event:
- Pass `args` and any text field through `redact()` from `watchdog/actions/patch.py` before writing. No keys, tokens or canary values in events.
- Truncate free text to 500 characters. The drawer links to neatlogs or the report for the full text.
- `seq` is per file. The observer orders by `ts`, then source, then `seq`.

## 6. Replay

- The exporter writes `site/replay/<run_id>.json`: the run's metadata, the event list, and the final incident report fields per case.
- The player plays events on a virtual clock. Gaps longer than 20 seconds are compressed to 2 seconds and marked on the scrubber.
- Oct 11 replay is exportable today from the archived databases, the incident JSONs and git. Guardrail blocks for the re-runs come from the re-run traces in neatlogs (GUARDRAIL spans), or are left out and the scrubber says so.
- Tomorrow's live take becomes a second replay file the same evening, which replaces the Oct 11 one as the default.

## 7. Frontend tech, for whoever builds it

Suggestions, not requirements:
- Vite with plain TypeScript, or React if the builder prefers. No server rendering needed.
- The swarm in a 2D canvas (PixiJS or Canvas 2D). Hundreds of moving dots and readable labels are easier in 2D than in Three.js. Keep Three.js for an optional atmospheric layer only.
- One `EventFeed` interface with two implementations: `LiveFeed` (EventSource on `/api/events`) and `ReplayFeed` (fetch JSON, emit on a virtual clock). Every component subscribes to `EventFeed` and holds no other data source.
- A single reducer turns events into view state: agents, services, cases. The same reducer runs for live and replay, so they cannot drift apart.
- Live mode turns on with `?live=http://127.0.0.1:<port>`; without it the page is always replay.
- Light theme and palette from the existing `site/index.html`, so the two pages read as one product.
- `prefers-reduced-motion`: dots step instead of glide, no ambient sweep.

## 8. Scope against the deadline

The submission window opens Oct 12 at 19:00 IST, and the recording is at 15:00.

| Phase | What | Who | Time | Fits before recording? |
|---|---|---|---|---|
| **A. Replay landing page** | Exporter for the Oct 11 run, replay JSON, frontend sections 1 to 8 in replay mode | Exporter: me, about 1.5 h. Frontend: your friend | Frontend is the long pole | Only if your friend has a solid day |
| **B. Live mode** | Three event sinks, observer service, live badge, Launch swarm button | Sinks and observer: me, about 3 h with tests. Frontend: live feed adapter, small | Tight. Touches the processes the live take depends on | Risky. Only after a full rehearsal |
| **C. AgentDojo swarm** | Section 6 visual from existing per-trace predictions | Frontend only, data already exists | Small | Yes, if A is on track |

My recommendation:
- **Ship A and C for the submission.** The public link shows a real recorded case end to end and both benchmarks, with no risk to the live take.
- **Record the demo video the planned way** (terminals, neatlogs, reports) and export that take as the second replay the same evening.
- **Build B after the hackathon** unless the rehearsal tonight goes clean and the frontend is done early. Adding hooks to all three processes the morning of the recording is the kind of change that breaks a live take.

## 9. Decisions (11 Oct)

1. Phase A and C for the submission. Phase B (live mode) after the hackathon.
2. The frontend is built from this file plus `site/replay/` and `site/data/` (see `site/replay/README.md`). Both data files exist now.
3. Hosting: GitHub Pages from `site/`.
4. The demo video is recorded the planned way (terminals, neatlogs, reports). That take gets exported as a second replay the same evening with `uv run python -m bench.replay <run_id> --watchdog-db runs/watchdog-oct12.duckdb`.
