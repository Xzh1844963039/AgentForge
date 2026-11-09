# HKGAI Stage 2 Control Experiments

Two minimal experiments isolate the most likely causes of the ~65s Stage 2
disconnect. No request has been executed yet; this document defines the prepared
protocol and budget.

## 1. Controlled variables

Both experiments use:

- the same `HKGAIProvider`;
- the same configured model (`t2_hkgai-v3_fp8_1m_e7` unless the course changes it);
- the same Base URL / `/v1/chat/completions`;
- non-streaming mode;
- `temperature=0.2`;
- `max_retries=0`;
- client timeout 120s;
- no tools, no runtime execution, no file/Git side effects;
- no model switch or extra request parameters.

The suite makes at most two HTTP requests total. A failure in A stops the suite
before B; B is gated on a persisted `a_status=passed`.

## 2. Experiment A — minimal structured output

Purpose: determine whether the model can return one simple JSON object at all,
without planning, tools, agent catalogs, or a long system prompt.

Messages:

- System: "You are a JSON-only endpoint. Return exactly one JSON object and
  nothing else. No Markdown fences, no commentary."
- User: return exactly `{"status": "ok", "value": 1}`.

Validation:

- JSON extraction succeeds;
- value is an object;
- exact keys/values are `status="ok"`, `value=1`;
- no extra keys.

Budget: 1 request, `max_tokens=256`, 120s timeout, no retry.

### Interpretation

| A result | Meaning |
|---|---|
| PASS | Authentication, route, minimal structured JSON and short reasoning work. The failure is more likely prompt/planning complexity or generation time. |
| FAIL with HTTP 4xx/5xx | Request/model/route or gateway error independent of planning; capture status/body. |
| FAIL with transport/empty | Even minimal structured requests cannot complete through this route/model; likely gateway/model route issue. |
| FAIL schema only | Content returned but does not satisfy the simple JSON contract; structure capability or formatting issue. |

## 3. Experiment B — real PLANNER_PROTOCOL, minimal task

Purpose: isolate user-task complexity while keeping the exact production
Coordinator system prompt.

Messages:

- System: the exact current `PLANNER_PROTOCOL` imported from
  `agentforge.agents.coordinator`.
- User: same format as production, with:
  - goal: `Create notes/simple.md containing "hello".`
  - the same default agent catalog;
  - original `MAX_SUBTASKS=3`; the model is asked to produce one minimal task;
  - the same "Return the JSON execution plan now" instruction.

Validation:

- JSON extraction;
- `ExecutionPlan` schema validation;
- `validate_plan(max_subtasks=3)` with exactly one resulting subtask;
- exactly one subtask;
- full capability coverage by a default agent (CapabilityAssigner);
- non-empty goal summary.

Budget: 1 request, `max_tokens=512`, 120s timeout, no retry.

### Interpretation

| A | B | Meaning |
|---|---|---|
| PASS | PASS | Real Planner protocol works for a minimal DAG; the original Stage 2 failure is more likely task complexity / longer reasoning / gateway response timing than an invalid request or broken parser. |
| PASS | FAIL with response JSON but schema error | Model can do JSON but not the full planning contract; prompt/schema guidance or model planning capability issue. |
| PASS | FAIL with no HTTP response | Even a minimal one-task Planner prompt triggers the upstream disconnect; the problem is more likely Planner system prompt size/processing or gateway behavior, not user task complexity. |
| FAIL | not run | Structured output itself is blocked; do not run B. |

## 4. Why this design

- A is the smallest possible structured-output test. If it fails, further
  planning-prompt tuning cannot be blamed first.
- B changes exactly one factor relative to the failed Stage 2: user task
  complexity (and max subtasks), while preserving the real system prompt and
  agent catalog.
- Both use non-streaming to match the original failed path. The earlier streaming
  probe showed no response headers at all, so streaming is not assumed to help.
- No tools or side effects means a failure cannot be attributed to ToolExecutor or
  sandbox code.
- Sequential gating prevents spending the second request after an A failure.

## 5. Execution protocol (not yet authorized)

Commands:

```powershell
# Run A only
python -m agentforge benchmark hkgai-experiments --provider hkgai `
  --experiment A --allow-live-api --output-dir benchmarks/runs

# If A passed, run B (or run AB after authorization)
python -m agentforge benchmark hkgai-experiments --provider hkgai `
  --experiment B --allow-live-api --output-dir benchmarks/runs
```

For a single authorized suite:

```powershell
python -m agentforge benchmark hkgai-experiments --provider hkgai `
  --experiment AB --allow-live-api --output-dir benchmarks/runs
```

Budget: **2 HTTP requests maximum**, 512 completion tokens maximum per request,
120s timeout, zero retries. The runner stops after A fails; B is refused unless
the persisted state says A passed.

Output:

```text
benchmarks/runs/hkgai_experiments_<timestamp>/
├── experiments.json
└── EXPERIMENTS_REPORT.md
benchmarks/runs/hkgai_experiments_state.json
```

No key is stored. Error text is key-redacted. Content preview is limited; no full
prompt/response is written.

## 6. Offline tests

`tests/test_phase6a_experiments.py` verifies:

- A validation accepts only the exact minimal object;
- B validation accepts one valid coding task with no dependencies;
- B rejects multi-task/invalid dependency plans;
- B is refused before A passes;
- real calls require explicit authorization.

No real request is made by these tests.

## 7. Executed results (2026-11-09 20:00)

Both authorized requests were executed. A passed; B failed and the suite stopped.

| Experiment | Status | HTTP | Elapsed | Tokens | JSON | Schema |
|---|---|---|---:|---:|---|---|
| A — minimal JSON | **PASSED** | 200 | 6.406s | 186 | true | true |
| B — real Planner, simple task | **FAILED** | none | 58.375s | 0 | false | false |

A returned exactly `{"status":"ok","value":1}`. Response model:
`t2_hkgai-v3_fp8_1m_e10_meeting` (configured alias `...e7`).

B received no HTTP response headers, no bytes, no usage, and disconnected with
`RemoteProtocolError: Server disconnected without sending a response` after
58.375s.

### Prompt-size comparison

| Prompt | Chars |
|---|---:|
| A system + user | 200 |
| B real `PLANNER_PROTOCOL` + full catalog + simple task | 2,290 |
| Previous full Stage 2 prompt | 2,298 |

B is only 8 characters shorter than the failed full Stage 2 prompt. Therefore B
did not materially isolate task complexity by prompt length; it isolated a short
structured request (A) from the full Planner system/catalog/Schema structure. The
failure correlates with the full planning prompt, not with the specific task
wording.

No AgentForge request-construction defect was demonstrated. The next useful
control (not authorized/run) would separate:
1. real system prompt + minimal catalog;
2. real catalog + minimal system prompt;
3. real system + catalog with plain-text response request.

Stage 3–6 not started. No Push.
