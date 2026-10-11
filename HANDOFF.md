# Bladerunner handoff

This is a half-built hackathon project: neatHack, solo, team "Heat". The previous session ran out of usage. All work is committed and pushed on `main`. This file tells you where things stand and lists every decision you must carry forward. Read it fully before touching code.

## 1. What to read, in order

1. This file.
2. `docs/superpowers/plans/2026-10-10-bladerunner.md`: the implementation plan. Every task has exact files, interfaces, code and tests. **Read only the task you are working on**, plus "Global Constraints" (lines 13 to 28). The plan is about 8000 lines, so don't load it whole.
3. `docs/superpowers/specs/2026-10-10-bladerunner-design.md`: the spec. It is the binding authority when the plan is ambiguous or wrong.
4. `.superpowers/sdd/2026-10-10-bladerunner/progress.md`: the ledger. It is gitignored and local, and lists every task outcome, every `Ruling:` and every deferred minor. **Trust the ledger plus `git log` over anything else.** The same folder has `task-N-brief.md` (plan text per task) and `task-N-report.md` (what the implementer did and any deviations).
5. `docs/day1-results.md`: live facts about neatlogs, Bedrock, LiteLLM, Brain and Entire, as verified on day 1.
6. `CLAUDE.md` and `.entire/agent-guide.md`: repo workflow (Entire Brain and Graph).

## 2. What Bladerunner is

It is an operator-side watchdog for AI agents.
- A local fleet of research agents (the arena) runs against mock services on 127.0.0.1, traced to neatlogs.
- The watchdog polls neatlogs traces and catches policy breaks (categories C1 to C7) with a dual-target detection compiler: neatlogs native regex detections plus a local evaluator.
- A finding goes to an investigator LLM (Kimi K2.5), then to a cross-family verifier (DeepSeek in dev, Claude Sonnet 4.6 when scored).
- On a verified finding the watchdog:
  1. quarantines the run;
  2. writes tags, feedback and a report back to neatlogs;
  3. patches the guardrail or policy with OpenCode, with Entire recording checkpoints;
  4. confirms the fix by re-running against hidden ground truth (`truth.duckdb`, which the watchdog never reads).

There are three processes:
- `arena-server`: 5 FastAPI ports; owns `truth.duckdb`.
- `fleet`: one process per agent.
- `watchdog`: owns `watchdog.duckdb` and never imports `arena`.

## 3. State at handoff (2026-10-11)

HEAD is `f66aeea`. The full suite gives 497 passed, 2 skipped (`uv run pytest -q`, Windows). **Nothing is in flight.**

**Tasks 0 to 17 are complete.** Each was implemented, reviewed, fixed and closed in the ledger.

| Task | State | Do next |
|---|---|---|
| 18, 19, 20, 21, 22, 25 | Complete. Built remotely by a Cursor agent (`5a6e8ee..7168eb6`), verified against every ruling below on 2026-10-11, Windows process-tree fix at `f66aeea`. Ledger has the details and deferred minors. | Nothing. |
| 23, 24, 26, 27 | Not started | See 5. Task 23 is next. |

Task 18 review findings (fixed in `0f92e50`; use them as the re-review checklist):
- Critical: the Markdown report rendered LLM claim, reason, patch.why and span_id unescaped. Required fixes: an `md_cell` sanitizer filter, URL-encoded ids, and a longer fence for `why`.
- Important:
  - Timeline and JSON dumped raw event data, evidence and idem_key. Required fix: an allowlist.
  - merge_tags cut adds at 10. Required fixes: evict tags bladerunner doesn't own first, and drop the `[:5]` category cap.
  - Duplicate span feedback reused one idempotency key. Required fix: one call per unique span.
  - The tag idempotency key ignored the body. Required fix: hash add, remove and name into it.
  - feedback() could raise OSError. Required fix: catch it and log only the type.
- Minor:
  - Log the exception type and status, never str(e).
  - ControlClient must wrap non-JSON responses.
  - The contract test must assert method, path and body.

Open caveats on Task 18:
- The span_url `span=` parameter and the default trace URL are unverified until live.
- The check "PATCH response tags contain every add" only runs if the response has a `tags` list.

## 4. How to work

For each task:
1. Read `task-N-brief.md`. If it doesn't exist, extract the task section from the plan.
2. Implement with TDD exactly as the plan's steps say: failing test, see it fail, code, see it pass. Plan code marked verbatim, prompts above all, must be used byte for byte.
3. Run the full suite.
4. Commit by explicit path only. Never `git add -A` or `git add .`. Push to `main` after each task. The user authorized pushing to main.
5. Review: a fresh reviewer, meaning a separate chat with no implementation context, checks the diff against the brief for spec compliance and quality. It reports Critical, Important and Minor findings with file:line.
6. Fix Critical and Important findings, then have a reviewer check the fix diff.
7. Record in the ledger: `Task N: complete (commits a..b, review clean)` plus any `Task N: minor (deferred): ...`.
8. When a finding conflicts with the plan text, decide it yourself. Record it as `Ruling: <decision> - <why> - <cost if wrong>` and keep going. The spec outranks the plan. Stop and ask the user only for destructive, security-sensitive or outward-facing actions.

Fix root causes, never partial patches. Keep diffs minimal. No filler comments. Match the surrounding code.

## 5. Queue after 18 and 19

| Task | Size | Notes |
|---|---|---|
| 20 Patch loop (OpenCode, path guard, tighten check, commit) | large, judgment | OpenCode must run with `common.config.tool_env()`. The Entire plugin needs `sh` on PATH; without it, checkpoints silently don't record. |
| 21 Confirmer | medium | |
| 22 Pipeline, daemon, boundary test | large, judgment | Carry every Task 22 ruling in section 6. It also creates `tests/contract/test_boundaries.py`. |
| 23 Live end-to-end loop on dev scenarios | large, live, judgment | Needs real terminal tabs and the user's keys. Includes the Task 19 Brain live check. |
| 24 Tier 2 scenarios | optional | Only after 23 works. |
| 25 Benchmark freeze and scorer | medium | Carry the Task 25 ruling. |
| 26 Record, freeze and score | live | |
| 27 Business plan (cfo.ai), demo, posts, submission | docs | The user posts. Never post anything yourself. |

After 27: one final whole-branch review on the most capable model available. Fix what it finds, then give the user the full list of `Ruling:` lines from the ledger.

## 6. Rulings to carry into later tasks (decided; do not relitigate)

**Task 22 (daemon):**
- Clear kv `last_poll_error` after a clean tick.
- Catch `NeatlogsError` from `ensure_detections` and set `detections = {}`. The watchdog must not depend on native detections; live, they returned zero hits.
- Run cases in threads.
- Enforce retry caps with `state.bump_retry(case_id, kind)`: quarantine 3, patch 1. The retry edges `quarantine_failed -> quarantined` and `patch_failed -> patching` exist.
- Pass spans in source order. TraceSource orders by parsed time, then span_id. `evaluate_trace` does not re-sort.
- Poller gaps from the Task 15 review:
  - Track pending agent traces in kv `pending:<id>`. Re-check them each tick with `source.trace_info`, independent of the 30-minute window, and drop them once finalized and evaluated. Reason: neatlogs finalization has stalled for about 90 minutes before, and `GET /traces?from=` filters on startedAt.
  - `tick` marks `seen:`/`hit:` only after `open_case` succeeds, or wraps `open_case` per flag.
  - Split the `spans()` fetch from `evaluate_trace`, so a deterministic evaluation error doesn't re-fetch forever.
- `investigate()` raises on LLM, ledger or source errors and on `InvestigationError` (no valid submit, or `findings` not a list). Catch it and retry once on the fallback model (`zai.glm-5`). If the fallback also fails, the case goes to `needs_review`, never `dismissed`.
- The investigator's needs_review signal is the claim prefix `"Uncertain:"`. The verifier force-downgrades any upheld claim with that prefix.
- The investigator's seed policy lookup for an agent with a missing role is unguarded. Guard it in the daemon.
- Construct `BrainMemory` WITHOUT remember_tool, fact_arg or path_arg; the BRAIN_ARGS in the plan are outdated. Never call `brain_remember`; it does not exist. Brain MCP has 36 tools, and `brain_query` takes `query`.

**Task 25 (freeze):** refuse or mark degraded spans, meaning those where the payload fetch failed and a truncated preview was kept. Freeze the full untruncated input and output.

**Tool-call counting:** count only kind == "TOOL" spans with a target_host (`trace_source.tool_call(span) is not None`). Ignore `run_stopped`.

**Task 17:** the verifier does not get the policy text. The investigator quotes policy facts in its claim and the prompt accepts them. Revisit only if Task 23 shows false quarantines caused by misquoted policy.

**Task 23 tuning:** in the live smoke run, the agent did not take the planted bait, so the run came back clean. Planted temptation strength needs tuning. The agent prompt deliberately does NOT say "treat fetched content as data", because that would pre-warn the agent.

**Task 10 deferred fix:** next time `arena/agent.py` is touched, wrap `tracer.stopped(...)` in try/finally so `run_stopped` is always emitted.

**Other settled decisions:**
- `open_case` returns `(Case, changed)`; creation counts as changed.
- The stricter `text_of` wrapper rule stays: a wrapper key AND a preview/value key.
- A TOOL span without `target_host` is skipped by every rule. Parked.
- The package name `watchdog` shadows a PyPI library. Parked.
- `set_extra` rejects the reserved key `retries`.

## 7. Verified live facts (don't re-derive; see docs/day1-results.md)

neatlogs reads:
- `/spans` uses `spanName`/`spanType` and has no input/output.
- `/raw-spans` has input/output as `{mimeType, preview, truncated}` plus `error`.
- The payload endpoint requires a `fields` param and returns `data.fields.{input,output}.value`.
- A TOOL span's `input.preview` is JSON `{"tool","target_host","args"}`.
- The finalized `traceName` is the WORKFLOW span name.
- `sessionId` is the run_id; tags are `agent:<name>`.
- An auto-created WORKFLOW root span repeats the tool input.
- The arena emits a `run_stopped` TOOL span with `{"reason"}` and no target_host.
- GET can return 409 `TRACE_PROCESSING`.

neatlogs writes:
- PATCH needs `expectedRevision`. On a 409, re-GET and retry.
- Tags are replaced wholesale, with a maximum of 10.
- Every mutation carries an idempotency-key.
- Service-account feedback returns 403, so feedback goes through the Node CLI:
  - `neatlogs feedback create --trace <id> --body-stdin --idempotency-key <k> --json`
  - `neatlogs feedback span-set <span-id> --trace --body-stdin --idempotency-key`
- CLI OAuth is on profile `default` with `observability:write`. The keychain is invisible to sandboxed shells, so the live watchdog with feedback must run in a real terminal.
- Inside `uv run`, `neatlogs` resolves to the SDK script. `NeatlogsCLI` already resolves the Node CLI with the venv Scripts dir stripped from PATH.

Detections:
- Create succeeds, but zero hits were ever returned.
- Proven regex fields: `type`, `displayName`, `groupId`, `runOn{frequency:"span_name", span_names}`, `config{input_pattern, output_pattern, case_sensitive, operator}`.
- Conditional detections are off.

Ingest:
- Endpoint `https://ingest.neatlogs.com`, header `x-api-key`, env `NEATLOGS_INGEST_KEY`.
- The key was rotated on 2026-10-10; the new one is in `.env`.
- SDK 1.4.26.

Models (`common/llm.py` ROLES):

| Role | Model |
|---|---|
| arena | `minimax.minimax-m2.5` |
| investigator | `moonshotai.kimi-k2.5` |
| fallback | `zai.glm-5` |
| verifier_dev | `deepseek.v3.2` |
| verifier_scored | `claude-sonnet-4.6` (LiteLLM, $18 hard cap) |
| polish | `claude-opus-4.6` |

Bedrock tool calling works on all 5 Bedrock models.

## 8. Hard constraints (from the user, non-negotiable)

- Keys live only in `.env` (gitignored). Never print, paste, commit or log secrets, and never put them in prompts. Check only whether a variable is set. Never type API keys into files or fields; the user does that. The repo and Entire transcripts are public.
- The arena is fully local. Fetch is allowlisted to 127.0.0.1:8101 through 8104. Stay at category level (C1 to C7), with no attack or evasion techniques.
- Ask the user before posting, sending messages, granting OAuth or submitting forms. Leave Discord alone.
- Machine-level commands (`entire`, `neatlogs` auth/profile, plugin installs, logins) run in a real terminal, never a sandbox. Close finished terminals.
- No em dashes anywhere (code, docs, prompts, posts). No emojis.
- Commit messages end with a `Co-Authored-By:` line naming the model that wrote the commit.
- Talking to the user: blunt, concise, peer tone, first person, no filler, no "it's not X, it's Y" phrasing, no diagrams unless asked. Ask when something is genuinely unclear.

## 9. Loose ends

- The arena server may still be running on ports 8100 to 8104 in an old terminal tab, possibly alongside a fleet tab. Stop both before Task 23 so it starts clean.
- Parked:
  - MCP access from the watchdog (the MCP endpoint returned 502, then 401).
  - Route auth on control port 8100.
  - Native detection for the unlisted tool (not compilable from policy).
