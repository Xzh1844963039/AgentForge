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

## 6. Current status (this environment)

- DeepSeek API key: **not configured**.
- HKGAI key/base/model: **not configured**.
- `.env` file: absent.
- Paid API calls made: **0**.
- Stages 1–6: **NOT_VERIFIED**.
- Fixture harness validation: 3/3 (separate from live capability).

Required external conditions to proceed:

1. supply the course-authorized DeepSeek/HKGAI key and endpoint through
   environment variables or `.env` (never paste keys into chat/logs);
2. explicitly authorize paid calls with `--allow-live-api`;
3. set a request/token budget;
4. run Stage 1, then 2, then 3, then 4, then 5, then 6; fix any stage failure
   before advancing.

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
