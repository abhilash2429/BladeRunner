# Bladerunner business plan

The financial model lives in cfo.ai (workspace "Abhilash CFOAI", scenario "Main Scenario", monthly, Nov 2026 to Dec 2027). This file records the assumptions fed into it and the outputs it produced, so the numbers can be checked against the repo.

## Product and buyer

Bladerunner is an operator-side watchdog for teams running AI agent fleets in production. It sits on top of neatlogs traces, checks every run against the agent's policy, quarantines violating runs with cited evidence, and ships a tighten-only guardrail patch that is confirmed against ground truth before it counts as fixed.

Buyer: the platform or ML engineering lead at a company running 5 to 500 agents against customer data or external tools. They already pay for observability and model spend. They lack a policy check and a closed fix loop.

## Pricing

| Tier | Price | Included | For |
|---|---|---|---|
| Developer | $0 | 1 agent role, 10,000 traces per month, detection and incident reports | Trial and open source users |
| Team | $299 per month | 10 agent roles, 100,000 traces per month, quarantine, neatlogs write-back, 25 auto-patch cases per month | Teams with one agent product in production |
| Scale | $1,499 per month | Unlimited roles, 1,000,000 traces per month, unlimited auto-patch, SSO, bring-your-own models | Companies with several agent products |

Overage on paid tiers: $3 per additional 1,000 traces.

## Unit costs

Measured in this repo, from `runs/spend.jsonl` and the benchmark:

- Local rule evaluation runs on every trace and costs no model spend.
- Only flagged traces reach the investigator and verifier. Measured cost per investigated case on Oct 11: $0.0097 (Kimi K2.5 investigator plus DeepSeek verifier on Bedrock).
- Benchmark cost per trace, all traces included: $0.0058 with the Claude Sonnet 4.6 verifier (bench/results/final.json).
- An auto-patch run (OpenCode on GLM-5) plus confirmation re-runs: modeled at $0.25 per case, a conservative ceiling.

Modeled cost of goods per customer per month:

| Tier | Model and patch spend | Hosting and storage | Total COGS |
|---|---|---|---|
| Developer | $2 | $3 | $5 |
| Team | $15 | $20 | $35 |
| Scale | $90 | $110 | $200 |

## Go to market

- Month 1 to 3: neatlogs integration partners and the neatHack community. Developer tier signups convert to Team.
- Month 4 onward: content on agent incidents (the DSEWiki case is the reference story), plus direct outreach to teams already on neatlogs.

## Growth assumptions

| Assumption | Value |
|---|---|
| Start | November 2026 |
| New Team customers | 2 in month 1, growing by 1 more each month, capped at 12 per month |
| New Scale customers | 1 every other month from month 4, 1 per month from month 10 |
| Monthly logo churn | Team 4%, Scale 2% |
| Overage revenue | 5% of subscription revenue |

## Operating costs

| Item | Monthly cost | Starts |
|---|---|---|
| Founder salary | $3,000 | Nov 2026 |
| Founding engineer | $7,000 | May 2027 |
| Developer relations hire | $5,000 | Sep 2027 |
| Tools, legal, accounting | $600 | Nov 2026 |
| Marketing | $1,000, rising to $3,000 from May 2027 | Nov 2026 |

## Funding

Opening cash: $60,000 (founder savings plus a pre-seed angel round), November 2026. No further raise is assumed in the model.

## Outputs from cfo.ai

Read from the "Bladerunner business plan" page in cfo.ai (Main Scenario), Nov 2026 to Dec 2027.

| Output | Value |
|---|---|
| MRR, Dec 2027 | $39,089 |
| Total revenue, 2027 | $239,762 |
| Gross margin, Dec 2027 | 88.4% |
| Peak monthly net burn | $4,042 (Nov 2026) |
| Lowest cash balance | $50,564 (Jan 2027) |
| First month with positive net cash flow | Feb 2027 (+$505). May 2027 dips to -$2,011 when the founding engineer starts and marketing steps up, then stays positive from June 2027. |
| Customers, Dec 2027 | 94 Team, 7 Scale |
| Cash, Dec 2027 | $117,594 |

Runway never binds in this plan: the lowest cash balance is $50,564 against a peak burn of $4,042 a month, which is over 12 months of cover at the worst point.

How cfo.ai read the assumptions:
- Developer tier customers are free and modeled at zero revenue and zero COGS.
- Churn applies to opening customers each month, so customer counts are fractional in the model.
- Scale customers land in Feb, Apr and Jun 2027, then every month from Aug 2027.
- Overage is modeled as 5% of subscription MRR, without a per-trace volume model.
- Revenue is collected in the month it is earned.
