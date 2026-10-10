# Day-1 results (Oct 10, 2026)

Probes live in `scripts/day1/`. Raw dumps are in `runs/raw/` (gitignored); recorded fixtures are in `tests/fixtures/neatlogs/recorded/`.

## Bedrock
- OpenAI endpoint, tool_calls yes/no, one call per model with a `get_value` tool: (observed in console, not saved)
  - `minimax.minimax-m2.5`: yes
  - `moonshotai.kimi-k2.5`: yes
  - `zai.glm-5`: yes
  - `deepseek.v3.2`: yes
  - `zai.glm-4.7-flash`: yes
- Converse (`bedrock-runtime`, us-east-1, `minimax.minimax-m2.5`): OK, usage returned as `inputTokens/outputTokens/totalTokens`. (observed in console, not saved)
- Response usage on the OpenAI endpoint has `completion_tokens/prompt_tokens/total_tokens` only (no token detail objects). (observed in console, not saved)

## LiteLLM
- Models listed: `nova-micro`, `nova-lite`, `nova-pro`, `nova-2-lite`, `claude-haiku`, `claude-sonnet-4.6`, `claude-opus-4.6`. (observed in console, not saved)
- Sonnet tool calling: yes (`claude-sonnet-4.6` returned `tool_calls`). Usage includes `cache_creation_input_tokens` / `cache_read_input_tokens`. (observed in console, not saved)
- `/key/info` returns spend: yes. Fields under `info`: `spend`, `total_spend`, `max_budget` (20.0), `budget_duration` ("30d"), `budget_reset_at`, `rpm_limit` (60), `tpm_limit` (null), `model_spend` ({}, not populated). The response also echoes a key hash at top level, never log or store the raw response. Spend after the day-1 calls was 0.0027588 and updated immediately. (observed in console, not saved)

## neatlogs SDK and visibility (neatlogs 1.4.26)
- Signatures: (evidence: `tests/fixtures/neatlogs/recorded/day1_logs/neatlogs_signatures.txt`, regenerated offline from the installed package, not from the original run)
  - `init(api_key=None, endpoint=None, workflow_name=None, user_id=None, tags=None, instrumentations=None, sample_rate=1.0, batch_size=100, flush_interval=5.0, debug=False, disable_export=False, capture_logs=False, log_level='INFO', mask=None, pii_enabled=None, pii_entities=None, pii_span_types=None, tracer_provider=None, isolate=None, register_shutdown_handlers=True, uploads_enabled=None, ...)`
  - `span(kind, name=None, *, description=None, version=None, tags=None, capture_input=None, capture_output=None, capture_stdout=False, mask=None, role=None, goal=None, tool_name=None, parameters=None, session_id=None, end_user_id=None, end_user_metadata=None)`. `kind` is the only positional-required argument; `name`, `tool_name`, `session_id` exist exactly as the plan assumes.
  - `trace(name, kind=None, ..., session_id=None, end_user_id=None, end_user_metadata=None, **attributes)`
  - `identify(session_id=None, end_user_id=None, end_user_metadata=None)` (context manager)
  - `wrap(client, **workflow_attributes)`, `flush(timeout_millis=30000) -> bool`, `shutdown(timeout_millis=30000, termination_reason='shutdown')`
- Trace visible after (s): 3.3, measured from worker thread start (t0), not from span end; the TOOL span was exported by `neatlogs.flush()` immediately after it ended, while the WORKFLOW span was still open. Tool span after (s): NOT VISIBLE. See the blocker below. (observed in console, not saved)
- Visible while workflow open: the trace row yes; spans no (`/spans` and `/raw-spans` return 409 TRACE_PROCESSING). (observed in console, not saved)
- Recorded fixtures for this state: `spans_409_processing.json`, `raw_spans_409_processing.json`, `mcp_trace_context_processing.json`. There are no real spans or payload fixtures yet.
- BLOCKER, ingest processing never completed: every trace from today (5 traces, oldest ingested at 16:17Z, last checked 16:49Z, 32+ minutes) is still `finalizationStatus: "pending"` with `spansCount: 0`, `GET /traces/{id}/spans` and `/raw-spans` return `409 TRACE_PROCESSING` (header `Retry-After: 1`), and MCP `get_trace_context` returns `status: "processing"`, `span_count: 0`, `spans: []`, `verification_ready: false`. `neatlogs doctor --probe` also fails with `TRACE_READBACK_TIMEOUT`, `current_stage: raw_durable`, `ingestion_state: processing`. So the data reaches durable raw storage and the SDK export works, but the projection that serves spans, payloads and detections has not run. Either a platform-side stall or a slow queue. Facts that depend on spans (span JSON keys, payload `fields` param, `target_host` attribute visibility via MCP, detection hit latency) are UNVERIFIED. Rerun `scripts/day1/collect_final.py <trace_id>` (traces `799e6fc99b3ee9e28124102b6c4869a4`, `25881a8c1ca64f97bda35464386de3ba`) and `scripts/day1/check_mcp.py <trace_id>` later; they record the fixtures. (evidence: `tests/fixtures/neatlogs/recorded/day1_logs/stall_check_1649Z.txt` shows trace `799e6fc9...` still pending with `/spans` 409 at 16:49Z, `tests/fixtures/neatlogs/recorded/day1_logs/visibility_run1_second_attempt.txt` shows the 409s while the workflow was open and the `session/tags` readback; the 5-trace listing, `doctor --probe` output and the 32-minute timeline were observed in console, not saved)
- sessionId populated: yes, but late. The row first shows `sessionId` equal to the trace id, and later shows the WORKFLOW `session_id` (`day1-<epoch>`) once the workflow span has ended. tags populated: yes (`init(tags=["agent:day1_agent"])` appears as `tags`). (observed in console, not saved)
- `traceName` was the name of the first exported span (`day1_notes_write`, the TOOL span), not the WORKFLOW span name, observed on one trace, while the workflow was still open. `workflowName` comes from `init(workflow_name=...)`. (observed in console, not saved)
- `GET /traces?from=` filters on `startedAt` (a trace with startedAt before `from` and ingestedAt after `from` was excluded). Default list order is newest first. Response shape: `data.traces[]`, `data.page.{hasMore,limit,nextCursor}`, `requestId`, `success`. Trace row keys: `completedAt durationMs finalizationStatus framework ingestedAt llmCallsCount sessionId spansCount startedAt status tags toolCallsCount totalCostUsd totalTokens traceId traceName workflowName` (plus `metadataRevision`, `metadataUpdatedAt` on `GET /traces/{id}`). A `tag` query param is rejected (400 unknown field). (observed in console, not saved) (evidence for the row shape only: `traces_list.json`, `trace.json`)
- Span JSON keys (id, parent, name, kind, start, input, output): UNVERIFIED (blocked).
- Payload endpoint `fields` param works: UNVERIFIED (blocked).
- opentelemetry importable: yes (`opentelemetry.trace.get_current_span().set_attribute` is available).
- MCP at `https://ingest.neatlogs.com/mcp` (Bearer ingest key) works. Tools: `ping whoami log_trace get_trace_context search_traces list_detections get_detection_trend list_detection_groups create_detection_group preview_detection create_detection triage_list triage_get triage_get_investigation triage_list_steps triage_list_versions triage_get_version triage_accept triage_dismiss triage_update_status triage_update triage_restore_version`. (evidence for the tool list: `tests/fixtures/neatlogs/recorded/day1_logs/visibility_run1_second_attempt.txt`)
- `get_trace_context` input: `trace_id` (string) plus optional `created_after`, `verification_marker`, `candidate_offset`, `limit` (1-200), `offset`, `span_types`, `span_names`, `fields` (enum `input_value output_value code_filepath code_function code_lineno`), `max_chars_per_field`, `context_revision`. No `required` list, so the brief's `required[0]` fallback resolves to `trace_id`. Response is a JSON string with `trace_context_contract_version`, `status`, `finalization_status`, `span_count`, `span_payload_complete`, `span_tree_complete`, `verification_ready`, `detections`, `spans`, and more. (observed in console, not saved)
- MCP get_trace_context contains target_host: no (the trace was still `processing`, so `spans` was empty). UNVERIFIED for finalized traces. (observed in console, not saved)
- Client library note: the installed `mcp` is 2.3.0. `streamablehttp_client` no longer exists, use `mcp.client.streamable_http.streamable_http_client(url, http_client=httpx2.AsyncClient(headers=...))`, which yields `(read, write)`. `Tool.inputSchema` is now `Tool.input_schema`.

## Write surface
- PATCH tags: status 200 with `{"expectedRevision": rev, "tags": [...], "traceName": ...}`. Tags are REPLACED, not merged (`['agent:day1_agent']` became `['day1-probe']`). `traceName` also changed. `metadataRevision` increments (0 then 1), so each write needs the current revision. PATCH works while `finalizationStatus` is `pending`. (observed in console, not saved)
- Service-account trace feedback status: 403 FORBIDDEN ("The public API credential is not authorized for this operation"). (observed in console, not saved)
- `GET /traces/{id}/comment-threads`: 200, empty `data.threads`. (observed in console, not saved)
- CLI feedback via OAuth: works NO as tested here. `neatlogs auth status` reports `authenticated: false` (profile `heat`) and `neatlogs feedback create` fails with `MISSING_STORED_CREDENTIAL`. OAuth login (`neatlogs auth login`) is interactive and was not done in this shell. Needs a real-terminal check by the user.
- Name shadowing: inside `uv run`, `neatlogs` resolves to the Python SDK console script (`.venv\Scripts\neatlogs.EXE`, subcommands `doctor` and `trace` only), not the Node CLI (`C:\Users\abhii\AppData\Roaming\npm\neatlogs.CMD`). Any code shelling out to the CLI must resolve it with the venv Scripts dir removed from PATH (done in `check_writes.py`).

## Detections
- Group id stored in .env: yes (`NEATLOGS_DETECTION_GROUP_ID`, group `bladerunner`, created by `scripts/day1/create_group.py` via `POST /detection-groups` `{"name": ...}` returning 201; listing is `GET /detection-groups` -> `data.groups[]`). (observed in console, not saved)
- Regex accepted / hit latency (s): accepted (201, `POST /detections`, fields as in the brief); hit latency NOT OBSERVED: no hit within 180 s on two runs because ingest processing never completed. (evidence: `tests/fixtures/neatlogs/recorded/day1_logs/detection_run1.txt`, `detection_run2.txt`)
- Conditional accepted / hit latency (s): accepted only in the flat form. The brief's `{"type": "rule", ...}` inside `config.conditions[]` is rejected (400 `unrecognized_keys` at `config.conditions.0`). Working form: `"config": {"conditions": [{"field": "...", "operator": "not_in", "value": [...]}], "logic": "AND"}` (201). The `type: "rule"` key is only valid inside the nested `root` tree form (`root.children[]` with `type: "rule"` / `type: "group"`) (inference, untested; read from `neatlogs schema show post_api_v1_public_detections`). Hit latency NOT OBSERVED (same blocker). (evidence for the 400 and the 201: `tests/fixtures/neatlogs/recorded/day1_logs/detection_run1.txt`, `detection_run2.txt`)
- Hits order: UNVERIFIED (no hits).
- Detection creation response keys: `createdAt displayName groupId groupName id isActive slug threshold type updatedAt`; list is `GET /detections?groupId=` -> `data.detections[]`; hits is `GET /detections/{id}/hits` -> `data.hits[]`, `data.page`. (evidence: `tests/fixtures/neatlogs/recorded/day1_logs/detection_run1.txt`, `detection_run2.txt`, response bodies truncated at 300 chars in the log)

## Brain
- Works from this shell with `env=tool_env()` (no sandbox issue). (observed in console, not saved)
- There is NO `brain_remember` tool. The server exposes 36 tools: `brain_status brain_refresh brain_brief brain_query brain_search brain_vsearch brain_get brain_multi_get brain_context brain_impact brain_changes brain_code brain_index_status brain_index_repository brain_list_projects brain_delete_project brain_search_code brain_search_graph brain_query_graph brain_get_graph_schema brain_get_code_snippet brain_trace_path brain_dead_code brain_detect_changes brain_get_architecture brain_ingest_traces brain_tests brain_boundaries brain_regressions brain_review brain_workspace_regressions brain_workspace_graph brain_workspace_review brain_patterns brain_entity_history brain_patterns_status`. Anything planned around `brain_remember` has to change (full schemas in `runs/raw/brain_tools.txt`). (evidence: `tests/fixtures/neatlogs/recorded/day1_logs/brain_tools.txt`)
- brain_query params: `query` (string, required); optional `source` (enum `all fact history conversation doc`), `branch`, `limit` (1-10000, max 50 when `concepts` is set), `keyword` (bool), `semantic` (bool, mutually exclusive with `keyword`), `concepts` (string[1..4], conversation source), `agent`, `after`, `before`, `session_id`, `include_abstract`. `brain_search` and `brain_vsearch` take the same shape minus `keyword`/`semantic`. (evidence: `tests/fixtures/neatlogs/recorded/day1_logs/brain_tools.txt`)
- Write-like tools: `brain_refresh` (`force`, `semantic`, `worktree`), `brain_index_repository` (`path`, `profile`, `force`, `worktree`), `brain_ingest_traces` (`path`: local JSON or NDJSON trace file, required), `brain_delete_project` (`confirm`, `repo_key`). No tool writes free-form memory entries. (evidence: `tests/fixtures/neatlogs/recorded/day1_logs/brain_tools.txt`)
- `brain_brief`: `task` (required), `limit`, `packet_format` (`legacy_json compact_v1 compact_v2 compact_v3`). `brain_get`: `id` (required), `branch`, `limit`, `after_turn`, `context_before`, `context_after`. `brain_multi_get`: `ids` (required, 1-50).
- `brain_status` right now: repo key `gh/abhilash2429/bladerunner`, manifest absent, all sources (`seed sessions semantic history facts docs`) false, daemon unsupported on Windows. Needs `brain_refresh` before queries return anything.
- print-config output (`entire brain mcp --print-config`): (observed in console, not saved)
  ```json
  {
    "mcpServers": {
      "entire-brain": {
        "args": ["mcp"],
        "command": "C:\\Users\\abhii\\AppData\\Local\\entire\\plugins\\bin\\entire-brain.exe",
        "env": {
          "ENTIRE_PLUGIN_CACHE_DIR": "C:\\Users\\abhii\\.cache\\entire",
          "ENTIRE_PLUGIN_CONFIG_DIR": "C:\\Users\\abhii\\.config\\entire",
          "ENTIRE_PLUGIN_DATA_DIR": "C:\\Users\\abhii\\AppData\\Local\\entire\\plugins\\data\\brain",
          "ENTIRE_PLUGIN_STATE_DIR": "C:\\Users\\abhii\\.local\\state\\entire",
          "ENTIRE_REPO_ROOT": "C:\\Users\\abhii\\Batcave\\Projects\\bladerunner"
        },
        "type": "stdio"
      }
    }
  }
  ```

## OpenCode + Entire
- glm-5 resolved: yes (`amazon-bedrock/zai.glm-5`).
- First attempt: the edit succeeded but no checkpoint was created. Root cause: Entire's generated OpenCode plugin (`.opencode/plugins/entire.ts`) spawns `sh -c "... entire hooks opencode <hook>"`, and `sh` is not on the user's Windows PATH (only `Git\cmd` is), so the spawn failed silently.
- Checkpoint trailer on post-OpenCode commit: yes, with Git's `bin` dir prepended to PATH. Commit 5f0999f has `Entire-Checkpoint: 01M4K9DRT9D50E4P6DZKP9KCEY`.
- entire why shows prompt: yes (`[AI] by OpenCode, zai.glm-5` plus the prompt).
- Fix: `common.config.tool_env()` now prepends Git's `bin` dir (commit 590bb47), and `scripts/day1/check_opencode.ps1` does the same.

## Decisions taken
- Bedrock OpenAI endpoint is usable for all five candidate models with tool calling; keep the Task 3 `ROLES` on Bedrock, LiteLLM `claude-sonnet-4.6` is available for the stronger roles.
- LiteLLM `/key/info` reports spend and a 20.0 / 30d budget, so it can cross-check `SpendLedger`; `SpendLedger` stays the primary budget source (`model_spend` is empty).
- Any OpenCode run under Bladerunner uses `tool_env()` so `sh` is on PATH, otherwise Entire records no checkpoint.
- Trace tag writes use PATCH with `expectedRevision` and send the full desired tag list (replace semantics).
- Service-account feedback is 403, so trace feedback must go through the CLI with OAuth; CLI OAuth is unverified here, so plan for `--no-cli-feedback` until the user confirms `neatlogs auth status` in a real terminal.
- Conditional detections use the flat `conditions` form with no `type: "rule"`; keep conditional detections off by default (Task 15) until a hit is observed.
- Do not design around `brain_remember`; it does not exist. Use `brain_query` / `brain_brief` for reads and `brain_ingest_traces` for loading trace files.
- Ingest-to-readable latency is unbounded so far (32+ min, nothing finalized). The watchdog design that polls spans, payloads or detection hits cannot be validated until neatlogs processes traces. Needs a decision from the controller before Tasks 4, 12, 13 and 15 build on span reads.
- Shelling out to the neatlogs Node CLI must strip the venv Scripts dir from PATH.
