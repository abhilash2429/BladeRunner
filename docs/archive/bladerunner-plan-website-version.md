# Bladerunner: full project plan (neatHack, solo)

Status: proposal, not locked. Written Oct 10, ~1 PM IST. Hacking started 12:30 PM IST. Submission window Oct 12, 7:00 to 11:55 PM IST.

---

## 1. The problem, plainly

AI agents now browse the web on their own, at scale. Some of them misbehave. In 2026 this stopped being theory:

- **collusion.wiki** (HN #1, 2,301 points): about 18,000 posts by OpenAI agents on small German wikis. They used the wikis as a shared notepad: swapping answers to their tasks, sharing tricks to escape their sandbox (a hostname loophole that let them send POST requests), trying XSS attacks, impersonating moderators, trying to crack a random seed, and testing when they'd get shut down. It stopped on June 22, after OpenAI-linked IPs showed up on the site.
- The same pattern showed up on **RubyGems** (975 points), **Hugging Face** (755), **Wikimedia** (this week), **urlquery.net** (Transluce report) and an **Australian government site**.

Every one of these was found by **humans doing slow, manual forensics**: reading edit logs, matching IPs to cloud ranges, spotting repeated payloads, lining up timestamps across sites. It took weeks per incident.

The people who run websites, wikis, package registries and public data portals have **no tool** that answers: "Are AI agents active on my site right now, which ones, working together, doing what?"

## 2. Our solution, plainly

**Bladerunner is an AI detective that investigates AI-agent activity on a website.**

You give it a site's activity history (for a wiki, its edit log). It works through it like a human investigator would, then hands you an incident report:

1. **Who** is an agent and who is a human, and the evidence for each call.
2. **Which agents are working together** (campaigns), and on what.
3. **Where they come from** (cloud provider or company IP ranges, at a coarse level only).
4. **What they were trying to do**: the timeline, the tactics (sandbox escapes, attacks, impersonation, data relays).
5. **Proof.** Every claim in the report links to the exact raw log entries behind it, and to the detection logic that found them.

It also runs in **live mode**: it watches a site and raises an alert when a new agent shows up.

## 3. Why this wins (mapped to judging)

| Criterion | Weight | How Bladerunner scores |
|---|---|---|
| The agent: substantial task start to finish | 30% | A full investigation is hours of human work: thousands of records, multiple data sources, hypotheses, verification, a final report. It hits every focus area they listed (planning, tool use, state, context, long execution, recovery, verification, iteration). |
| Use of neatlogs, Entire, cfo.ai | 25% | All three do real work (section 8). Entire becomes chain of custody, which is a new use the Entire judges won't have seen. |
| Demo video | 20% | The demo has a hook: "researchers took weeks, our agent did it in under an hour," a score that climbs between runs, and a live catch. |
| Building in public | 15% | The topic is already viral on HN. Live catches during the event are posts that write themselves. |
| Usefulness + originality | 10% | Nobody has automated this. The problem is in the news right now. |
| Best Use of Entire (side prize) | separate | Chain of custody built on checkpoints, plus `entire why` cited inside the report. |

## 4. The data (verified Oct 10)

### 4a. The answer key: collusion.wiki dump
Free download, no login, from https://collusion.wiki/explorer/download. They invite people to analyze it. IPs are half-redacted and usernames removed.

| File | Size | What it has |
|---|---|---|
| revisions.jsonl.gz | 3.2 MB | 14,591 agent edits, each with full saved text |
| events.jsonl.gz | 0.5 MB | 14,591 saves, 5,217 deletions, 4 reverts, 101 probes |
| labels.jsonl.gz | 0.2 MB | 3,103 self-chosen agent names, each with the pages it edited |
| pages.jsonl.gz | 0.2 MB | 4,579 pages with totals and editor names |
| manifest.json.gz | 6 KB | how the export was built and checked |
| records / links / site-coverage / coverage-gaps | ~6.5 MB | community follow-up: 143 more sites, 499 shortener links, etc. |

Coverage: 7,203 agent edits across sites (dse 5,154, probier 601, fractal 68, others), plus link shorteners and rubygems.org (81).

**Limitation:** the dump only contains agent activity, so it can't teach or score "human vs agent" alone. Section 4b fixes that.

### 4b. The live site: DseWiki
- https://wikiservice.at/dse/wiki.cgi?action=rc&days=3650 returns **the entire change history in one request**: 14,045 entries from Jan 2017 to today, about 1.2 MB. Each entry has page, timestamp, editor name or IP, and the edit summary.
- Diffs are at `wiki.cgi?action=browse&diff=N&id=PAGE`.
- The admin (HelmutLeitner) opened up long lookbacks on purpose so people can study the "swarm attack".
- **Humans and agents are mixed together.** Joining the live history against the dump gives us positives (agent edits) and negatives (the human regulars and years of normal activity). That's our labeled benchmark.
- **Agents are still arriving.** In the last two weeks: CollaborativeAIVisitorOct09, CuriousFeedbackVisitor, MuseAssistent, GreenAiBot, QuietNodeAgent, MikhailKuryaevAssistant and others. That's the live-mode target.

### 4c. Secondary sites (to show it generalizes)
publictestwiki.com (MediaWiki, which has a proper API) and the probier and fractal wikis listed in the dump.

### 4d. Attribution data (public)
- OpenAI's published crawler IP list: openai.com/chatgpt-user.json (plus the archived copy collusion.wiki used)
- Azure, AWS and GCP published IP range files
- ARIN RDAP lookups for ownership of an IP block

## 5. What the product looks like

1. **CLI:** `bladerunner investigate --site dse` runs a full investigation. `bladerunner watch --site dse` runs live mode.
2. **Incident report:** a single HTML page (also saved as markdown in the repo) with:
   - a summary: N agent identities, M campaigns, date range, a confidence for each
   - a timeline of activity by day, agent versus human
   - campaign cards (who, what, which pages, tactics, evidence links)
   - an attribution table (coarse: "Azure range, matches OpenAI-published list")
   - a "how we know" section: each claim leads to evidence rows, then to the detector that found them (file:line), then to the Entire checkpoint showing why that detector exists
3. **Scoreboard:** `bladerunner score` prints precision, recall and finding coverage against the answer key, plus cost and time from neatlogs.
4. **Live alerts:** printed to the terminal, plus a log file we screenshot for X posts.

No fancy web app. The report page is the visual.

## 6. How it works, end to end

### 6a. Ingest (plain code, no AI)
1. Download the dump once into `data/raw/collusion/`.
2. Fetch DseWiki's full RecentChanges once, cached to `data/raw/dse/rc.html`, and parse it into rows.
3. Load everything into one **DuckDB** file: an `edits` table (site, page, timestamp, editor, ip_prefix, summary, text, source) and a `labels` table built from the dump. The labels are hidden from the agent and used only by the scorer.
4. Fetch page text and diffs from the live wiki **only for edits the agent asks about**, cached and rate-limited to 1 request every 2 seconds.

### 6b. The detective agent (the AI part)
A Python agent loop. Model roles (full details and budget in section 9):
- **Investigator (Kimi K2.5 on Bedrock):** plans, forms hypotheses, decides the next step, writes the report.
- **Bulk reader (GLM-4.7 Flash on Bedrock):** reads thousands of edits cheaply to tag the suspicious ones.
- **Verifier (DeepSeek V3.2 on Bedrock while developing, Claude Sonnet 4.6 via the hackathon key for scored runs):** tries to refute every finding.

**Its tools:**

| Tool | What it does |
|---|---|
| `sql(query)` | Read-only queries on the edits table (counts, timelines, who edited what). Labels are not visible. |
| `read_edit(id)` / `read_diff(page, n)` | Fetch an edit's full text or diff (from cache or the live site). |
| `triage(batch_filter)` | Send a batch of edits to the bulk reader. Returns a per-edit agent-likelihood score with reasons. |
| `ip_owner(prefix)` | Check an IP prefix against published cloud and OpenAI ranges and RDAP. |
| `similar(edit_id)` | Find edits with near-duplicate text, payloads or structure (embeddings plus MinHash). |
| `write_detector(name, spec)` | Have a coding agent write a reusable detection script into `detectors/` and commit it (this is where Entire comes in, section 8b). |
| `run_detector(name)` | Run a detector over the data and get back the edit IDs it flags. |
| `add_finding(claim, evidence_ids, confidence)` | Record a claim. It's rejected unless every evidence ID exists and actually supports the claim (checked by the verifier). |
| `write_report()` | Build the HTML and markdown report from the verified findings. |

**The loop, step by step:**
1. **Plan:** read a dataset summary (sizes, date range, top pages, editor counts) and write an investigation plan: what to check first.
2. **Hunt:** run SQL, triage and similarity searches, and write detectors for any pattern it spots (for example "page names like DataUSA*SequenceCollab*", "edit summaries in a templated style", "base64 blobs", "relay URLs like r.jina.ai").
3. **Hypothesize:** group flagged edits into identities and campaigns.
4. **Verify:** a separate verifier agent, from a different model family than the investigator, tries to **refute** each finding against the raw evidence. Findings that don't survive are dropped or downgraded.
5. **Iterate:** each verified finding seeds the next queries ("this campaign also used page X, who else touched it?").
6. **Report:** write the incident report.

**State and recovery:** every step's state (plan, hypotheses, findings, to-do list, detectors) is saved in DuckDB. If the process dies, `--resume` continues where it stopped. Expected failures, each handled visibly:
- the live wiki rate-limits or times out: back off, then use the cache
- a detector crashes or flags 0 or 100% of edits: the agent reads the error, rewrites the detector, re-runs
- the bulk reader returns malformed JSON: retry, then split the batch
- a finding fails verification: downgrade it and find more evidence

### 6c. The scorer (plain code)
- **Edit level:** precision and recall of "agent edit" calls against the labels (dump positives versus human negatives from 4b).
- **Identity level:** how many of the 3,103 agent names we flagged.
- **Finding level:** we turn collusion.wiki's published findings into a checklist (sandbox hostname loophole, XSS attempts, moderator impersonation, seed cracking, shutdown testing, data relay pages, the June 22 drop-off, and so on). The score is how many of them our report independently surfaces with correct evidence.
- **Efficiency:** cost, tokens and wall time per run, pulled from neatlogs.

### 6d. Live mode
Every 15 minutes: fetch RecentChanges for the last day (one small request), triage new edits, and if an edit scores high, run a short investigation and print an alert with evidence. It runs on my machine during the hackathon. Every catch is an X post.

## 7. How we prove it improved (required evidence)

- **Run 1:** baseline agent, simple prompt, no detectors. Record the score, cost and time.
- **Runs 2 to N:** after reading run 1's neatlogs traces, fix what went wrong (for example "it wasted 40% of tokens re-reading the same page" or "it missed the relay campaign because triage ignored edit summaries"). Each fix is a commit.
- **Final:** compare run 1 with run N in a table in the README: recall, precision, findings coverage, cost, time, each with a neatlogs trace link.
- **Recovery evidence:** a trace where a detector crashed or the site throttled us, and the agent's next step.

## 8. The three sponsor tools, exactly

### 8a. neatlogs: the detective's flight recorder
- **Setup:** `neatlogs.init(...)` at startup, then wrap the model client. `@neatlogs.span(kind="AGENT")` on the investigator and verifier, `kind="TOOL"` on each tool, `kind="WORKFLOW"` on a full investigation.
- **What we use it for:**
  1. Debugging runs: where the agent looped, wasted tokens, or picked the wrong tool.
  2. The before/after numbers (cost, latency, tool calls per run).
  3. Recovery evidence (failed spans followed by the retry).
  4. Evals: score each run against the answer key and log the score into neatlogs, so the improvement curve lives in their dashboard.
- **Day-1 check:** every model is called through the OpenAI SDK (Bedrock's OpenAI-compatible endpoint and the hackathon LiteLLM endpoint), so `neatlogs.wrap(OpenAI(base_url=...))` should cover all of them. Confirm the spans show up. If Bedrock's OpenAI-compatible endpoint doesn't serve Kimi/GLM, fall back to the Bedrock Converse API, which neatlogs documents support for.
- **In the demo:** the span tree of one investigation, a failed span and its recovery, and the run 1 vs run N comparison.

### 8b. Entire: chain of custody
In forensics, evidence without provenance is worthless. Entire gives us provenance for free.
- **Our development:** the whole repo is built with Claude Code while Entire is on, so every commit has a checkpoint (required evidence). entire-graph is installed so the coding agent navigates the repo through `entire graph search`.
- **Inside the product (the creative part):** when the detective wants a new detector, `write_detector` runs a headless coding agent (OpenCode `opencode run`, on GLM-5 via Bedrock; Entire officially supports OpenCode) inside the repo. Entire records that session, so when the detector is committed, it gets a checkpoint that holds the detective's request and the reasoning behind the code.
- **In the report:** for each finding, the "how we know" section runs `entire why detectors/relay_pages.py:<line>`. Each claim then shows: these log rows, flagged by this line of this detector, which exists because of this checkpoint and this reasoning.
- **Day-1 check:** confirm Entire captures headless `opencode run` sessions. If it doesn't, the fallback is to write detectors through an interactive-style session, or to register our detective as its own agent through Entire's Agent Integration Protocol (11 small CLI subcommands, JSON over stdin/stdout). The protocol route is also a strong Best Use of Entire story, so I'd treat it as a stretch goal either way.

### 8c. cfo.ai: the business plan
- **Customers:**
  - trust and safety teams at platforms with user-generated content
  - wiki foundations and hosts (Wikimedia, Fandom, Miraheze)
  - package registries (RubyGems, PyPI, npm)
  - public data portals that agents are scraping and probing
- **Pricing to model:** a free tier (one site, monthly report), a Pro tier (live monitoring per site, per month), and per-investigation pricing for incident response.
- **Costs to model:** inference per investigation (real numbers from neatlogs), monitoring compute, and support.
- **Output:** pricing, unit economics, revenue scenarios and runway. Export it into the repo as `business/plan.md` (plus screenshots), and show it in the demo for about 20 seconds.

## 9. Models and cost

### What we have
| Pot | Amount | Covers | Rule |
|---|---|---|---|
| **AWS credits (Bedrock)** | $153 | Open models AWS bills directly (Kimi, GLM, DeepSeek, MiniMax, Qwen, Nova, gpt-oss). Not Claude on Bedrock. | The workhorse. All development and most runs. |
| **Hackathon LiteLLM key** | $20 over 30 days | nova-micro, nova-lite, nova-pro, nova-2-lite, claude-haiku, claude-sonnet-4.6, claude-opus-4.6. OpenAI-compatible at `https://litellm.vision.aivar.app/v1`. | Precious. Claude only where it moves the score. |
| **Hackathon AWS infra allowance** | $25 | Compute, not models | Small always-on box for live mode, so it keeps watching while the laptop sleeps. |
| Azure | $0 left, pay-as-you-go | | Not used. |

Everything else is free: neatlogs paid plans (free for the event), Entire, cfo.ai paid plan (cancel right after signup), the data.

### Who does what (Bedrock prices verified via the AWS pricing API, us-east-1, per 1M tokens in/out)
| Role | Model | Price | Pot |
|---|---|---|---|
| Investigator | Kimi K2.5 (backups: GLM-5 $1.00/$3.20, MiniMax M2.5 $0.30/$1.20) | $0.60 / $3.00 | AWS |
| Bulk triage | GLM-4.7 Flash | $0.07 / $0.40 | AWS |
| Detector coding agent | OpenCode on GLM-5 | $1.00 / $3.20 | AWS |
| Verifier while developing | DeepSeek V3.2 | $0.62 / $1.85 | AWS |
| Verifier for scored runs | claude-sonnet-4.6 | budget-tracked | LiteLLM |
| Model bake-off (first hour) | Kimi K2.5 vs GLM-5 vs MiniMax M2.5 vs claude-sonnet-4.6, same short task | ~$3 total | both |
| Final report write-up (2 to 3 times) | claude-opus-4.6 | budget-tracked | LiteLLM |

### Token-saving design (built in from the start)
- Plain code does the first pass (SQL, regex, MinHash). The investigator reasons over aggregates and samples, not 4M tokens of raw text.
- Triage only reads edits the first pass can't settle.
- Hard token budget per run, enforced in code. The run stops and reports partial results if it hits the cap.
- Compact the investigator's context every N steps (summarize old tool results, keep findings).

### Budget
| Item | AWS | LiteLLM |
|---|---|---|
| Smoke tests + bake-off | $5 | $3 |
| ~15 development runs at ~$4 to $5 | $70 | 0 |
| ~5 scored runs (verifier on Claude) | $20 | $8 |
| Final report write-ups | 0 | $4 |
| Live mode | $2 | 0 |
| **Planned** | **~$97** | **~$15** |
| **Buffer** | **~$56** | **~$5** |

Guardrails: every run logs its cost (from neatlogs), a running spend total is printed after each run, and LiteLLM spend is checked before every Claude call. If AWS spend passes $120, triage switches to a local model (Ollama on the RTX 3050, ~8B quantized).

### Fair before/after
The baseline and final runs both use the exact same models. At the end I re-run the baseline commit with the final model config, so the only difference between "before" and "after" is our code.

## 10. Tech stack and repo

- Python 3.12, `uv` for dependencies
- `openai` SDK for every model (Bedrock OpenAI-compatible endpoint + hackathon LiteLLM), `boto3` Converse as fallback, `neatlogs`
- OpenCode CLI (detector coding agent inside the product)
- DuckDB, `httpx`, `selectolax` (HTML parsing), `datasketch` (MinHash), `jinja2` (report)
- Claude Code with Entire and entire-graph for development

```
bladerunner/
  ingest/        dump loader, dse scraper, mediawiki client
  agent/         investigator loop, verifier, tools, state
  detectors/     agent-written detection scripts (each one checkpointed)
  score/         scorer + answer-key findings checklist
  report/        html/markdown templates
  live/          watch mode
  business/      cfo.ai plan export
  runs/          per-run summaries + neatlogs trace links
  hackathon.md   build log (neatlogs skill format)
  README.md
```

Public GitHub repo, created after 12:30 PM Oct 10 so every commit falls inside the event window.

## 11. Timeline (IST)

**Sat Oct 10**
- 1 to 3 PM: repo, Entire, Graph, neatlogs, Bedrock smoke test (all day-1 checks), first X post
- 3 to 7 PM: ingest finished, DuckDB built, labels joined, scorer working against a dumb baseline
- 7 PM to 1 AM: detective loop with core tools, first full run (run 1 baseline)

**Sun Oct 11**
- Morning: read run 1 traces, fix the top problems, add the verifier and detector writing through OpenCode with Entire
- Afternoon: runs 2 to 5, report page, the "how we know" chain with `entire why`
- Evening: live mode running against DseWiki, secondary site run, cfo.ai business plan
- Through the day: 3 to 4 X posts with real numbers

**Mon Oct 12**
- Until 3 PM: final runs, freeze the numbers, README with before/after table and links
- 3 to 6 PM: record and edit the demo video
- 7 PM sharp: submit as a quote post of the official neatlogs post (aim for the first 50, since neatlogs and cfo.ai engage with those)

## 12. Demo video (3:00 max)

- 0:00 to 0:20: the hook. The collusion.wiki headline: "18,000 posts. Weeks of manual forensics. Can an agent do it in an hour?"
- 0:20 to 0:50: the agent running. Plan, hunt, a detector being written, verification rejecting a weak claim.
- 0:50 to 1:20: the report. Timeline, campaigns, one finding traced down to log rows, then the detector line, then `entire why`.
- 1:20 to 1:50: neatlogs. The span tree, a failure plus recovery, run 1 vs run N numbers.
- 1:50 to 2:15: the live catch. A real agent that showed up on DseWiki during the hackathon.
- 2:15 to 2:40: cfo.ai. Who pays, pricing, runway.
- 2:40 to 3:00: architecture recap covering all three tools.

The rules require the video to cover, for **each** of neatlogs, Entire and cfo.ai: architecture, implementation, runtime, reasoning and result. The script above hits all five for each.

## 13. Building in public (planned posts, tagged #neatHack)

1. Sat afternoon: "Building an AI detective that hunts rogue AI agents. Starting point: the collusion.wiki dataset."
2. Sat night: first numbers ("baseline finds X% of known agent edits")
3. Sun morning: a neatlogs trace screenshot showing why it failed and the fix
4. Sun: first live catch on DseWiki
5. Sun night: "run 5: X% recall at $Y per investigation"
6. Mon: the "how we know" chain screenshot (Entire)
7. Mon: demo teaser, then the submission

## 14. Submission checklist

- [ ] Public repo, all commits dated Oct 10 to 12
- [ ] Demo video, 3 minutes or less, covering all three tools as required
- [ ] neatlogs trace links: before, after, and the recovery run
- [ ] Entire checkpoint link plus ID (format: entire.io/gh/OWNER/REPO/commit/SHA), plus entire-graph output
- [ ] cfo.ai business plan in `business/`
- [ ] Before/after numbers table in the README
- [ ] Links to the #neatHack posts
- [ ] Quote post on X with team name, project name, repo and demo, sent at 7 PM on Oct 12

## 15. Risks and what we do about them

| Risk | Plan |
|---|---|
| neatlogs doesn't auto-trace the Bedrock Claude client | Manual LLM spans with `neatlogs.trace`. 15 minutes. |
| Entire doesn't capture headless `claude -p` | Agent Integration Protocol, or a semi-interactive session for detector writing |
| Bedrock model access or quota issues | Switch to the next model in the bake-off ranking. Same OpenAI-style client, one config line. |
| Open models are weaker than Opus on long loops | Bake-off picks the best one by score per dollar. Plain-code first pass carries the volume. A verifier from a different family catches bad claims. |
| LiteLLM $20 runs out | Claude is only used for the scored verifier and report write-up. DeepSeek V3.2 takes over as verifier. |
| AWS $153 runs out | Hard per-run token caps and a spend log. Local Ollama for triage past $120. |
| A key leaks into the public repo or Entire transcripts | Keys only in `.env` (gitignored). Never paste a key into a coding-agent prompt. |
| Live wiki goes down or blocks us | Everything is cached after the first fetch. Live mode becomes a replay of the last 14 days. |
| No new agents show up during the event | The benchmark carries the demo. Recent arrivals (Oct 5 to 9) are already in the data. |
| Benchmark labels are imperfect | Report label caveats honestly. Hand-check a sample of 50 disagreements. |
| Agent edits are partly German | Claude reads German fine. The report is in English. |

## 16. Guardrails

- Public data only. We never edit the wiki, never interact with the agents, never probe anything.
- Attribution stays at provider level. No full IPs of humans anywhere public.
- The report states evidence. No accusations against named companies beyond what public evidence shows.
- Strip the dump's canary GUID from anything we publish, and don't redistribute their data. The repo downloads it at setup time.
- Rate-limit the live wiki (1 request every 2 seconds, cached). It's one person's hobby server.
- No secrets in prompts. The repo is public and Entire transcripts are public with it.

## 17. Locked vs open

**Locked when you say yes:** idea, data, architecture, models, sponsor roles, timeline.

**Open (yours to decide):**
1. Project name (Bladerunner is a placeholder)
2. Building in public: yes or no (I recommend yes, it's 15%)
3. The Agent Integration Protocol for Entire as a stretch goal: yes or no (I recommend yes if Sunday goes well)
4. Permission to download the collusion.wiki dump (about 12 MB)
