# HKGAI Real Stage 2 — Runtime Integration Run

**Date:** 2026-11-10 (00:41–01:05 local)
**Status:** **PARTIALLY_VERIFIED** (A/B/C verified live; D–G blocked by gateway)
**Budget used:** 10 of 12 authorized requests, 4,057 of 20,000 returned tokens
**Do not claim:** full Stage 2 PASS, real Agent→Tool→Evidence completion.

## 1. What changed in the runtime before this run

1. **Planner** — `resolve_planner_protocol("auto", provider)` selects the
   889→911-character compact planner for HKGAI; the full planner remains the
   default for other providers and is still selectable.
2. **Agent action prompt** — a minimal compact action protocol plus a one-line
   tool catalog and a bounded tool ledger. Measured system prompt fell from
   ~9,640 to ~1,110 chars; the whole first action prompt is ~1,350 chars instead
   of >5,300.
3. **Policy tasks** (synthesis/review/finalize) use a 214-char no-tools prompt.
4. **Truncation-safe repair** — when HKGAI returns `finish_reason=length`, the
   repair no longer echoes the oversized truncated reply; it re-asks from the
   original prompt. Truncated-content echo was what triggered the first
   pre-response disconnect.
5. **Bounded Stage 2 runner** (`benchmark hkgai-stage2`) — hard caps
   (≤12 requests, ≤1,024 tokens/request, cumulative token cap), provider
   `retries=0`, no model switch, stop-on-disconnect, per-call evidence.

## 2. Observed request pattern (all six runs)

| Run (local time) | Plan call | Follow-up call | Requests | Tokens | A | B | C | D–G |
|---|---|---|---|---:|---|---|---|---|
| 00:41 | HTTP 200, 9.97s, `finish=length` | repair, disconnect 55.5s | 2 | 1,366 | ✅ | – | – | – |
| 00:45 | disconnect 64.0s (first call) | – | 1 | 0 | – | – | – | – |
| 00:47 | HTTP 200, 6.53s, `finish=stop` | action, disconnect 57.3s | 2 | 905 | ✅ | ✅ | ✅ | – |
| 00:56 | HTTP 200, 7.74s, `finish=stop` | action, disconnect 56.3s | 2 | 1,172 | ✅ | ✅ | ✅ | – |
| 01:01 | disconnect 64.0s (first call) | – | 1 | 0 | – | – | – | – |
| 01:03 | HTTP 200, 5.74s, `finish=stop` | action, disconnect 60.5s | 2 | 614 | ✅ | ✅ | ✅ | – |
| **Total** | 4 success / 6 | **0 success / 4** | **10** | **4,057** | | | | |

Follow-up prompt sizes that still disconnected: 1,283 / 5,179 / 1,382 / 1,392
chars. Successful first calls: 913 / 913 / 913 / 1,054 chars. Every failure had
**no HTTP status, no headers, no bytes, no usage, no model**, and the connection
was closed at 55.5–64.0s.

## 3. Root-cause assessment

- The compact planner itself is not the blocker: it returns a valid plan in
  ~6–8s whenever the gateway answers, including through the **real runtime**.
- **Second and later requests of a runtime session never returned** (0/4), while
  first requests returned 4/6. The two failed first requests happened in the run
  immediately following a gateway disconnect.
- Prompt size is **not** the discriminator: a 1.28k-char repair and a 1.38k
  action prompt failed exactly like the 5.2k action prompt; 1.05k first calls
  sometimes succeeded and sometimes failed.
- This retroactively explains the earlier full-planner Stage 2 failures: those
  requests were always issued **after** Stage 1 in the same session, i.e. always
  follow-up requests.
- Best current explanation: the HKGAI gateway serializes (or reuses) upstream
  work per client/key and cuts any request that has not produced a response
  within ~55–65s, including requests queued behind the previous call.

## 4. Verified vs unverified

Live-verified (real HKGAI through the real runtime):

- **A** — runtime used the real `HKGAIProvider`; coordinator selected the compact
  planner and the minimal agent protocol (recorded per call).
- **B** — Coordinator returned a valid plan; `ExecutionPlan` + `validate_plan`
  accepted it; the plan was materialised as one `coding` task.
- **C** — the capability dispatcher assigned the task to `coding` with
  assignment score 1.0.

Not live-verified (budget + gateway):

- **D** — agent structured action (every follow-up call was cut).
- **E** — action schema/permission validation on a live action.
- **F** — sandbox tool execution with this workflow.
- **G** — tool evidence reaching synthesis/finalize in this workflow.

Offline-verified equivalents (mock provider, real dispatcher/Docker/audit):
`demo-tools` executed `python_execute` in Docker (exit 0), and the persisted
audit evidence appeared in the reviewer verdict and final report; the Stage 2
runner's own A–G evaluator passes end-to-end with a scripted provider. These are
**not** real-LLM evidence and are not presented as such.

## 5. Budget accounting

- Authorized for this instruction: ≤12 requests, ≤1,024 tokens/request,
  ≤20,000 cumulative returned tokens, ≤120s timeout, `retries=0`, HKGAI only.
- Used: **10 requests, 4,057 returned tokens** across six runner invocations.
- Remaining: 2 requests — insufficient for the minimum 5-call A–G workflow, so
  online requests were stopped instead of repeating the plan call.
- No model or provider switch; no API key logged or committed.

## 6. Evidence files

`benchmarks/runs/hkgai_stage2_20261110_004112/`
`benchmarks/runs/hkgai_stage2_20261110_004517/`
`benchmarks/runs/hkgai_stage2_20261110_004746/`
`benchmarks/runs/hkgai_stage2_20261110_005654/`
`benchmarks/runs/hkgai_stage2_20261110_010136/`
`benchmarks/runs/hkgai_stage2_20261110_010302/`

Each contains `stage2.json` (per-call prompts/tokens/latency/status/finish
reason/HTTP status) and `STAGE2_REPORT.md`.

## 7. Questions for the HKGAI provider (updated)

1. Is there a per-key concurrency limit or session/connection reuse that makes a
   second request from the same client wait >55s?
2. Is there a gateway pre-response timeout of ~60s for non-streaming requests,
   and does it include queueing behind a previous request?
3. What is the supported way to run a multi-turn agent loop (plan → tool action
   → synthesis) against this endpoint: SSE streaming, one connection per
   request, a larger timeout, or a dedicated route?
4. Is `t2_hkgai-v3_fp8_1m_e7` intended for interactive multi-turn structured
   output at all, or should the course alias be used?

## 8. Next step once the gateway supports follow-up requests

Continue the bounded runner with fresh authorization. The workflow is already
wired and needs at most 5 requests (plan, tool_call action, completion action,
synthesis, finalize) with the remaining A–G checks automatic.

## 10. Transport follow-up (2026-11-10)

A dedicated two-request fresh-connection experiment (new `httpx.AsyncClient` and
`Connection: close` per request) failed on its **first** request with no response
headers for ~120s. HTTP connection reuse is therefore ruled out as the root
cause, and the audit found provider traffic silently routed through the Windows
registry proxy `http://127.0.0.1:7897` (controllable via
`AGENTFORGE_HTTP_TRUST_ENV`). See `docs/HKGAI_TRANSPORT_DIAGNOSIS.md`.

## 9. Stage 3–6 status after this run

Stage 2 did not pass, so live Stages 3–6 were **not** run and no additional
online requests were made. Offline integration was re-verified:

- **Stage 3 (real tools/Docker)** — `demo-tools` with Docker 29.9.2 executed
  `python_execute` (exit 0, 0.4s), recorded the audit row, and the persisted
  evidence reached the reviewer verdict and final report. Docker suite 5/5.
- **Stage 4 (multi-agent planning/delegation/review)** — the offline `demo`
  workflow completed 6 tasks / 6 executions with messages, DAG and persistence;
  delegation and review paths are covered by the Phase 1–4 tests.
- **Stage 5 (HITL and memory)** — approvals, clarifications, pause/resume,
  memory evolution and the live-memory harness pass offline; real-LLM HITL and
  memory reuse remain NOT VERIFIED.
- **Stage 6 (full live benchmark)** — NOT RUN (no Stage 2 pass, no remaining
  online budget).
- **Live gating** — `benchmark live --provider hkgai --stage all` without
  `--allow-live-api` made 0 requests and marked every stage NOT_VERIFIED.

Final offline regression for this change set: **314 passed, 2 skipped**,
Docker **5/5**, Ruff F/E9 clean, coverage **74%**.
