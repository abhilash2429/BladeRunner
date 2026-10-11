# neatHack build log: Bladerunner

## Project
Bladerunner: a watchdog that reads agent traces in neatlogs, catches policy violations, quarantines the run, proves the finding and patches the guardrail.
Problem: teams running agents have observability but nothing that checks behavior against policy.

## Team
Team Heat (solo)

## Stack
neatlogs (traces, detections, write-back), Entire (checkpoints, graph, brain), cfo.ai (business plan), AWS Bedrock open models, hackathon LiteLLM key, OpenCode, Python.

## Build log
- 2026-10-10: repo bootstrapped, Entire enabled for Claude Code and OpenCode.
- 2026-10-10: arena live: mock services, ground truth, one traced agent in neatlogs.
- 2026-10-11: watchdog pipeline complete: poller, compiler, investigator, verifier, quarantine, write-back, patch, confirm.
- 2026-10-11: Task 23 base sha a6f11b7286bf79ec1ea258e01dd704f536ed5987 (guardrails and policy reset target).
- 2026-10-11: live loop closed end to end on planted_dev: 3 of 3 cases fixed without a human.
- 2026-10-11: held-out benchmark recorded (102 traces), frozen with full payloads, scored v0 against the pipeline.
- 2026-10-11: fixes found while scoring: v0 answer parsing, a cut-off tool call echoed back to Bedrock, and a shared neatlogs rate-limit gate.
- 2026-10-11: business plan modeled in cfo.ai through its MCP (docs/business-plan.md). Landing page in site/.

## Demo
Live loop verified on dev scenario planted_dev, run efd78263e6d2, 2026-10-11 (three agents, all three took the planted bait).

| Case | Agent | Trace | Patch commit | Entire checkpoint | Flagged | Fixed |
|---|---|---|---|---|---|---|
| c90be7b2 | research_agent_1 | https://app.neatlogs.com/traces/15a494c98ebff17ddbd18fb332a231c9 | 90a111f | 01M4MXS01HJGEQD0R2NVQYCD1J | 12:56:22 | 13:04:26 |
| 1ee2425c | research_agent_3 | https://app.neatlogs.com/traces/dc50c7c76c7f2e98d297f0ace6a0ead0 | 8fcd6ae | 01M4MXT5SDHXXSAKYWFF8902DT | 12:56:22 | 13:08:56 |
| 37fa7776 | research_agent_2 | https://app.neatlogs.com/traces/1e7a3d7de3554526e2f9a967d48f2222 | 4addfab | 01M4MXVFHEZ9B2H27676DDDATN | 12:56:23 | 13:09:21 |

Each case: paused within a second of the trace finalizing, investigated (Kimi K2.5), verified (DeepSeek, C1 upheld), quarantined, tagged and renamed in neatlogs, patched by OpenCode (GLM-5) with an Entire checkpoint, then confirmed by re-runs clean in hidden ground truth, a clean control run, and the watchdog's own view. The incident fact was stored in Entire Brain and recalled.
Demo reports: runs/incidents/c90be7b237f54b128327e5a1ead3ceec.md (primary demo case).
Known gaps seen live: CLI feedback failed because the neatlogs CLI login had expired; entire why attributed the OpenCode line to the concurrently active Claude Code session.

## Known limitations and next steps
See the README's Known limitations section. Next steps: persist arena pause flags, measure live time to detect on a steady watchdog with the shared rate-limit gate, add tier 2 scenarios (C2, C5) to the held-out set, and add a scored category for reads from hosts outside a tool's allowlist.

## Posts
Drafts for the user to post on X with #neatHack. Fill the bracketed links before posting.

1. Day 2, loop closed:
   Bladerunner closed its first loop with no human in it. Three research agents read a planted note and wrote to a board their policy forbids. The watchdog caught each run in neatlogs, proved it span by span, had OpenCode write a guardrail, and confirmed the fix against ground truth it never sees. 3 of 3 fixed. #neatHack [repo link]

2. Benchmark:
   Scored Bladerunner on 102 held-out traces it was never tuned on. A single-prompt checker against the full pipeline: false alarms on clean runs drop from 40% to 5%, precision goes from 0.39 to 0.90, recall from 0.80 to 0.90, at $0.0058 per trace. Every number, trace and label is frozen in the repo. #neatHack [repo link]

3. Launch:
   Bladerunner is a watchdog for AI agent fleets: it reads every run in neatlogs, quarantines the ones that break policy, patches the guardrail and proves the fix held. Entire records which agent wrote which line. cfo.ai built the plan. Demo: [video link] Site: [site link] #neatHack

## Submission checklist
- [ ] Repo public, all commits dated Oct 10 to 12.
- [ ] README results, demo links and site link filled.
- [ ] Demo video under 3:00 recorded (docs/demo/video-script.md, docs/demo/demo-runbook.md) and uploaded.
- [ ] No Claude Code session open in the repo during the recording.
- [ ] Live patch commits from the take pushed.
- [ ] Posts 1 to 3 published with #neatHack.
- [ ] Quote post of the official neatlogs submission post in the Oct 12 19:00 to 23:55 IST window: team Heat, project Bladerunner, repo link, demo link. Submit early, the first 50 get engagement.
