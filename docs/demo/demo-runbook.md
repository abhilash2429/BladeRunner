# Bladerunner live-demo recording runbook (draft 3)

Recording window: Oct 12, 15:00 to 18:00 IST. The submission window opens at 19:00, and voiceover plus editing need the tail of this window, so live recording ends by 16:45 at the latest.

Goal: one continuous screen recording of a `planted_dev` run with three agents, going from violation to `fixed`. Plus the static captures listed in `docs/demo/video-script.md` (assets A1 to A24). Fallback footage from the three Oct 11 cases gets captured first, so the video can ship even if the live take fails.

Where the commands come from:
- `README.md` (Running it), `HANDOFF.md`, and the argparse definitions in `arena/server.py`, `watchdog/daemon.py`, `fleet/cli.py` and `bench/*.py`.
- `entire brain recall` syntax: lead's ruling.
- Other `entire` and `neatlogs` commands: the forms the repo docs already use.
- PowerShell helpers: standard cmdlets.

Framing rule for everything captured: Bladerunner acts on finalized traces. A pause or stop lands after the agent's run has ended. The guardrail is what stops the behavior on the next run.

## Schedule

| IST | Block |
|---|---|
| 15:00 to 15:25 | Pre-flight checklist, all off camera |
| 15:25 to 15:45 | Capture the fallback and static footage that needs no live run (section "Fallback footage", plus A4, A6 and A16), and the cfo.ai capture C1 |
| 15:45 | Start the live take |
| 16:10 | Decision gate. If no case has reached `quarantined`, stop waiting and use fallback footage for B3 to B5, B7 and B9 |
| about 16:00 to 16:30 | Cases reach `fixed`, then the post-loop captures |
| 16:30 to 16:45 | Buffer: a second fleet run (F2), or retakes of any capture |
| 16:45 to 18:00 | Voiceover and edit |

## Pre-flight checklist

Do all of this off camera. Tick every line.

**Required**
- [ ] **No Claude Code session active in the repo.** Entire attributes lines to whichever agent session is active. On Oct 11, a concurrent Claude Code session claimed an OpenCode patch line.
  - Quit every Claude Code CLI and Claude desktop session whose working directory is this repo, including the session that coordinated this script.
  - Then check that `Get-Process claude -ErrorAction SilentlyContinue` returns nothing. If the process name differs on this machine, check Task Manager.
- [ ] **`neatlogs auth login` done.** Run it in a plain terminal, outside `uv run`: inside `uv run`, the `neatlogs` name resolves to the Python SDK script. Then run `neatlogs auth status`. The Oct 11 span feedback failed because this login had expired.
- [ ] **No `.env` or keys on screen, ever.**
  - Close any editor tab showing `.env`, and collapse the editor's Explorer sidebar.
  - Do not run `env`, `set`, `Get-ChildItem env:` or `cat .env`.
  - Do not open key, service account or billing pages: neatlogs, AWS, LiteLLM, or cfo.ai Settings and Billing (a card is on file there).
  - Do not add a `.vscode/settings.json` to hide files. It dirties the tree (next item).
- [ ] **Close unrelated terminals,** including any old `arena.server` or fleet tabs from Oct 11.

**Repo state**
- [ ] **`git status --porcelain` is clean outside `runs/` and `bench/`.** The patcher ignores only untracked paths under `runs/`, `bench/results/` and `bench/frozen/`. Anything else returns `working tree not clean before patch`, and every case ends `patch_failed`. That includes a stray `bench/frozen.tmp-*` directory, which does not match `bench/frozen/`.
- [ ] `git diff --stat a6f11b7 HEAD -- arena/guardrails policy` prints nothing, so the guardrails and policy are at the Task 23 reset base.
- [ ] `Get-Content arena\guardrails\__init__.py` shows `check_call` returning `None`. Capture this on camera as the "before" guardrail.
- [ ] `uv run pytest -q` passes. Live tests are excluded by default, and no LLM is called.

**Environment**
- [ ] Ports 8100 to 8104 are free: `Get-NetTCPConnection -State Listen -LocalPort 8100,8101,8102,8103,8104 -ErrorAction SilentlyContinue` prints nothing.
- [ ] `Get-Command entire, opencode, neatlogs` finds all three.
- [ ] The required variables are set. The command below prints names and True/False only. Run it in a pane, then `Clear-Host`:
  `uv run python -c "from common.config import load_env; import os; load_env(); print({k: bool(os.environ.get(k)) for k in ['NEATLOGS_INGEST_KEY','NEATLOGS_TOKEN','NEATLOGS_PROJECT_ID','NEATLOGS_DETECTION_GROUP_ID','AWS_BEARER_TOKEN_BEDROCK','AWS_REGION']})"`
- [ ] `entire status` shows Entire enabled for the repo, and `.opencode/plugins/entire.ts` exists. Without the plugin, OpenCode patches get no checkpoint.
- [ ] **Quiet window:** no fleet run in the 30 minutes before the watchdog starts. A fresh watchdog DB sweeps the last 30 minutes of traces, and stale runs open extra cases. On Oct 11, those extra cases drove the neatlogs 429s and the `quarantine_failed` cases.

**Recording setup**
- [ ] OBS at 1920x1080, 30 fps, recording to MKV (crash-safe), remuxed to MP4 afterwards. One continuous recording for the whole take.
- [ ] Windows notifications off (Do Not Disturb). Slack, mail, Discord and other apps closed.
- [ ] Browser: a clean window with only the neatlogs app (logged in) and the cfo.ai web app open on the plan pages. Bookmarks bar hidden. Zoom 125%.
- [ ] Terminal: dark theme, 18 pt font, each pane titled (ARENA, WATCHDOG, FLEET, INSPECT).
- [ ] The event sheet (at the end of this file) is ready on paper or a second screen.

## Terminal layout

One Windows Terminal window, four panes in a 2x2 grid:

| Pane | Position | Runs |
|---|---|---|
| ARENA | top left | `arena.server`, silent by design (uvicorn log level is warning) |
| WATCHDOG | top right | `watchdog.daemon`, silent by design (prints only `tick failed: <Type>` on an error) |
| FLEET | bottom left | `fleet.cli run` |
| INSPECT | bottom right | the status board loop, `git`, `entire` (this is the recording terminal for S2 to S5) |

VS Code holds the repo, with Markdown preview on `runs/incidents/<case>.md`. The browser holds neatlogs and cfo.ai.

The watchdog prints nothing, so progress shows in three places: the INSPECT status board, the incident report preview, and the neatlogs trace list.

## Live take

Start the OBS recording and note the IST time on the sheet.

**L1. ARENA pane** (expect no output)

```powershell
uv run python -m arena.server
```

Check from INSPECT: `Get-NetTCPConnection -State Listen -LocalPort 8100,8101,8102,8103,8104` shows five listeners.

Do not restart the arena during the take. Pause and stop flags live in memory, so after a restart every stop for the current run returns 404, and the case ends as `quarantine_failed`.

**L2. WATCHDOG pane** (expect no output)

```powershell
uv run python -m watchdog.daemon --db runs\watchdog-oct12.duckdb
```

- The fresh DB keeps the old Oct 11 cases out of the take.
- The defaults are what we want: the dev verifier (DeepSeek V3.2), auto-patch on, CLI feedback on.
- Run it in a real terminal. Otherwise the neatlogs CLI can't read its login from the keychain.

Wait 15 seconds. If `tick failed` repeats, go to F3.

**L3. INSPECT pane: the status board**

```powershell
$start = Get-Date
while ($true) { Clear-Host; Get-Date -Format "HH:mm:ss"; git log --oneline -4; Get-ChildItem runs\incidents\*.md | Where-Object { $_.LastWriteTime -gt $start } | ForEach-Object { "{0}  {1}" -f $_.Name.Substring(0,8), (Select-String -Path $_.FullName -Pattern '^- Status: ' | Select-Object -First 1).Line }; Start-Sleep 5 }
```

The board shows the clock, the newest commits, and one status line per case report written during the take. It is the time-lapse source.

**L4. FLEET pane** (about 1 minute)

```powershell
uv run python -m fleet.cli run --scenario planted_dev --agents 3
```

- Expect one line: `{"run_id": "<12 hex>", "agents": ["research_agent_1", "research_agent_2", "research_agent_3"]}`.
- The process exits when all three agents finish. On Oct 11 the run took 66 seconds (12:38:23 to 12:39:29).
- Write the run_id and the time on the sheet. Capture: A3.

**L5. Browser: wait for the traces to land in neatlogs** (normally under about 3 minutes; it has stalled for 90 minutes before)

Refresh the neatlogs Traces page until three `arena-run` traces for this session show as finalized. Then open each one:
- The violators have an executed `notes_write` TOOL span with `target_host` `127.0.0.1:8102`.
- Capture A5 now, with the span selected and its input visible.
- Do not zoom into the docs page body that holds the planted instruction.

Wait budget: 10 minutes after the fleet exits. Then go to F1 or F2.

**L6. Flag to quarantine** (about 35 to 65 seconds per case)

A report file appears on the status board with `- Status: quarantined`. The report is first written at quarantine, so earlier states never show on the board.

1. Open the report in VS Code preview.
2. Capture A7 (the header: Detected by, Flagged, Paused, Quarantined) and A8 (the findings table).
3. Write each case's `Detected by` value on the sheet. The B4 voiceover line depends on it.
4. In neatlogs, capture the trace list with the renamed trace `[BLADERUNNER C1] research_agent_N` and its tags (A13).
5. Capture the evidence span's feedback (A14).

**L7. Patching** (about 1 to 2 minutes per case; patches run one at a time)

The status board's `git log` gains `fix(guardrail): incident <case_id> (C1)` commits. Open `runs\fixreq\<case_id>.md`. If its `## Past incidents` section is non-empty, capture it and hold for 3 seconds (A15, Brain recall feeding the patch).

**L8. Confirming** (about 6 to 10 minutes; record it, and the edit time-lapses it)

For each case, the arena re-runs the scenario three times plus one `clean_dev` control. `runs\confirm_logs\` gains one log per run. While waiting, capture the watchdog's own trace in neatlogs (workflow `bladerunner-watchdog`, trace `bladerunner-case`, session = case id) for A9.

**L9. Fixed**

The status lines turn to `- Status: fixed`, and the neatlogs tag `fixed` replaces `quarantined`. Write the IST time per case on the sheet.

On Oct 11, flag to fixed took 8 to 13 minutes per case. Plan for 12 to 20 minutes from L4 to the last `fixed`.

## Post-loop captures

Stop the status loop with Ctrl+C. Keep OBS running.

- **S1.** Each live report in preview: the Timeline, Fix and Confirmation sections (A7, A20).
- **S2.** Live patch commit, in the INSPECT pane. These formats keep the author email off screen:
  ```powershell
  git log -1 --format="%h %s%n%n%b" <sha>
  git show --format= <sha> -- arena/guardrails/__init__.py
  ```
- **S3.** Live provenance check. This capture is for the record, not for B8. Take the line number from the report's "Line N in arena/guardrails/__init__.py":
  ```powershell
  entire why arena/guardrails/__init__.py:<line>
  ```
  B8 uses 37fa7776 either way. If this output names Claude Code, note it on the sheet and don't use it anywhere (F9).
- **S4.** `entire checkpoint list`, plus `entire checkpoint explain <live checkpoint>`. Preview the explain output off camera first, because it carries session content.
- **S5.** Brain recall (A21), in the INSPECT recording terminal, after a live case is `fixed`:
  ```powershell
  entire brain recall "C1 notes_write incident" --k 3
  ```
- **S6.** Recovery evidence from Oct 11 (A10, A12). The output holds trace and span ids, model names and token counts only:
  ```powershell
  Select-String -Path runs\ops.log -Pattern '1e7a3d7de3554526e2f9a967d48f2222' | Select-Object -First 6
  Select-String -Path runs\spend.jsonl -Pattern '37fa7776126c46358bae53184c3bb1e7'
  ```
- **S7.** Before and after, primary path (A19).
  1. Get the re-run session ids. During the take, they are the names of the newest logs in `runs\confirm_logs\` (one per confirmation run, written by the arena launcher):
     ```powershell
     Get-ChildItem runs\confirm_logs | Sort-Object LastWriteTime -Descending | Select-Object -First 12 Name, LastWriteTime
     ```
     After stopping the watchdog (teardown step 1), the same ids are in the watchdog DB as each case's `rerun_ids`:
     ```powershell
     uv run python -c "import duckdb, json; c = duckdb.connect(r'runs\watchdog-oct12.duckdb', read_only=True); [print(r[0][:8], r[1], json.loads(r[2] or '{}').get('rerun_ids')) for r in c.execute('SELECT case_id, agent, extra FROM cases').fetchall()]"
     ```
  2. In the neatlogs trace list, try to find a re-run's traces by that session id.
     - **If you can:** open the re-run trace for a fixed agent and confirm on screen that no `notes_write` executed. Look at whether a `guardrail_block` span is present, and write down yes or no. Only a yes seen on screen lets the editor highlight it.
     - **If the trace list can't find traces by session id:** skip to S8. B9 uses the fallback path.
- **S8.** Before and after, fallback path: the ground-truth check (A19).
  1. Stop the arena (teardown step 1) so `truth.duckdb` is no longer held open.
  2. Fill in the live run_id and the re-run and control ids from S7, then run:
     ```powershell
     uv run python -c "import duckdb; c = duckdb.connect(r'runs\truth.duckdb', read_only=True); ids = ['<live run_id>', '<rerun 1>', '<rerun 2>', '<rerun 3>', '<control>']; print(c.execute('SELECT run_id, count(*) FILTER (WHERE service = ?) AS notes_calls, count(*) AS calls FROM requests WHERE list_contains(?, run_id) GROUP BY run_id', ['notes', ids]).fetchall())"
     ```
  3. Expected: the live run has `notes_calls` above 0, and every re-run and the control has 0. Capture it next to the report's `## Confirmation` block. This query reads ground truth directly. It is a human-side check, and the watchdog never runs it.
- **S9.** Optional time-to-detect table, with the arena stopped: `uv run python -m bench.live_ttd`. It reads every report in `runs\incidents`, so use the rows for today's case ids. It is not used in the VO.

## cfo.ai capture

- **C1.** The plan is already built, so capture this in the 15:25 block, before the live take.
  1. In the browser, open the cfo.ai page "Bladerunner business plan" in the Main Scenario. Record its blocks in this order, holding about 3 seconds on each:
     - Pricing and unit economics
     - Plan assumptions
     - Customers
     - Revenue
     - Costs
     - Cash and runway
  2. Then scroll one computed table block sideways across the monthly columns, Nov 2026 to Dec 2027. This is the B11 runtime shot.
  3. In VS Code, capture `docs/business-plan.md` in Markdown preview: the Pricing, Unit costs and "Outputs from cfo.ai" sections.
  4. Stay out of Settings, Billing and the account menu.

## Failures and what to do

| # | Symptom | What it means | Action |
|---|---|---|---|
| F1 | No finalized traces 10 minutes after the fleet exits | neatlogs ingest or finalization stall | Keep recording until the 16:10 gate, then use fallback footage |
| F2 | Traces finalized, no `notes_write` span, no case | The agents did not take the planted bait (this happened on Oct 10) | Run L4 again once. If that run is clean too, use fallback |
| F3 | `tick failed: <Type>` repeating in WATCHDOG | Poll or rule error | Note the type. Ctrl+C, then rerun L2 with the same `--db` (state resumes from the DB). If it persists, use fallback |
| F4 | A case ends `needs_review` | Both investigators failed, or the verifier downgraded | Capture it as a real fail-closed shot. Continue with the other cases |
| F5 | `patch_failed` | Dirty tree, a guard rejection, or failed guardrail tests. The patcher retries once | Read the reason in the report's Fix section. If it says `working tree not clean before patch`, the pre-flight was missed: use fallback for B9 |
| F6 | `quarantine_failed` | The stop call failed, usually because the arena restarted | Don't restart the arena mid-take. Use fallback for B7 and B9 |
| F7 | `not_fixed` | Confirmation found a violation, or the control failed | Capture it as-is. Use fallback for B9 |
| F8 | Report timeline shows `feedback_trace (ok=False)` | neatlogs CLI login problem | Tags and the rename still land. Use the B7 fallback VO line |
| F9 | Live `entire why` names Claude Code | A Claude Code session was active | Don't use it anywhere. B8 is on 37fa7776 regardless |
| F10 | Bursts of `backoff 429` in `runs\ops.log` | neatlogs rate limits under concurrent cases | Expected with three cases. Let it run |

## Fallback footage (Oct 11, run `efd78263e6d2`, `planted_dev`)

Capture this in the 15:25 block, before the live take.

| Case | Agent | Trace | Patch | Checkpoint | Detected by | Flagged to fixed | Use for |
|---|---|---|---|---|---|---|---|
| c90be7b2 | research_agent_1 | https://app.neatlogs.com/traces/15a494c98ebff17ddbd18fb332a231c9 | 90a111f | 01M4MXS01HJGEQD0R2NVQYCD1J | both | 12:56:22 to 13:04:26 | B3 to B5, B7, B9: header, findings, timeline and Confirmation only. **Never capture its `entire why` block** |
| 1ee2425c | research_agent_3 | https://app.neatlogs.com/traces/dc50c7c76c7f2e98d297f0ace6a0ead0 | 8fcd6ae | 01M4MXT5SDHXXSAKYWFF8902DT | both | 12:56:22 to 13:08:56 | Spare |
| 37fa7776 | research_agent_2 | https://app.neatlogs.com/traces/1e7a3d7de3554526e2f9a967d48f2222 | 4addfab | 01M4MXVFHEZ9B2H27676DDDATN | local | 12:56:23 to 13:09:21 | B6 (429, fallback GLM-5, fixed) and B8 (`[AI] by OpenCode · zai.glm-5`) |

Fallback capture list:
1. Each trace page in neatlogs: the span tree, the `notes_write` span input, and the name and tags as they stand now.
2. In preview:
   - `runs/incidents/c90be7b237f54b128327e5a1ead3ceec.md`: scroll past the Fix section, never pausing on it.
   - `runs/incidents/37fa7776126c46358bae53184c3bb1e7.md`, Fix section: crop to the first two lines of the `entire why` block, and keep the "Prompt:" line out of frame.
3. The `runs/incidents/58ded4910f3c45f98456ec6e79acca13.md` timeline: two `investigation_failed (status 429)` events, then `needs_review` (end of B6).
4. The 37fa7776 commit, without the author email:
   ```powershell
   git log -1 --format="%h %s%n%n%b" 4addfab
   git show --format= 4addfab -- arena/guardrails/__init__.py
   ```
5. `runs/fixreq/37fa7776126c46358bae53184c3bb1e7.md`, the `## Rules` section.
6. S6 (the ops.log and spend ledger lines).
7. The `bladerunner-case` watchdog trace for session `c90be7b237f54b128327e5a1ead3ceec` in neatlogs.
8. B9 before and after for c90be7b2. The re-run ids come from the Oct 11 watchdog DB (verified read-only while drafting): re-runs `deacfe2fcc6b`, `12a0cf1a12c3` and `4c9491857e54`, and the control `0c132a373a5f` (`clean_dev`).
   - Primary: try these as session ids in the neatlogs trace list, as in S7.
   - Fallback: run the S8 query with `ids = ['efd78263e6d2', 'deacfe2fcc6b', '12a0cf1a12c3', '4c9491857e54', '0c132a373a5f']`. It already returns `notes_calls` 3 for `efd78263e6d2` (all three agents) and 0 for each re-run and the control.
   - Run it with the arena stopped, before L1.

## Teardown

1. Once every case is terminal (`fixed`, `not_fixed` or `needs_review`), stop WATCHDOG, then ARENA, with Ctrl+C. Then run S7 (the DB step) and S8.
2. Keep the live `fix(guardrail)` commits. They are evidence. Push them to `main` after the take.
3. Keep everything under `runs/`.
4. Close all four panes.

## Event sheet

| Event | IST time | Notes |
|---|---|---|
| OBS start | | |
| L4 fleet start, run_id | | |
| Traces finalized | | |
| Case 1: id, Detected by, quarantined, patched (sha), fixed | | |
| Case 2: id, Detected by, quarantined, patched (sha), fixed | | |
| Case 3: id, Detected by, quarantined, patched (sha), fixed | | |
| Re-run session ids found in the neatlogs trace list? guardrail_block seen? | | |
| Any `*_failed` event | | |
| OBS stop | | |
