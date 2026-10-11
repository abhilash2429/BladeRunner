# Bladerunner live-demo recording runbook (draft 1)

Recording window: Oct 12, 15:00 to 18:00 IST. The submission window opens at 19:00, and voiceover plus editing need the tail of this window, so live recording ends by 16:45 at the latest.

Goal: one continuous screen recording of a `planted_dev` run going from violation to `fixed`, plus the static captures listed in `docs/demo/video-script.md` (assets A1 to A24). Fallback footage from the three Oct 11 cases gets captured first, so the video can ship even if the live take fails.

Commands come from `README.md` (Running it), `HANDOFF.md` and the argparse definitions in `arena/server.py`, `watchdog/daemon.py`, `fleet/cli.py` and `bench/*.py`. Every `entire` and `neatlogs` command is one the repo docs already use (`docs/neathack-context.md`, `.entire/agent-guide.md`, `HANDOFF.md`). Their flags can't be checked against source, so run `--help` off camera before relying on one. PowerShell helpers (`Get-NetTCPConnection`, `Select-String` and so on) are standard cmdlets.

## Schedule

| IST | Block |
|---|---|
| 15:00 to 15:25 | Pre-flight checklist (below), all of it off camera |
| 15:25 to 15:40 | Capture fallback and static footage that needs no live run (section "Fallback footage", plus A4, A6, A10 to A12, A16) |
| 15:40 | Start the live take |
| 16:10 | Decision gate: if no case has reached `quarantined`, stop waiting and use fallback footage for B3 to B9 |
| about 16:00 to 16:30 | Cases reach `fixed`, then post-loop captures |
| 16:30 to 16:45 | cfo.ai capture, if the plan exists by then |
| 16:45 to 18:00 | Voiceover and edit |

## Pre-flight checklist

Do all of this off camera. Tick every line.

**Required**
- [ ] **No Claude Code session active in the repo.** Entire attributes lines to whichever agent session is active, and on Oct 11 a concurrent Claude Code session claimed OpenCode's patch line. Quit every Claude Code CLI and Claude desktop session whose working directory is this repo, including the session that coordinated this script. Then check that `Get-Process claude -ErrorAction SilentlyContinue` returns nothing. If the process name differs on this machine, check Task Manager.
- [ ] **`neatlogs auth login` done.** Run it in a plain terminal, outside `uv run`, because inside `uv run` the `neatlogs` name resolves to the Python SDK script. Then run `neatlogs auth status`. The Oct 11 span feedback failed because this login had expired.
- [ ] **No `.env` or keys on screen, ever.** Close any editor tab showing `.env`. Collapse the editor's Explorer sidebar. Do not run `env`, `set`, `Get-ChildItem env:` or `cat .env`. Do not open the neatlogs API key or service account pages, the AWS console, or the LiteLLM dashboard. Do not add a `.vscode/settings.json` to hide files: it dirties the tree and blocks patching (next item).
- [ ] **Close unrelated terminals,** including any old `arena.server` or fleet tabs from Oct 11.

**Repo state (the patcher refuses to run on a dirty tree)**
- [ ] `git status --porcelain` shows only paths under `runs/`, `bench/results/` or `bench/frozen/`. The patcher returns `working tree not clean before patch` for anything else, so every case would end `patch_failed`. As of this draft the tree also has: modified `README.md`, untracked `site/`, untracked `docs/demo/` (these two files), and untracked `bench/frozen.tmp-61ioqsor/` (it does not match the `bench/frozen/` prefix). Commit them or move them out of the repo before the take. That's your call.
- [ ] `git diff --stat a6f11b7 HEAD -- arena/guardrails policy` prints nothing, meaning the guardrails and policy are at the Task 23 reset base.
- [ ] `Get-Content arena\guardrails\__init__.py` shows `check_call` returning `None`. Capture this on camera as the "before" guardrail (B8 diff context).
- [ ] `uv run pytest -q` passes. Live tests are excluded by default and no LLM is called.

**Environment**
- [ ] Ports 8100 to 8104 are free: `Get-NetTCPConnection -State Listen -LocalPort 8100,8101,8102,8103,8104 -ErrorAction SilentlyContinue` prints nothing.
- [ ] `Get-Command entire, opencode, neatlogs` finds all three.
- [ ] The required variables are set. This prints names and True/False only. Run it in a pane, then `Clear-Host`:
  `uv run python -c "from common.config import load_env; import os; load_env(); print({k: bool(os.environ.get(k)) for k in ['NEATLOGS_INGEST_KEY','NEATLOGS_TOKEN','NEATLOGS_PROJECT_ID','NEATLOGS_DETECTION_GROUP_ID','AWS_BEARER_TOKEN_BEDROCK','AWS_REGION']})"`
  Add `LITELLM_API_KEY` only if you run with `--verifier scored`.
- [ ] `entire status` shows Entire enabled for the repo. `.opencode/plugins/entire.ts` exists. Without it, OpenCode patches get no checkpoint.
- [ ] **Quiet window:** no fleet run in the 30 minutes before the watchdog starts. A fresh watchdog DB sweeps the last 30 minutes of traces, and stale runs open extra cases. On Oct 11 those extra cases drove the neatlogs 429s and `quarantine_failed` cases.
- [ ] Off camera, read `entire brain --help` and find the retrieval command for S5. The agent guide calls it recall; confirm the exact syntax.

**Recording setup**
- [ ] OBS at 1920x1080, 30 fps, recording to MKV (crash-safe), remuxed to MP4 afterwards. One continuous recording for the whole take.
- [ ] Windows notifications off (Do Not Disturb). Slack, mail, Discord and other apps closed.
- [ ] Browser: a clean profile or window with only the neatlogs app (logged in) and later cfo.ai. Bookmarks bar hidden. Zoom 125%.
- [ ] Terminal: dark theme, 18 pt font, each pane titled (ARENA, WATCHDOG, FLEET, INSPECT).
- [ ] A paper or second-screen event sheet ready (template at the end).

## Terminal layout

One Windows Terminal window, four panes in a 2x2 grid:

| Pane | Position | Runs |
|---|---|---|
| ARENA | top left | `arena.server`, silent by design (uvicorn log level warning) |
| WATCHDOG | top right | `watchdog.daemon`, silent by design: it prints only `tick failed: <Type>` on an error |
| FLEET | bottom left | `fleet.cli run` |
| INSPECT | bottom right | status board loop, `git`, `entire` |

VS Code holds the repo with Markdown preview for `runs/incidents/<case>.md`. The browser holds neatlogs. Since the watchdog pane prints nothing, progress is visible in three places: the INSPECT status board, the incident report preview, and the neatlogs trace list.

## Live take

Start OBS recording and note the IST time on the sheet.

**L1. ARENA pane** (expect: no output)

```powershell
uv run python -m arena.server
```

Check from INSPECT: `Get-NetTCPConnection -State Listen -LocalPort 8100,8101,8102,8103,8104` shows five listeners. Do not restart the arena during the take. Pause and stop flags live in memory, and a restart makes every stop for the current run return 404, which ends the case as `quarantine_failed`.

**L2. WATCHDOG pane** (expect: no output)

```powershell
uv run python -m watchdog.daemon --db runs\watchdog-oct12.duckdb
```

The fresh DB keeps old Oct 11 cases out of the take. Default flags give the dev verifier (DeepSeek V3.2, which the VO names), auto-patch on and CLI feedback on. This pane has to be a real terminal, or the neatlogs CLI can't read its login from the keychain. Wait 15 seconds. If `tick failed` repeats, go to F3.

**L3. INSPECT pane: status board**

```powershell
$start = Get-Date
while ($true) { Clear-Host; Get-Date -Format "HH:mm:ss"; git log --oneline -4; Get-ChildItem runs\incidents\*.md | Where-Object { $_.LastWriteTime -gt $start } | ForEach-Object { "{0}  {1}" -f $_.Name.Substring(0,8), (Select-String -Path $_.FullName -Pattern '^- Status: ' | Select-Object -First 1).Line }; Start-Sleep 5 }
```

The board shows the clock, the newest commits, and one status line per case report written during the take. It stays on screen for the whole take and gives the editor the time-lapse source.

**L4. FLEET pane** (about 1 minute)

```powershell
uv run python -m fleet.cli run --scenario planted_dev --agents 3
```

Expect one line, `{"run_id": "<12 hex>", "agents": ["research_agent_1", "research_agent_2", "research_agent_3"]}`, then the process exits once all three agents finish. On Oct 11 the run took 66 seconds (12:38:23 to 12:39:29). Write the run_id and time on the sheet. Capture: A3.

**L5. Browser: wait for neatlogs to finalize** (normally under about 3 minutes; it has stalled for 90 minutes before)

Refresh the neatlogs Traces page until three `arena-run` traces for this session show as finalized. Open each one. The violators have an executed `notes_write` TOOL span with `target_host` `127.0.0.1:8102`. Capture A5 now, with the span selected and its input visible. Do not zoom into the docs page body that holds the planted instruction.

Wait budget: 10 minutes after the fleet exits. Then go to F1 or F2.

**L6. Flag to quarantine** (about 35 to 65 seconds per case)

A report file appears on the status board with `- Status: quarantined` once a case is verified. The report is first written at quarantine, so earlier states don't show on the board. Open the report in VS Code preview. Capture A7 (header: Detected by, Flagged, Paused, Quarantined) and A8 (findings table). Then in neatlogs, capture the trace list with the renamed trace `[BLADERUNNER C1] research_agent_N` and its tags (A13), plus the evidence span's feedback (A14).

**L7. Patching** (about 1 to 2 minutes per case; patches run one at a time)

The status board's `git log` gains `fix(guardrail): incident <case_id> (C1)` commits. Capture `runs\fixreq\<case_id>.md` (`## Past incidents` and `## Rules`) for A15. If Past incidents lists the Oct 11 incidents, that is the Brain recall shot. Hold it for 3 seconds.

**L8. Confirming** (about 6 to 10 minutes; record it, the edit time-lapses it)

The arena re-runs the scenario three times plus one `clean_dev` control for each case. `runs\confirm_logs\` gains one log per run. While waiting, capture the watchdog's own trace in neatlogs (workflow `bladerunner-watchdog`, trace `bladerunner-case`, session = case id) for A9.

**L9. Fixed**

Status lines turn to `- Status: fixed`, and the neatlogs tag `fixed` replaces `quarantined`. Write the IST time per case on the sheet. On Oct 11, flag to fixed took 8 to 13 minutes per case, so plan for 12 to 20 minutes from L4 to the last `fixed`.

## Post-loop captures

Run these in INSPECT after stopping the status loop with Ctrl+C. Keep OBS running.

- **S1.** Each live report in preview: Timeline, Fix and Confirmation sections (A7, A20).
- **S2.** The patch commit, header and trailers first, then the guardrail diff (A17):
  ```powershell
  git show --stat <sha>
  git show <sha> -- arena/guardrails/__init__.py
  ```
- **S3.** Provenance (A18). Take the file and line from the report's "Line N in arena/guardrails/__init__.py":
  ```powershell
  entire why arena/guardrails/__init__.py:<line>
  ```
  Use it only if the line reads `by OpenCode · zai.glm-5`. If it names Claude Code, go to F9.
- **S4.** `entire checkpoint explain <checkpoint>` and `entire checkpoint list`. Preview them off camera first, because the explain output carries session content.
- **S5.** Brain recall of the incident fact (A21), with the command confirmed in pre-flight. Query on the category and tool, for example `C1 notes_write`.
- **S6.** Recovery evidence from Oct 11 (A10, A12). The output holds trace and span ids, model names and token counts only:
  ```powershell
  Select-String -Path runs\ops.log -Pattern '1e7a3d7de3554526e2f9a967d48f2222' | Select-Object -First 6
  Select-String -Path runs\spend.jsonl -Pattern '37fa7776126c46358bae53184c3bb1e7'
  ```
- **S7.** Before and after (A19). List the confirmation run ids, then open one re-run trace for a fixed agent in neatlogs next to its violating trace:
  ```powershell
  Get-ChildItem runs\confirm_logs | Sort-Object LastWriteTime -Descending | Select-Object -First 8 Name, LastWriteTime
  ```
  Each log name is a confirmation run_id, which is the neatlogs session id of that run's traces.
- **S8.** Optional Entire Graph shot, using the form documented in `.entire/agent-guide.md`:
  ```powershell
  entire graph query --repo . --profile full --query "where does a guardrail block a tool call"
  ```

Then stop the services: Ctrl+C in WATCHDOG, then in ARENA. With the arena stopped, so `truth.duckdb` is no longer held open, run the time-to-detect table for the optional [TTD_VIOLATION_TO_PAUSE] stat:

```powershell
uv run python -m bench.live_ttd
```

It reads every report in `runs\incidents`. Use the rows for today's case ids.

## Failures and what to do

| # | Symptom | What it means | Action |
|---|---|---|---|
| F1 | No finalized traces 10 minutes after the fleet exits | neatlogs ingest or finalization stall | Keep recording until the 16:10 gate, then use the fallback footage for B3 to B9 |
| F2 | Traces finalized, no `notes_write` span, no case | The agents did not take the planted bait (this happened on Oct 10) | Run L4 again once. If that is clean too, use fallback |
| F3 | `tick failed: <Type>` repeating in WATCHDOG | Poll or rule error | Note the type. Ctrl+C and rerun L2 with the same `--db` (state resumes from the DB). If it persists, use fallback |
| F4 | A case ends `needs_review` | Both investigators failed, or the verifier downgraded | Capture it. It is a real recovery shot for B6. Continue with the other cases |
| F5 | `patch_failed` | Dirty tree, a guard rejection, or failed guardrail tests. The patcher retries once | Read the reason in the report's Fix section. If it is `working tree not clean before patch`, the pre-flight was missed; use fallback for B8 and B9 |
| F6 | `quarantine_failed` | The stop call failed, usually because the arena restarted | Don't restart the arena mid-take. Use fallback for B7 to B9 |
| F7 | `not_fixed` | Confirmation found a violation, or the control failed | Capture it as-is. Use fallback for B9 |
| F8 | Report timeline shows `feedback_trace (ok=False)` | neatlogs CLI login problem | Tags and the rename still land. Use the B7 fallback VO line |
| F9 | `entire why` names Claude Code | A Claude Code session was active | Use the 37fa7776 `entire why` from its report for B8 |
| F10 | Bursts of `backoff 429` in `runs\ops.log` | neatlogs rate limits under concurrent cases | Expected with three cases. Let it run. A primary-to-fallback switch here is good B6 footage |

## Fallback footage (Oct 11, run `efd78263e6d2`, `planted_dev`)

Capture these in the 15:25 block, before the live take.

| Case | Agent | Trace | Patch | Checkpoint | Flagged to fixed | Best for |
|---|---|---|---|---|---|---|
| c90be7b2 | research_agent_1 | https://app.neatlogs.com/traces/15a494c98ebff17ddbd18fb332a231c9 | 90a111f | 01M4MXS01HJGEQD0R2NVQYCD1J | 12:56:22 to 13:04:26 | B3 to B5, B7, B9 (`Detected by: both`, the cleanest timeline). Never show its `entire why` block |
| 1ee2425c | research_agent_3 | https://app.neatlogs.com/traces/dc50c7c76c7f2e98d297f0ace6a0ead0 | 8fcd6ae | 01M4MXT5SDHXXSAKYWFF8902DT | 12:56:22 to 13:08:56 | Spare. Its `entire why` reads `[MX] by OpenCode` |
| 37fa7776 | research_agent_2 | https://app.neatlogs.com/traces/1e7a3d7de3554526e2f9a967d48f2222 | 4addfab | 01M4MXVFHEZ9B2H27676DDDATN | 12:56:23 to 13:09:21 | B6 (429, then fallback GLM-5, then fixed) and B8 (`[AI] by OpenCode · zai.glm-5`, C1 plus C3) |

Fallback capture list:
1. Each trace page in neatlogs: span tree, the `notes_write` span input, the name and tags as they stand now.
2. `runs/incidents/c90be7b237f54b128327e5a1ead3ceec.md` and `runs/incidents/37fa7776126c46358bae53184c3bb1e7.md` in preview.
3. `runs/incidents/58ded4910f3c45f98456ec6e79acca13.md` timeline (two `investigation_failed (status 429)` events, then `needs_review`) for the last line of B6.
4. `git show 4addfab` (the commit stays in history after the revert).
5. `runs/fixreq/37fa7776126c46358bae53184c3bb1e7.md` `## Rules`.
6. S6 (ops.log and spend ledger).
7. The `bladerunner-case` watchdog trace for session `c90be7b237f54b128327e5a1ead3ceec` in neatlogs.

The Oct 11 patches were reverted, so `entire why` on today's `arena/guardrails/__init__.py` won't show them. For B8, use the `entire why` block stored in the 37fa7776 report, or `entire checkpoint explain 01M4MXVFHEZ9B2H27676DDDATN` after an off-camera preview.

## Teardown

1. After every case is terminal (`fixed`, `not_fixed`, `needs_review`), stop WATCHDOG, then ARENA, with Ctrl+C.
2. The take leaves `fix(guardrail)` commits on local `main`. On Oct 11 they were reverted so the benchmark scores against a clean arena. Revert or keep is your call. Don't push until you decide.
3. Keep everything under `runs/`. It is the evidence.
4. Close all four panes.

## Event sheet

| Event | IST time | Notes |
|---|---|---|
| OBS start | | |
| L4 fleet start, run_id | | |
| Traces finalized | | |
| Case 1: id, quarantined, patched (sha), fixed | | |
| Case 2: id, quarantined, patched (sha), fixed | | |
| Case 3: id, quarantined, patched (sha), fixed | | |
| Any `*_failed` event (for B6) | | |
| OBS stop | | |
