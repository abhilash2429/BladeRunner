# neatHack context (gathered 2026-10-09, ~9:45 PM IST)

Sources: https://neatlogs.com/hackathon (official, incl. FAQ JSON-LD), https://luma.com/s5blr882, docs.neatlogs.com, github.com/entireio/cli, docs.entire.io/guides/graph

## Core facts
- Host: neatlogs (agent observability). Co-host TheVibeFounder.
- Online, 48h. One track: "Build an agent that works (literally)".
- Team: 1-4. Every member registers on Luma with the same team name. One team per person.
- All times IST.

## Timeline (IST)
- Oct 10 08:00 web check-in opens (neatlogs.com/hackathon/check-in). A $25 AWS infra allowance goes to the FIRST 350 TEAMS to check in (us-east-1, EC2/Lambda/IAM/LB, no Bedrock).
- Oct 10 09:00 kickoff call (judges/mentors say what they want, "surprise workshop").
- Oct 10 12:30 hacking starts. AWS + Crustdata credits transferred.
- Oct 10-11 workshops + office hours.
- Oct 12 19:00-23:55 submission window. Late = not judged.
- Oct 16 winners.

## Hard rules
- Planning in advance is fine. ALL code written between start Oct 10 and deadline Oct 12. No pre-built or half-built projects.
- Public repo, commits must be verifiably dated during the hackathon.
- Must use all three: neatlogs, Entire, cfo.ai, each with a clear role. Demo must cover all three.
- Conduct: no harassment/spam/plagiarism. Bought likes/bots disqualify.

## Submission
- Public quote post on X of the official neatlogs submission post: team, name, project, public repo, demo link. "Submit on X" button only appears in the window. Opening the draft is not submitting.
- Demo video <= 3 min. For EACH of neatlogs, Entire, cfo.ai it must explicitly cover: architecture, implementation, runtime, reasoning, result.
- Supporting evidence lives in the repo.
- neatlogs and cfo.ai engage with the first 50 quote tweets (submit early in the window).

## Judging (each /100, weighted; mentors score)
| Category | Weight |
|---|---|
| The agent: substantial task start to finish | 30% |
| Use of neatlogs, Entire, cfo.ai | 25% |
| Demo video clarity (agent + all three tools) | 20% |
| Building in public (posts tagged #neatHack during event) | 15% |
| Usefulness + originality | 10% |

Community winner ($500, stackable): genuine engagement, launch/content quality, people trying/sharing it. Follower count does not decide. Partner usage not scored for this one.

## Evidence judges expect
- Agent improved: before/after runs (two neatlogs traces) with compared numbers.
- neatlogs: trace links or screenshots.
- Entire: checkpoints or entire-graph output from the repo.
- cfo.ai: business plan with pricing, costs, revenue, runway.
- Recovery: a run where an action failed and what the agent did next.
- Building in public: links to #neatHack posts.
- Code: public repo, commits during event.

## Focus areas (what "works" means to them)
Planning, tool use, state across steps/runs/failures, context selection, long execution, recovery, verification, iteration.
Example task sizes: fix a GitHub issue end to end; cited research with conflicting sources; watch a running system, diagnose, act; workers + coordinator resolving disagreements; long task in a changing environment.

## Prizes
- 1st $2,000 + $5,000 Dodo Payments credits
- 2nd $1,000 + $3,000 Dodo
- 3rd $500 + $1,000 Dodo
- Each placed winner: 3 months neatlogs Pro
- Community winner $500 (stackable with a placement)
- Best Use of Entire: listed only on Entire's own guide (hack.entire.io/neathacks-2026), "most creative use of Checkpoints, Graph or Brain". Not on the neatlogs page.
- Judges: Haimantika Mitra, Abhi Singh (Entire), Prateek Karnal (Orchestrator.inc), Ajay Yadav, Akshay Hangloo (neatlogs).

## Credits / resources
- AWS: $25 infra allowance, first 350 teams, us-east-1, EC2/Lambda/IAM/load balancers. "Bedrock isn't included."
- LiteLLM key: $20 per team over 30 days, shared. nova-micro, nova-lite, nova-pro, nova-2-lite, claude-haiku, claude-sonnet-4.6, claude-opus-4.6. Base https://litellm.vision.aivar.app/v1.
- Own AWS account 791725739974 (standalone, IAM user abhilash) holds $153 credits for Bedrock open models.
- Crustdata: 5,000 credits each (company + people data APIs).
- cfo.ai: sign up at cfo.ai/neathacks. Card required. CANCEL SUBSCRIPTION IMMEDIATELY in Settings -> Billing. neatHack team gives credits directly.
- Discord (official comms): neatlogs.com/discord
- neatlogs hackathon skill (keeps a hackathon.md evidence log at repo root): neatlogs.com/hackathon/skill

## Tool notes

### neatlogs (Python SDK)
- `pip install -U "neatlogs[openai]" openai`, Python 3.10-3.13. Key in `NEATLOGS_API_KEY`.
- `neatlogs.init(api_key=..., workflow_name=..., instrumentations=["openai"], tags=[...])` before importing instrumented libs.
- `client = neatlogs.wrap(OpenAI(...))` traces chat.completions AND responses.create, streaming, async.
- `@neatlogs.span(kind=...)` kinds: WORKFLOW, AGENT, CHAIN, TOOL, RETRIEVER, EMBEDDING, GUARDRAIL, MCP_TOOL. Put WORKFLOW on the entry point. `neatlogs.trace(name, kind=...)` context manager for blocks.
- `neatlogs.flush(); neatlogs.shutdown()` before exit.
- Also: Claude Code plugin, Codex hooks, MCP server, experiments, detections, investigations (fix briefs), evals. Free tier: 100k spans/mo, 3 investigations/mo.
- `flush_interval` default 5.0s. Partial-trace visibility undocumented.
- Public API: SDK ingest key can't read it. Needs a service-account token (Bearer) + `x-project-id`. Every mutation needs `idempotency-key` (22-43 chars).
- Service account OK: trace reads, `PATCH /traces/{id}` (tags, traceName), `POST /detections`, `GET /detections/{id}/hits`.
- OAuth only: trace feedback, span feedback, comment messages, detection preview, backfills. No endpoint creates a comment thread. Preview and backfill also need `tier-c-compute`, disabled in production.
- CLI: `neatlogs auth login` (OAuth). `neatlogs feedback create|span-set`, `neatlogs comments create --thread`.

### Entire
- Git + agent-session recorder. Windows: PowerShell installer or `scoop install entire/entire`.
- `entire enable` in repo (installs git hooks + agent hooks into .claude/settings.json / .codex/hooks.json). `entire status`.
- Checkpoint created per commit, linked by `Entire-Checkpoint: <id>` trailer, stored under refs/entire/checkpoints/*. Commit often.
- `entire checkpoint list | explain <id> | tokens | search`, `entire recap`, `entire why <file>:<line>`.
- entire-graph: `entire plugin install graph`, then `entire graph init-agents --repo .` (writes managed blocks into AGENTS.md / CLAUDE.md). Needs CLI >= 0.10.0, Git >= 2.36. Local tree-sitter code graph.
- OpenCode: `entire agent add opencode` writes `.opencode/plugins/entire.ts`. No subagent capture, no `entire import` for OpenCode.
- Brain: `entire brain setup`, `entire brain init-agents`, `entire brain mcp`.
- RISK: transcripts live in the repo; public repo exposes them. Secret redaction is best-effort. Keep keys out of prompts.

### cfo.ai
- "Ari", AI CFO. Builds financial models (P&L, cash flow, scenarios, runway). Use for agent's pricing/costs/revenue/runway plan. Export format unknown. MCP `https://api.cfo.ai/mcp`, OAuth via client.

## Unknowns
- Surprise workshop content, AWS credit redemption flow, cfo.ai credit amount.
- Discord announcements not checked (user said leave Discord alone).
