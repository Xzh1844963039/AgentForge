# Phase 6A Report — Real-LLM Acceptance Readiness & Integration Hardening

**Date:** 2026-11-09
**Phase:** 6A — Real-LLM acceptance framework, provider hardening, final readiness
**Environment:** Windows 11 (10.0.26200), Python 3.12.10, Docker Desktop 29.9.2
**Version:** 0.6.0
**Status:** Engineering complete for offline validation. **Real-LLM stages are
NOT_VERIFIED: no API key was configured and no paid API call was made.**

---

## 1. Executive summary

Phase 6A does not pretend to validate a real model without credentials. It:

- audits and hardens the DeepSeek/HKGAI HTTP provider path;
- adds bounded, audited structured-output repair;
- adds hard per-task token, workflow LLM-call and repeated-action limits;
- implements the six-stage live acceptance ladder with cumulative request/token
  budgets and explicit `--allow-live-api` authorization;
- adds a live memory-reuse experiment with separate learning and evaluation
  tasks;
- audits the Phase 5 benchmark metrics/verifiers and fixes a budget accounting
  inconsistency;
- expands failure-path tests for provider errors, parsing, staged-live gating and
  benchmark correctness;
- prepares the final evaluation protocol/submission checklist without claiming
  the final course submission is complete.

Current external limitation: DeepSeek and HKGAI credentials are absent. The
staged report therefore has six `NOT_VERIFIED` stages and zero API requests.
No ZCode internal model or other company model was used.

---

## 2. Code changes

### Provider layer

- Base URL handling accepts both `https://host` and a full
  `https://host/v1/chat/completions` endpoint without duplicating the path.
- Response content supports string and content-part list formats.
- API keys are redacted from HTTP error detail.
- 429/408/409/5xx retries remain bounded; 401/403 are not retried.
- Existing no-fallback behavior is preserved: missing keys raise
  `ProviderConfigurationError`.
- Empty/malformed JSON response bodies raise explicit `LLMProviderError`.

### Structured output / agent loop

- Added `structured_output_repairs` (default 1), `require_structured_actions`
  (default true), `max_task_tokens`, `max_llm_calls_per_workflow` and
  `max_repeated_tool_actions`.
- Plain text or JSON without action envelope is repaired in task mode, not
  accepted as success.
- Repair attempts are bounded and audited (`parse_error`, `repair_count`).
- Exhausted repairs fail the task with an explicit error.
- Coordinator planning uses the same bounded repair setting.
- Repeated identical tool calls are stopped before they can loop indefinitely.
- Every task still has tool-call, execution-step and token limits.

### Benchmark / live acceptance

- `LiveBudgetProvider` enforces cumulative request and token budgets shared
  across all live stages.
- `BenchmarkRunner` accepts a shared provider override, enabling cumulative
  live budgets without changing task isolation.
- Added six-stage `LiveStageRunner` (connectivity, structured output, tools,
  collaboration, HITL/memory, full benchmark).
- Stages stop at the first failure; remaining stages are `SKIPPED`.
- Added `LiveMemoryExperimentRunner` with separate learning/evaluation tasks,
  retrieval IDs, applied IDs and action comparison.
- Added `memory_feedback` config to run scoped feedback after evaluation without
  learning.
- Added live tasks for file creation and a memory learning/evaluation pair.
- Added benchmark correctness fixes: budget violations clear `goal_success`;
  reports state sample size/uncertainty and separate fixture token estimates from
  live provider usage.

---

## 3. Provider compatibility audit

| Check | Result |
|---|---|
| DeepSeek endpoint `POST /chat/completions` | ✅ verified with MockTransport |
| HKGAI OpenAI-compatible request path | ✅ shared provider; config requires base URL + model |
| Configurable base URL/model/key | ✅ |
| Custom full endpoint path | ✅ no duplicate `/chat/completions` |
| Response parsing (`choices[0].message.content`) | ✅ string and content-part list |
| Token usage parsing | ✅ prompt/completion/total; missing usage defaults to 0 |
| Timeout | ✅ transport timeout propagates after bounded retries |
| Retry | ✅ 429/5xx/transport retried; 401 not retried |
| Auth error propagation | ✅ `LLMProviderError` with HTTP status; key redacted |
| Rate limit | ✅ 429 retry tested |
| Malformed JSON body | ✅ explicit provider error |
| Silent Mock fallback | ✅ none; factory raises configuration error |
| ZCode internal model | ✅ not used |

Network calls in tests use `httpx.MockTransport`; no external API was contacted.

---

## 4. Staged live acceptance status

Command executed:

```powershell
python -m agentforge benchmark live --stage all --provider deepseek `
  --output-dir benchmarks/runs/live_stages_official
```

Result: all six stages **NOT_VERIFIED** because `deepseek_api_key=False` and
`hkgai` is not configured. No `--allow-live-api` paid call was made.

| Stage | Required condition | Status | Reason |
|---:|---|---|---|
| 1 | Real request, non-empty content, usage | NOT_VERIFIED | no API key |
| 2 | Valid Plan + Action, bounded repair | NOT_VERIFIED | no API key |
| 3 | Real file + Docker tool execution | NOT_VERIFIED | no API key |
| 4 | Delegation + reviewer + evidence | NOT_VERIFIED | no API key |
| 5 | HITL + memory retrieval/reuse | NOT_VERIFIED | no API key |
| 6 | Full live benchmark | NOT_VERIFIED | no API key |

Report:
`benchmarks/runs/live_stages_official/live_stages_20261109_123525/LIVE_STAGES_REPORT.md`

The framework is implemented and offline-tested. It must be run with:

```powershell
python -m agentforge benchmark live --provider deepseek --stage all `
  --allow-live-api --max-requests 120 --max-tokens 250000
```

Only after credentials and explicit authorization exist. No claim is made about
real planning, tool selection, JSON reliability, review quality or memory reuse.

### Why Stage 5 has its own memory experiment

The Phase 5 fixture ablation showed retrieval `5` / used `0` because the fixture
policy did not consume memory. Phase 6A implements a real protocol:

- learn a note convention twice with memory evolution;
- evaluate on a different note task with memory OFF and ON;
- record retrieved IDs, applied feedback IDs and tool-action differences;
- require an objective file verifier for success.

The experiment is not run without an authorized live provider. If the model
never retrieves or never applies the memory, the result is `INCONCLUSIVE`, not
a claimed improvement.

---

## 5. Benchmark correctness audit

| Audit item | Result |
|---|---|
| Fixture pipeline vs real goal completion | Separate labels; fixture reports carry a prominent warning |
| PASSED/FAILED/NEEDS_REVIEW/SKIPPED | Separate statuses; manual review not success |
| `workflow.completed` used as success | No; verifier status is authoritative |
| Research evidence coverage | Seeded facts/citations checked; one open task requires review |
| Sample size/uncertainty | Report states run count and no significance claim |
| Intentional rejections/timeouts counted as crashes | No; expected safe-failure tasks pass only when verifier confirms behavior |
| Mock token stats mixed with real usage | Report distinguishes estimates from provider usage |
| Microbenchmark speedups presented as E2E | No; Docker/model latency separated |
| Git commit/config/provider/model recorded | Yes, in batch manifest/run records |
| Budget violation semantics | Fixed: tool/time/token budget violations clear `goal_success` |

The Phase 5 fixture result remains **33 verified passes + 1 needs-review, 0
unexpected failures**; it is not presented as real-LLM performance.

---

## 6. Memory / ablation status

- Existing fixture A/B/C ablation remains valid as plumbing evidence: A/B/C 6/6,
  multi-agent ~20% more tokens, memory retrieval 5 / used 0.
- Phase 6A adds the live memory protocol but did not execute it (no key).
- Real memory reuse, learning effect and completion/time/token deltas are
  **NOT_VERIFIED**.
- Task-scoped retrieval/feedback remains enabled and was not changed to a
  workflow/global heuristic.

---

## 7. Reliability hardening

Added/audited failure paths:

1. invalid action JSON → bounded repair → explicit failure if exhausted;
2. plain text in task mode → repair, not silent success;
3. provider 429/5xx → bounded retry; 401 → immediate error;
4. provider timeout → bounded retry → error;
5. malformed response body → explicit error;
6. per-task token limit;
7. workflow LLM-call limit;
8. repeated identical tool action limit;
9. live request/token budget;
10. live stage gating and no-key safety;
11. high-risk reconciliation remains: unresolved intent blocks replay;
12. Docker cleanup/container-death recovery test skips honestly if Docker is
    unavailable and passes when available;
13. clarification/approval/cancel/restart/replay behavior remains covered by
    Phase 4/5 tests.

**No universal exactly-once claim.** `tool_reconciliations` still fail closed on
an unknown high-risk outcome.

---

## 8. Tests and quality gate

Final code state:

| Check | Result |
|---|---|
| `pytest -q --cov=agentforge` | **257 passed, 2 skipped** |
| Docker tests | **5 passed** (including container-death recovery) |
| Coverage | **75%** (9,654 statements, 2,458 missed) |
| Ruff F/E9 | **All checks passed** |

Phase 6A adds 15 tests: provider request/response/retry/redaction, full endpoint
URLs, content-part responses, structured repair success/exhaustion, plain-text
rejection, token/workflow limits, live request/token budgets, staged-live no-key
safety, live memory harness and benchmark correctness.

The two skips remain the Windows symlink-privilege test and the Docker-missing
failure-path test (Docker was available for the final run).

---

## 9. Documentation prepared

- `docs/REAL_LLM_ACCEPTANCE.md` — provider config, stages, budgets, memory
  protocol, current NOT_VERIFIED status.
- `docs/PHASE6A_REPORT.md` — this report.
- `docs/FINAL_EVALUATION_PROTOCOL.md` — protocol for the teacher-provided test
  set.
- `docs/FINAL_SUBMISSION_CHECKLIST.md` — remaining submission gates.
- README/ARCHITECTURE/DEVELOPMENT_PLAN updated.

---

## 10. Git

Local commits:

1. `4d4e066` — `feat(live): harden providers and add staged real-LLM acceptance`
2. `18ceabe` — `fix(live): record git commit in acceptance reports`
3. docs commit created after this report was finalized (Phase 6A docs/results)

No remote push. No GitHub remote created. The no-key live reports record code
commit `18ceabe`.

---

## 11. Remaining external conditions

1. Course-authorized **DeepSeek or HKGAI** key, base URL and model. The key must
   be supplied via environment/`.env`; never in chat or logs.
2. Explicit authorization to make paid API calls.
3. A configured request/token budget.
4. Stage-by-stage live runs; fix each stage before advancing.
5. Official teacher-provided test set (not yet available). The protocol supports
   loading it without assuming its content.
6. Human review for open-ended research quality and real-user HITL behavior.

No claim of real-LLM completion, memory benefit, HITL quality or final course
submission readiness is made until these conditions are satisfied.
