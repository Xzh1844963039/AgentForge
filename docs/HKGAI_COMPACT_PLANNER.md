# HKGAI Compact Planner Probe

**Date:** 2026-11-09 20:27 (local)
**Status:** PASSED (single-request probe)
**Scope:** Stage 2 structured planning only; Stage 2 as a whole remains NOT PASSED.

## 1. Why this probe exists

The full Coordinator planning prompt previously failed against HKGAI with no
HTTP status, headers, SSE bytes, usage or response model after roughly 58–68s.
Control experiment B used the real `PLANNER_PROTOCOL` and a minimal one-task
goal, but its prompt was only 8 characters shorter than the failed Stage 2
prompt (2,290 vs 2,298 chars), so it did not test whether a genuinely smaller
planning prompt can complete.

The Compact Planner reduces the planning context instead of only simplifying the
natural-language goal:

| Item | Full protocol | Compact protocol |
|---|---:|---:|
| System prompt | 1,213 chars | 470 chars |
| Agent catalog | full descriptions | agent id + capability tokens |
| Total measured prompt (minimal one-task goal) | 2,290 chars | 889 chars |
| Total reduction | — | 61.2% smaller |

The compact prompt still requires a JSON object with `goal_summary` and
`subtasks`, capability tokens from the catalog, an acyclic `depends_on` DAG,
`MAX_SUBTASKS`, and no review/finalize task. It is not a hardcoded plan.

## 2. Implementation

- `COMPACT_PLANNER_PROTOCOL` is defined alongside the existing full
  `PLANNER_PROTOCOL` in `agentforge.agents.coordinator`.
- `build_planner_messages(..., protocol="compact")` builds the shorter system
  prompt and a compact `[AGENTS]` catalog.
- `planner_protocol` is configurable through
  `AGENTFORGE_PLANNER_PROTOCOL=auto|full|compact`.
  - `auto` (default): compact for HKGAI, full for DeepSeek/mock.
  - `full`: always use the original protocol.
  - `compact`: always use the compact protocol.
- The normal Coordinator still validates every response with the existing
  `ExecutionPlan` model and `validate_plan` (unique keys, known dependencies,
  acyclicity, `MAX_SUBTASKS`). Capability coverage is still enforced by
  `CapabilityAssigner` in the orchestrator.
- Tool execution, permissions, approvals, sandboxing and workflow semantics were
  not changed.

Offline tests cover single-task planning, a dynamic multi-task DAG, capability
matching and dispatch failure for an uncovered capability, unknown/cyclic
dependencies, `MAX_SUBTASKS`, malformed JSON, JSON repair, prompt-size reduction
and automatic protocol selection. They are in
`tests/test_phase6a_compact_planner.py` (10 tests).

## 3. Authorized live request

Exactly **one** new HKGAI request was made, with no repair request and no retry:

```powershell
python -m agentforge benchmark hkgai-compact-planner `
  --provider hkgai --allow-live-api --timeout 120 --output-dir benchmarks/runs
```

Request invariants:

- Provider/endpoint: configured HKGAI only; no model switch.
- Configured model: `t2_hkgai-v3_fp8_1m_e7`.
- Mode: non-streaming.
- `max_tokens`: 768.
- Client timeout: 120s.
- Retries: 0.
- Tools: none.
- Budget: 1 request, 10,000 tokens.
- No Stage 3–6 activity.

## 4. Result

| Metric | Value |
|---|---|
| Status | **PASSED** |
| HTTP status | 200 |
| Response model | `t2_hkgai-v3_fp8_1m_e10_meeting` |
| Elapsed | 13.5s |
| Prompt chars | 889 (system 470, catalog 322) |
| Tokens | 300 prompt + 344 completion = 644 |
| `finish_reason` | `stop` |
| JSON valid | true |
| `ExecutionPlan` valid | true |
| Capability coverage valid | true |
| Subtask count | 1 |
| Requests used | 1 / 1 |
| Token budget used | 644 / 10,000 |

Returned plan:

```json
{"goal_summary":"Create notes/simple.md containing the text \"hello\".","subtasks":[{"key":"k1","description":"Create the file notes/simple.md, creating the notes directory if needed, with exactly the content: hello","required_capabilities":["coding"],"depends_on":[],"kind":"coding"}]}
```

Validation used the same `extract_json_payload`, `ExecutionPlan.model_validate`,
`validate_plan(..., max_subtasks=3)` and `CapabilityAssigner` code paths as the
normal runtime. The probe additionally requires exactly one subtask because the
goal is intentionally minimal.

Raw evidence:
`benchmarks/runs/hkgai_compact_planner_20261109_202704/compact_planner.json`
and `COMPACT_PLANNER_REPORT.md`.

## 5. Interpretation and limits

What this result establishes:

- The current HKGAI endpoint/model can return a complete, valid, capability-matchable
  execution plan for a minimal one-task goal when the planning prompt is 889 chars.
- A genuinely smaller planning prompt avoids the pre-response disconnect observed
  on the full ~2,290-char prompt in this specific one-request probe.

What it does **not** establish:

- Full Stage 2 is **not** marked passed. The original full-protocol Stage 2
  requests still failed before response headers, and complex/multi-task goals,
  broader capability coverage, repair behavior and production planning prompts
  have not been validated live.
- Dynamic multi-task DAGs are covered by offline tests and the unchanged
  `validate_plan` / orchestrator logic, not by this single-task live response.
- Stages 3–6, real-LLM HITL, memory reuse, multi-agent collaboration and full
  benchmark performance remain **NOT VERIFIED**.
- One successful request is not a reliability or exactly-once guarantee.

## 6. Recommendation

Use the compact protocol as the default/recommended planning mode for HKGAI
(`planner_protocol=auto` already does this), while keeping the full protocol
available for other providers and for comparison. Before declaring Stage 2
complete, run the full staged acceptance with fresh authorization and include at
least one multi-subtask goal so the DAG and capability-coverage path is exercised
live. The gateway's behavior on full/long prompts still needs a provider answer.

## 7. Runtime integration and Stage 2 outcome (2026-11-10)

The compact planner is now wired into the real runtime, not only the probe:

- `runtime.py` resolves `AGENTFORGE_PLANNER_PROTOCOL=auto` → compact for HKGAI
  and full for other providers; the same resolution applies to the benchmark
  single-agent path. There is no second Coordinator entry point with a hardcoded
  protocol; the diagnostic probe modules still import the full
  `PLANNER_PROTOCOL` deliberately, and are not part of the runtime.
- `AGENTFORGE_AGENT_PROTOCOL=auto` adds a minimal HKGAI action prompt: ~1,110-char
  system prompt (one-line tool catalog + compact action protocol), a bounded
  tool ledger instead of a growing conversation, and a 214-char no-tools prompt
  for synthesis/review/finalize. The full action protocol and full tool schemas
  remain the default for other providers.
- Truncated (`finish_reason=length`) replies are never echoed into a repair
  request; repairs re-ask from the original prompt.

Result of the bounded real Stage 2 run (10/12 requests, 4,057/20,000 tokens):
live A/B/C passed (real provider, valid plan, capability dispatch to `coding`);
D–G did not run because every follow-up request in a session was cut by the
gateway at ~56–64s with no response. Planning itself succeeded 4 of 6 times
(913-char prompt, HTTP 200, ~6–8s), so the remaining blocker is the gateway's
handling of the second and later requests, not the compact planner. Full details:
`docs/HKGAI_STAGE2_REAL_RUN.md`.
