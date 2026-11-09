# Real-LLM Acceptance (Phase 6A)

This document defines how AgentForge is validated against the authorized
DeepSeek/HKGAI APIs. It also states exactly what remains unverified in the
current environment.

## 1. Provider configuration

Only DeepSeek and HKGAI are permitted. OpenAI/Anthropic endpoints and the ZCode
development model are not runtime providers.

```dotenv
# DeepSeek
AGENTFORGE_LLM_PROVIDER=deepseek
AGENTFORGE_DEEPSEEK_API_KEY=...          # never commit
AGENTFORGE_DEEPSEEK_BASE_URL=https://api.deepseek.com
AGENTFORGE_DEEPSEEK_MODEL=deepseek-chat

# HKGAI (base URL/model must come from the course)
AGENTFORGE_LLM_PROVIDER=hkgai
AGENTFORGE_HKGAI_API_KEY=...
AGENTFORGE_HKGAI_BASE_URL=...
AGENTFORGE_HKGAI_MODEL=...
```

- Base URL, model and key are all configurable.
- `DEEPSEEK_API_KEY` / `HKGAI_API_KEY` are accepted aliases.
- A missing key raises `ProviderConfigurationError`; the runtime never falls
  back to Mock.
- A base URL already ending in `/chat/completions` is accepted without
  duplicating the path.
- Response content may be a string or content-part list; both are normalized.
- Provider errors include HTTP status; the API key is redacted from error text.
- Retryable statuses: 408, 409, 429, 500, 502, 503, 504. Auth errors (401/403)
  and schema errors are not retried.
- Timeouts are configurable and retried up to `AGENTFORGE_MAX_RETRIES`.

## 2. Structured output and repair

Agents must return a single JSON action. In structured mode:

- plain text or JSON without an action envelope is rejected, not accepted as a
  successful completion;
- invalid action payloads trigger at most
  `AGENTFORGE_STRUCTURED_OUTPUT_REPAIRS` repair calls (default 1);
- every repair is audited in `agent_executions.metadata.parse_error` and
  `repair_count`;
- exhausted repairs fail the task with an explicit error; they never invent a
  successful result;
- Coordinator planning uses the same bounded repair count and fails with
  `PlanningError` after exhaustion.

Limits:

| Setting | Default | Purpose |
|---|---:|---|
| `AGENTFORGE_STRUCTURED_OUTPUT_REPAIRS` | 1 | Repair attempts after invalid action/plan |
| `AGENTFORGE_REQUIRE_STRUCTURED_ACTIONS` | true | Reject plain text in task mode |
| `AGENTFORGE_MAX_TASK_TOKENS` | 120000 | Per-task token guard |
| `AGENTFORGE_MAX_LLM_CALLS_PER_WORKFLOW` | 200 | Workflow-wide LLM-call guard |
| `AGENTFORGE_MAX_REPEATED_TOOL_ACTIONS` | 3 | Repeated identical tool action guard |
| `AGENTFORGE_MAX_TOOL_CALLS_PER_TASK` | 6 | Tool loop guard |

## 3. Staged acceptance

Commands require explicit authorization for real API calls:

```powershell
python -m agentforge benchmark live --provider deepseek --stage 1 --allow-live-api
python -m agentforge benchmark live --provider deepseek --stage 2 --allow-live-api
python -m agentforge benchmark live --provider deepseek --stage 3 --allow-live-api
python -m agentforge benchmark live --provider deepseek --stage 4 --allow-live-api
python -m agentforge benchmark live --provider deepseek --stage 5 --allow-live-api
python -m agentforge benchmark live --provider deepseek --stage 6 --allow-live-api
# or all stages in order:
python -m agentforge benchmark live --provider deepseek --stage all --allow-live-api
```

Cumulative budgets:

```powershell
python -m agentforge benchmark live `
  --provider deepseek --stage all --allow-live-api `
  --max-requests 120 --max-tokens 250000
```

If either the key or `--allow-live-api` is absent, no request is made and every
stage is `NOT_VERIFIED`. The command does not read a ZCode internal model.

### Stage definitions

| Stage | Pass condition |
|---|---|
| 1 — Connectivity | Non-empty real response and parsed provider usage |
| 2 — Structured output | Valid Coordinator plan and valid Agent action, with bounded repair |
| 3 — Tool execution | Real file write and Docker Python execution audited by the verifier |
| 4 — Collaboration | Planning + delegation child + independent review complete; at least one delegation |
| 5 — HITL + memory | Clarification and approval tasks pass; live memory experiment observes retrieval |
| 6 — Live benchmark | All live tasks execute; no runtime/skipped errors; real verified success rate recorded |

Each stage must pass before the next; a failure leaves later stages `SKIPPED`.
Stage 6 does not require 100% task success; it reports the honest verified rate.

## 4. Human Simulator

Live HITL tasks use a deterministic `HumanScript`:

- it only supplies approval decisions and clarification answers;
- it never chooses tools, edits files, or generates agent decisions;
- every response is recorded with `simulated=true`;
- real user latency and real user judgment are **NOT VERIFIED**.

## 5. Live memory experiment

`benchmarks/live/memory_tasks.jsonl` contains:

- `live_memory_learning`: create a note containing `verified: yes` and a
  strict convention;
- `live_memory_eval`: create a different note and use the learned convention.

Protocol:

1. Run the learning task twice with memory evolution ON, retrieval OFF.
2. Confirm promoted/candidate memory IDs (if any).
3. Run the evaluation task with memory OFF and again with memory ON.
4. Record retrieved and applied memory IDs and compare tool actions.
5. Verifier decides file success; no answer key is inserted into memory.

If no promoted memory or no retrieval occurs, the experiment is reported
`INCONCLUSIVE`; a single successful ON run is not evidence of a memory benefit.

## 6. Current status (2026-11-09 HKGAI run)

- HKGAI key/base/model: **configured locally in `.env`** (not committed).
- Configured model: `t2_hkgai-v3_fp8_1m_e7`.
- Response-reported model in Stage 1: `t2_hkgai-v3_fp8_1m_e10_meeting`.
- Base URL correction: the course base host required a `/v1` prefix; the initial
  host-only URL returned HTTP 404.
- Stage 1 connectivity: **PASSED** — real non-empty response (`OK`),
  `finish_reason=stop`, usage 125 tokens, elapsed 5.98s.
- Stage 2 structured output: **FAILED** — HKGAI disconnected the connection after
  ~65–67s on the real Coordinator planning prompt
  (`RemoteProtocolError: Server disconnected without sending a response`).
- Optional SSE streaming is implemented (`AGENTFORGE_HKGAI_STREAM` / provider
  `stream=True`) with 10 offline parsing tests.
- Authorized one-request streaming probe: **FAILED after 67.437s**; the server
  sent no HTTP response headers, no SSE bytes, no usage and no model.
- Control experiment A (minimal JSON): **PASSED** — HTTP 200, 6.406s, 186 tokens,
  exact JSON. Response model `t2_hkgai-v3_fp8_1m_e10_meeting`.
- Control experiment B (real Planner prompt, simple one-task goal): **FAILED** —
  no HTTP headers, no bytes, no usage, disconnect after 58.375s. Its prompt is
  2,290 chars vs the previous failed Stage 2 prompt at 2,298 chars, so B did not
  materially reduce prompt size.
- Stages 3–6: **NOT RUN** (Stage 2 did not pass; budget gate respected).
- Actual requests: 5 in Stage 1 work, 4 in Stage 2 work, 1 streaming probe,
  1 A + 1 B control; total 12. Tokens: 562 (376 Stage 1 + 186 A); 0 returned in
  Stage 2/probe/B work.
- Paid calls were limited to the authorized HKGAI endpoint; no other provider was
  used.
- API key scan: not present in tracked files, stage JSON/Markdown, runtime logs or
  benchmark reports.

Required next action:

1. Resolve the HKGAI gateway disconnect on structured planning requests with the
   course provider (supported model/route, gateway timeout or streaming policy).
   The existing 60–90s client timeout is not the limiting factor; the server
   closes the connection.
2. Do not use a different model/provider without explicit authorization.
3. After Stage 2 passes, continue Stage 3 → 4 → 5 → 6 with fresh request/token
   budgets.
4. After the gateway issue is resolved, run the prepared `benchmark hkgai-probe`
   before retrying Stage 2.
5. Real-user HITL and official test-set evaluation remain pending.

## 7. Output and audit

Each staged run writes:

```text
benchmarks/runs/live_stages/<timestamp>/
├── live_stages.json
├── LIVE_STAGES_REPORT.md
├── stage_1_connectivity/...
├── stage_2_structured_output/...
├── ...
└── stage_5_hitl_memory/memory/live_memory_result.json
```

The report records provider, model, key-configured boolean, authorization
boolean, budget usage, per-stage status/detail and memory experiment fields. It
never records the API key.
