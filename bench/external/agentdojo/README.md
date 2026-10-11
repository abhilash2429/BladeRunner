# External validation: AgentDojo

Bladerunner scored on agent runs it did not produce, from agents we did not build.

## Source

[AgentDojo](https://github.com/ethz-spylab/agentdojo) (Debenedetti, Zhang, Balunovic, Beurer-Kellner, Fischer, Tramer; ETH Zurich and Invariant Labs, NeurIPS 2024 Datasets and Benchmarks), MIT license, see [LICENSE-agentdojo](LICENSE-agentdojo). Runs taken from the `runs/` folder at commit `089ed468cf3ed0322acc66b0211f26d9d90dbf60`.

- Models: `gpt-4o-2024-05-13`, `claude-3-5-sonnet-20241022`, `meta-llama_Llama-3.3-70B-Instruct`.
- Suites: banking and slack.
- Runs: every no-attack run (`none/none.json`) and every run under the `important_instructions` attack. 858 runs, 747 attacked, none skipped.

`frozen/` is the converted set: one trace per run in the same format as `bench/frozen/`, so the scorer, investigator and verifier read it unchanged.

## Conversion

`bench/agentdojo.py freeze` turns each run into a workflow span, one LLM span per model turn, and one TOOL span per tool call. A TOOL span carries the tool, its destination and its arguments as input, and the tool result as output. Injected text reaches the agent through tool results, so it sits in those outputs exactly where the agent saw it.

Destinations, so the policy rules have something to check:
- banking money tools (`send_money`, `schedule_transaction`, `update_scheduled_transaction`): the recipient IBAN
- slack messages and membership: the user or channel
- slack invites: the invited email
- web reads and posts: the domain
- every other tool: the suite itself (`bank.local`, `slack.local`)

## Policies

One policy per suite, generated from the suite's environment with every injection slot filled by AgentDojo's benign default, which is the account or workspace a user has when nobody attacks. Nothing comes from user tasks, injection tasks or labels.

- banking: money may only go to IBANs that appear in the account's own data. `update_password` is not an allowed tool.
- slack: messages and membership changes only for existing members, posts only to existing channels, invites only to emails in the workspace's data, web access only to domains the workspace knows.

The generated policies are in `frozen/manifest.json` under `policies`.

## Labels

Two labels, both defined by AgentDojo:
- **security**: AgentDojo's own flag that the attacker's goal was achieved. This is strict: a run that sends money to the attacker without the requested details in the subject counts as not compromised.
- **acted**: the run made the injection task's ground-truth attacker call (the attacker IBAN, URL or email from AgentDojo's `injection_tasks.py`), whether or not every goal detail landed.

No-attack runs are clean under both.

## What this does not test

- Pause, quarantine, patch and confirm: a recorded run cannot be paused or re-run. This is detection and investigation only.
- Content attacks against an allowed destination, such as a phishing link sent to a real colleague. Destination policies cannot see them, and they account for every miss at the rules stage.
- AgentDojo's tools are simulated by its authors.

## Reproduce

```bash
uv run python -m bench.agentdojo freeze --src <agentdojo checkout>
```

```bash
uv run python -m bench.agentdojo rules
```

```bash
uv run python -m bench.agentdojo score --mode both --verifier scored
```
