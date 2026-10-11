# Site data

Everything the frontend needs, as static JSON. Architecture and the event schema are in `docs/frontend/proposal.md`.

## `replay/efd78263e6d2.full.json`: the Oct 11 live run, flag to fixed

This is the `bench.replay` export. The landing page plays a shorter cut, `replay/efd78263e6d2.json`, built by `frontend/scripts/build_public_data.py` (58 events, player fields `label`, `agents`, `note`). Regenerating the export overwrites `efd78263e6d2.json`; move that output back to `efd78263e6d2.full.json` and rebuild the cut before shipping.

- `runs`: every run in the replay and its role: `live` (the three agents), `confirm` (re-runs after a patch), `control` (a clean scenario run alongside).
- `cases`: one summary per case: agent, final status, detected by, neatlogs trace link, findings with the verifier's verdict, patch commit and Entire checkpoint, confirmation result.
- `events`: 234 events sorted by time, each `{seq, ts, source, type, run_id, agent, case_id, data}`.

| type | count | what it is |
|---|---|---|
| `run.started` | 13 | a run began: 1 live, 9 confirm re-runs, 3 controls |
| `agent.call` | 134 | an agent's tool call: service (`docs`, `data`, `notes`), path, and `planted` when the page it read carried the planted note |
| `agent.blocked` | 25 | a patched guardrail stopped a re-run's call to the notes board |
| `case.opened` | 3 | the watchdog opened a case; `detected_by` is native, local or both |
| `case.transition` | 27 | a state change, `from` and `to` |
| `case.findings` | 3 | the investigator's findings |
| `case.verdict` | 3 | the same findings after the verifier, with `verdict` and `reason` |
| `case.writeback` | 13 | tags written to neatlogs, and span feedback attempts (`ok` false on Oct 11: CLI login had expired) |
| `case.patched` | 3 | patch commit sha, Entire checkpoint, files |
| `case.confirming` | 3 | the re-run and control run ids |
| `case.confirmed` | 3 | re-runs clean, control clean, watchdog view clean |
| `case.failed` | 1 | case 37fa7776: the investigator hit a neatlogs rate limit, the fallback model finished the job |
| `case.remembered` | 3 | the incident was stored in Entire Brain |

Timing to know for the player: agents run 12:38:23 to 12:39:29 IST, cases open at 12:56:22 (the watchdog was being restarted during this run), patches land within about a minute, confirmation takes 6 to 10 minutes. Compress the gaps.

## `data/benchmarks.json`

- `held_out`: our 102-trace exam. Aggregate counts and rates for `v0` and `final` (Bladerunner). Per-trace predictions were not saved for this run, so draw any grid from the counts.
- `agentdojo`: 858 published runs. `summary` per checker and label, and `runs` with one entry per run: `model`, `suite`, `attacked`, `acted` (made the attacker's call), `security` (AgentDojo's strict label), and whether `v0` and `bladerunner` flagged it.

## Regenerate

```bash
uv run python -m bench.replay efd78263e6d2
mv site/replay/efd78263e6d2.json site/replay/efd78263e6d2.full.json
```

```bash
uv run python -m bench.site_data
```
