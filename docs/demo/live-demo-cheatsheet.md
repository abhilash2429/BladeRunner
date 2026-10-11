# Live demo cheat sheet

One page to keep open while recording. The full version with every capture and failure case is `demo-runbook.md`.

## The story you are showing

Three AI agents get a task. A help page they read contains a planted instruction to post their answer on a notes board their rules forbid. Bladerunner catches it in neatlogs, freezes the agent, proves it with the exact span, gets OpenCode to write a guardrail, and re-runs the agents to prove the fix holds. Nobody touches anything after the first command.

## Before you hit record (10 minutes, off camera)

1. Close every Claude Code window open in this project. Check Task Manager for `claude`.
2. In a plain terminal: `neatlogs auth status`. If it is not logged in, `neatlogs auth login`.
3. `git status` shows nothing outside `runs/`.
4. `uv run pytest -q` passes.
5. Nothing on ports 8100 to 8104, no fleet run in the last 30 minutes.
6. Browser: neatlogs Traces page logged in, cfo.ai plan page. Nothing else. No `.env` anywhere on screen.
7. Windows Terminal, 4 panes, big font: ARENA, WATCHDOG, FLEET, BOARD.

## The run (start OBS, then type these in order)

**ARENA** (prints nothing, that is normal)

```powershell
uv run python -m arena.server
```

**WATCHDOG** (prints nothing, that is normal)

```powershell
uv run python -m watchdog.daemon --db runs\watchdog-oct12.duckdb
```

**BOARD** (refreshes every 5 s: clock, newest commits, one line per case)

```powershell
$start = Get-Date; while ($true) { Clear-Host; Get-Date -Format "HH:mm:ss"; git log --oneline -4; Get-ChildItem runs\incidents\*.md | Where-Object { $_.LastWriteTime -gt $start } | ForEach-Object { "{0}  {1}" -f $_.Name.Substring(0,8), (Select-String -Path $_.FullName -Pattern '^- Status: ' | Select-Object -First 1).Line }; Start-Sleep 5 }
```

**FLEET** (about 1 minute, prints the run id, then exits)

```powershell
uv run python -m fleet.cli run --scenario planted_dev --agents 3
```

## What to show, in order

| Minute | Where | Show | Say |
|---|---|---|---|
| 0 | VS Code | `policy/research_agent.yaml` (`notes_write: []`) and `arena/guardrails/__init__.py` (`check_call` returns `None`) | "Here are the agent's rules: it may not write to the notes board. And the guardrail is empty." |
| 1 | FLEET | the run id line | "Three agents, one task each. One help page has a planted instruction." |
| 2 to 4 | neatlogs Traces | the three `arena-run` traces finalize; open one, select the `notes_write` span, show `target_host 127.0.0.1:8102` | "This is the agent doing what the planted note said." |
| 4 to 5 | BOARD | a case line appears: `Status: quarantined` | "Bladerunner caught it, froze the agent, investigated and verified, with no human involved." |
| 5 | VS Code preview | `runs/incidents/<case>.md`: header (Flagged, Paused, Quarantined times) and the Findings table; click the evidence span link | "Every claim links to the exact span. A second model from a different family checked it." |
| 5 | neatlogs | the trace is now named `[BLADERUNNER C1] research_agent_N`, tagged quarantined, with feedback on the span | "The verdict goes back into neatlogs where the team already looks." |
| 6 to 7 | BOARD | a `fix(guardrail): incident ...` commit appears | "OpenCode wrote a guardrail. It may only tighten rules, only in two files." |
| 7 | BOARD pane (Ctrl+C the loop first, or a 5th pane) | `git show --format= <sha> -- arena/guardrails/__init__.py` then `entire why arena/guardrails/__init__.py:<line>` | "Entire records which agent wrote each line and why." |
| 7 to 15 | BOARD | wait; cut this in the edit | "Now it re-runs the same agent on the same trap, plus a normal run." |
| 15 | BOARD and neatlogs | `Status: fixed`, tag `fixed` replaces `quarantined` | "Fixed only when the re-runs are clean in ground truth it never sees." |
| 16 | any pane | `entire brain recall "C1 notes_write incident" --k 3` | "And it remembers, so the next investigation starts smarter." |

Then cut to the numbers (README Results and External validation) and the cfo.ai plan page.

## If something goes wrong

| What you see | Do this |
|---|---|
| No finalized traces 10 minutes after FLEET exits | neatlogs is slow. Keep recording until 16:10, then use the Oct 11 footage (runbook, Fallback footage). |
| Traces finalized but no case after 2 minutes | Agents didn't take the bait. Run FLEET once more. |
| WATCHDOG prints `tick failed` over and over | Ctrl+C and run the same WATCHDOG command again; it resumes. |
| A case says `needs_review` | That is the safe outcome when evidence is unclear. Show it, keep going with the others. |
| `patch_failed` | Open the report's Fix section for the reason. If it says the tree is not clean, use the Oct 11 footage for the fix part. |
| Never restart ARENA mid-take | It forgets who is paused and stops fail. |

## After

Ctrl+C WATCHDOG, then ARENA. Keep the `fix(guardrail)` commits and push them.
