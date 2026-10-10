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

## Demo
Pending.

## Known limitations and next steps
Pending.

## Submission
Requirements confirmed: public repo, commits dated Oct 10 to 12, demo video under 3 minutes covering neatlogs, Entire and cfo.ai, business plan, #neatHack posts. Outstanding: everything.
