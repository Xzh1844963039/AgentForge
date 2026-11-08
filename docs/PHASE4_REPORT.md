# Phase 4 Report — Human-in-the-Loop, Permissions and Workflow Lifecycle

**Date:** 2026-11-08
**Phase:** 4 — Full human-in-the-loop, permissions, pause/resume/cancel
**Environment:** Windows 10.0.26200 (x64), Python 3.12.10, pytest 8.4.2,
Docker Desktop 29.9.2
**Status:** Complete for runtime, persistence, tools, CLI, demos and tests.
Real-LLM HITL behavior is **NOT VERIFIED** because no DeepSeek/HKGAI API key was
available.

---

## 1. Executive summary

Phase 4 turns the Phase 2 placeholder `ApprovalGate` into a durable human-interaction
protocol and adds genuine workflow lifecycle control:

- approvals are persisted with exact arguments, canonical digest, scope, risk and TTL;
- a pending approval stops the task and pauses the workflow;
- approval tokens are bound to tool + arguments + workflow + task + agent and consumed
  exactly once;
- clarifications pause execution and resume with the persisted human answer;
- workflows can be paused, resumed after process restart, or cancelled with cleanup;
- successful tool effects are replayed from SQLite after resume, avoiding duplicate
  side effects;
- delegated tasks can be rejected and reassigned to another fully capable agent;
- memory feedback is now scoped by workflow/task/execution instead of a time window;
- metrics and CLI/REPL expose the new state.

The central invariant is: **the final authorization check is inside `ToolExecutor` /
`ApprovalGate`, not in a prompt or an LLM claim.** An approval cannot grant a missing
permission or authorize different arguments.

**Current baseline:** `225 passed, 2 skipped`, `76%` overall coverage. Docker
integration: **4/4 passed**. Ruff F/E9 clean. 20 new Phase 4 tests.

The two skips are pre-existing: Windows denied symlink creation to the test user, and
the Docker-missing failure-path test was skipped because Docker was available.

---

## 2. Requirements → implementation

| Requirement | Result |
|---|---|
| ApprovalRequest full fields and statuses | ✅ workflow/task/agent/tool/arguments/digest/risk/reason/expiry/decision/consumption |
| Pause real execution | ✅ `ApprovalRequired` → `AgentSuspension` → task `PENDING`, workflow `PAUSED` |
| Persist and survive restart | ✅ SQLite approvals/clarifications/workflow events; restart test |
| Human sees exact action | ✅ CLI `approvals show` prints arguments, digest, risk, expiry |
| Approve/reject | ✅ persistent decision APIs, CLI and REPL |
| Execute only after approval | ✅ approved token required; pending/rejected/expired execute nothing |
| Bind tool/args/digest | ✅ canonical SHA-256 + exact tool/task/agent/workflow scope |
| Changed arguments invalidate approval | ✅ digest mismatch rejected; new request required |
| Duplicate requests do not duplicate effects | ✅ replay cache + one-time token consumption |
| Expiry prevents execution | ✅ service expiry + executor denial; test |
| Audit | ✅ approvals table, tool_calls and JSONL events |
| Priority delete/overwrite/push | ✅ `delete_file`, `update_file`, `write_file overwrite=true` and `git_push` are approval-gated |
| Final check in ToolExecutor / ApprovalGate | ✅ no prompt-only authorization path |
| Clarification OPEN/ANSWERED/EXPIRED/CANCELLED | ✅ model, service, persistence, CLI |
| Agent asks and workflow pauses | ✅ `clarify` action → suspension → pause |
| Human answer resumes | ✅ `[CLARIFICATION_ANSWER]` injected into the task prompt |
| Persistence/restart for clarification | ✅ second-runtime test |
| Workflow pause/resume/cancel/status | ✅ CLI, REPL and runtime APIs |
| Save unfinished tasks/agent state/pending tools | ✅ tasks, metadata, approval/clarification records |
| Genuine interruption and recovery | ✅ control checks before turns/tool calls; restart recovery |
| No duplicate side effects | ✅ successful-effect replay; test with observable effect counter |
| Cancel stops new subtasks | ✅ remaining tasks become `CANCELLED`; resume returns immediately |
| Docker cleanup on cancel | ✅ runtime stops sandbox per involved agent |
| Idempotent resume / unsafe resume marking | ✅ terminal no-op; `recovered_from_running` + replay mode |
| Stronger permission enforcement | ✅ role + operation + workflow/task + workspace + persisted decisions |
| Approval cannot expand permission | ✅ permission checked before approval; cross-agent token rejected |
| Delegation accept/reject/reassignment | ✅ `reject_assignment`, candidate replacement, bounded count |
| Multi-level A2A E2E | ✅ two delegated tasks at depths 2 and 3 |
| Task-scoped memory feedback | ✅ workflow/task/execution IDs in retrieval and feedback |
| Concurrent feedback isolation | ✅ concurrent workflow test |
| Metrics from real events | ✅ approvals, clarifications, pauses, resumes, cancellations, delegation/reassignment |
| Three E2E demos | ✅ `examples/phase4_demos.py` A/B/C |
| CLI/REPL | ✅ approvals, clarification, workflows, pause/resume/cancel |
| Docs and version | ✅ HITL docs, Phase 4 report, version 0.4.0 |

---

## 3. Architecture

```text
ApprovalService (SQLite approvals)
  request → PENDING + TTL
  approve/reject/cancel/expire
  consume(tool + digest + workflow + task + agent) exactly once

ClarificationService (SQLite clarifications)
  OPEN → ANSWERED / EXPIRED / CANCELLED

WorkflowControlService (SQLite workflow_events)
  pause / resume / cancel + status

ToolExecutor
  permissions → schema → replay cache → approval consume/request → execute → audit

TaskOrchestrator
  suspension handling → task PENDING / workflow PAUSED
  resume validation → running-task recovery → deterministic replay
  cancel cleanup → tasks cancelled, HITL closed, sandbox stop via runtime
  reject_assignment → replacement task → parent dependency rewiring
```

Detailed design: `docs/HITL_ARCHITECTURE.md`, `docs/APPROVAL_SYSTEM.md`,
`docs/WORKFLOW_LIFECYCLE.md`.

---

## 4. Persistence

SQLite schema version 3 adds:

- `approvals`
- `clarifications`
- `workflow_events`

The separate memory database is migrated to schema version 2 with
`workflow_id`, `task_id` and `execution_id` on `retrieval_events` and
`memory_feedback`; migration is additive (`ALTER TABLE`) for existing databases.

Approval rows store the exact reviewed arguments. A tool call may execute only when
name, digest, workflow, task and agent all match. Consumption uses a conditional
update so concurrent consumers cannot both execute.

---

## 5. Verification

### 5.1 Full regression

```text
pytest -q
225 passed, 2 skipped in 93.10s
```

### 5.2 Docker

```text
pytest -m docker -q
4 passed, 223 deselected in 16.29s
```

No leftover AgentForge containers were present.

### 5.3 Coverage

```text
coverage: 76% (7263 statements, 1772 missed)
approval_service.py: 78%
clarification_service.py: 70%
interaction/repository.py: 81%
orchestration/control.py: 75%
orchestrator.py: 85%
memory/engine.py: 95%
memory/evolution_store.py: 90%
```

### 5.4 Ruff

```text
ruff check src tests --select F,E9
All checks passed!
```

### 5.5 Phase 4 tests

20 new tests:

- approval persistence, binding, one-time consumption, expiry, rejection;
- delete, in-place update and explicit `write_file` overwrite approval, including
  rejection preserving original content;
- permissions before approval and cross-agent token rejection;
- clarification pause/answer and restart persistence;
- human pause interruption and resume;
- cancellation and idempotent resumed status;
- crash-left `RUNNING` task recovery;
- duplicate side-effect prevention with observable effect tools;
- delegation rejection/reassignment and two-level A2A delegation;
- retrieval/feedback scope filters, concurrency isolation, cancelled-workflow exclusion;
- CLI approval/clarification/workflow commands.

### 5.6 E2E demos

`python examples/phase4_demos.py` (real runtime, state, bus, SQLite, tools):

```text
=== Demo A: human rejects a destructive approval ===
workflow after request : paused
approval               : delete_file {'path': 'stale.md'}
argument digest        : cba086ec...
workflow after resume  : completed
approval status        : rejected
file preserved         : True
tool audit             : pending_approval -> denied
approval metrics       : requests=1 rejected=1

=== Demo B: workflow pauses for a human clarification ===
workflow after request : paused
question               : Should data retention be 30 or 90 days?
interpretations        : ['30 days', '90 days']
workflow after answer  : completed
clarification metrics  : requests=1 answered=1
lifecycle events       : clarification_requested, workflow_paused,
                         clarification_pending, clarification_answered,
                         workflow_resumed

=== Demo C: two-level agent-to-agent delegation ===
workflow               : completed
delegated task         : depth=2 agent=research status=completed delegated_by=coding
delegated task         : depth=3 agent=reviewer status=completed delegated_by=research
delegation metrics     : count=2 max_depth=3 success=100.0%
final report           : True
```

The demo LLM decisions are deterministic fixtures. Approvals, clarifications, file
effects, replay, messages, state transitions and persistence are real.

---

## 6. Security invariants

1. `ToolPermissionPolicy` runs before approval handling; an approval is never a
   capability grant.
2. Approval tokens bind exact tool, canonical arguments, workflow, task and agent.
3. Tokens are consumed once with an atomic conditional update.
4. Expired/rejected/cancelled approvals execute nothing.
5. A replay cache hit is allowed only for a prior SUCCESS in the same
   workflow/task with identical arguments.
6. A resumed task cannot bypass a pending approval or open clarification.
7. Cancellation is terminal and closes HITL state.
8. Permission/approval decisions are persisted in `tool_calls` for audit.

---

## 7. Known limitations

1. **Real-LLM HITL NOT VERIFIED** — no API key. Deterministic Mock decisions exercise
   the runtime, but a real model's tool-call stability on resume is unmeasured.
2. **Cooperative interruption** — pause/cancel is observed at control points; an
   in-flight tool call is allowed to finish rather than being preemptively killed.
3. **No raw conversation checkpoint** — tasks replay from persisted context plus the
   replay cache. This is deterministic for fixtures and safe for side effects, but it
   is not token-level continuation.
4. **LLM may change its tool call on resume** — this correctly triggers a new approval
   rather than reusing the old decision; it can increase human interaction.
5. **SQLite event log is local** — no multi-host distributed coordination.
6. **Metrics are workflow-local** — no organization-wide dashboard.
7. **Web UI absent** — CLI/REPL only.

---

## 8. Git

All Phase 4 work is committed locally; no remote push is performed. Phase 1–3 history
is preserved.

---

## 9. Recommended next phase

Phase 5 subsequently delivered the benchmark/ablation/reliability harness; see
`docs/PHASE5_REPORT.md`. The next step is **Phase 6 — External integrations and
real-provider hardening**:

1. authorized DeepSeek/HKGAI acceptance runs for planning, tools, approvals,
   clarification and resume stability;
2. live benchmark execution with a cost cap;
3. live search/citation validation;
4. controlled GitHub operations with least-privilege credentials;
5. keep Phase 4 invariants unchanged: final enforcement in `ToolExecutor`, exact
   argument binding, durable lifecycle transitions and task-scoped memory feedback.
