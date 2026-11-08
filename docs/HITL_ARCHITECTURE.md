# Human-in-the-Loop Architecture (Phase 4)

Phase 4 adds durable human interaction and workflow lifecycle control on top of the
Phase 1–3 runtime. The design goal is not cosmetic status changes: approvals,
clarifications, pauses and cancellations must stop real execution, survive process
restart, and resume without repeating successful side effects.

## 1. Components

```text
Agent LLM action
   │
   ├─ tool_call ──► ToolExecutor ──► ToolPermissionPolicy (role/operation)
   │                   │
   │                   ├─ requires approval ──► ApprovalService (SQLite)
   │                   │                         │
   │                   │                         └─ PENDING + ApprovalRequired
   │                   │
   │                   ├─ already approved token ──► consume once (tool+digest+scope)
   │                   │
   │                   └─ execute / audit / replay cache
   │
   ├─ clarify ─────► AgentSuspension("clarification")
   │                   └─ ClarificationService (SQLite)
   │
   └─ human pause/cancel ──► WorkflowControlService (SQLite + workflow_events)
                              │
                              └─ cooperative control checks before each LLM turn
                                 and each tool dispatch
```

| Component | File | Responsibility |
|---|---|---|
| `ApprovalService` | `interaction/approval_service.py` | Persisted approval requests, expiry, decision API, one-time token consumption |
| `ClarificationService` | `interaction/clarification_service.py` | Open/answered/expired/cancelled question lifecycle |
| `WorkflowControlService` | `orchestration/control.py` | Pause/resume/cancel status and lifecycle events |
| `InteractionRepository` | `interaction/repository.py` | SQLite persistence for approvals, clarifications and workflow events |
| `AgentSuspension` | `agents/base.py` | Signals that a running task must stop and yield to a human |
| `ToolExecutor` | `tools/executor.py` | Final permission/approval enforcement, argument binding, idempotent replay |
| `TaskOrchestrator` | `orchestration/orchestrator.py` | Suspension handling, pause/resume/cancel, reassignment, metrics |

## 2. Storage

SQLite schema version 3 adds three tables:

- `approvals` — full request, arguments JSON, argument digest, risk, status,
  decision, expiry, consumption timestamp and metadata.
- `clarifications` — question, context, interpretations, status, answer and
  timestamps.
- `workflow_events` — append-only lifecycle stream used by metrics and CLI.

The separate memory database also stores `workflow_id`, `task_id` and
`execution_id` on retrieval events and feedback rows.

## 3. Execution interruption

A task is not simply marked `PAUSED` and left running in memory. The following
control path is used:

1. A tool needs approval → `ToolExecutor` persists the request and raises
   `ApprovalRequired` (runtime default `raise_on_pending=True`).
2. `BaseAgent` converts this into `AgentSuspension(kind="approval")`.
3. `TaskOrchestrator` sets the task back to `PENDING`, clears the agent's active
   task, pauses the workflow and persists both records.
4. A clarification action or human pause uses the same suspension path.
5. Cancellation raises `WorkflowCancelledRequested`; remaining tasks are marked
   `CANCELLED` and Docker sandboxes for the workflow's agents are stopped by the
   runtime.

Before every LLM turn and every tool dispatch, `BaseAgent` calls a control check
that reads the workflow status from SQLite. A pause/cancel from another process is
therefore observed at the next control point. Interruption is cooperative at these
points; an already-running tool is allowed to finish and is never claimed to have
been killed mid-operation.

## 4. Resume without duplicate effects

Resuming does not replay an LLM conversation transcript. It deterministically
replays the task from persisted task metadata and tool audit:

- The orchestrator resets any task left `RUNNING` by a process crash to `PENDING`
  and records `recovered_from_running`.
- Each task attempt receives a new `execution_id`.
- The agent runs again with the persisted approval token or clarification answer.
- `ToolExecutor` checks the persisted `tool_calls` audit first. If the same
  workflow/task/tool/arguments already succeeded, it returns the recorded output
  marked `replayed=True` instead of executing the side effect again.
- A fresh side effect executes only after a valid approval token is consumed.

The replay cache is intentionally scoped to `(workflow_id, task_id, tool_name,
argument_digest, status=SUCCESS)`. It never reuses effects from another task or
workflow.

## 5. Resume safety rules

- Approval tokens are bound to tool name, canonical argument SHA-256 digest,
  workflow, task and agent.
- Changing arguments invalidates the old token; the dispatcher requests a new
  decision instead of widening the approved scope.
- An approval can be consumed exactly once (atomic SQLite update).
- Expired or rejected approvals execute nothing.
- Permissions are evaluated before approval handling. An approved action can
  never grant an agent a permission it did not already hold.
- A task with a pending approval or open clarification cannot be resumed; a
  cancelled/expired question cannot be resumed.
- A task left `RUNNING` after a crash is marked for replay instead of being
  silently treated as completed.

## 6. Delegation and reassignment

Agent-initiated delegation remains bounded by depth, rounds, task and step
budgets. Phase 4 adds an explicit agent action:

```json
{"action": "reject_assignment", "reason": "outside my verified scope"}
```

For a delegated task, the orchestrator selects another fully capable agent,
creates a replacement task, keeps the original as `CANCELLED` with
`reassigned_to`, rewires the parent's `blocked_by` dependency and emits a
`task_reassigned` event. An agent cannot reject a plan-assigned task to avoid
work; that path fails the task. Reassignment count is bounded by
`max_reassignments`.

## 7. Task-scoped memory feedback

Phase 3 attributed feedback using task start/finish timestamps. Phase 4 records
exact workflow, task and execution IDs at retrieval time and filters feedback by
that scope. This prevents a concurrent workflow from receiving another
workflow's retrieval feedback. Cancelled workflows produce no successful
feedback. The legacy time-window fallback is used only for old, unscoped records.

## 8. Observability

Every approval decision, clarification transition, pause, resume, cancellation and
reassignment is written to both SQLite events and the JSONL event log. Workflow
metrics are recomputed from durable rows at pause, cancellation, completion and
resume boundaries.

## 9. Known limitations

- LLM/tool interruption happens at control points, not in the middle of a running
  process or an in-flight tool call.
- Conversation tokens are not checkpointed; the task is replayed with deterministic
  fixtures or the same model, relying on argument-bound approvals and the replay
  cache for safety.
- A resumed task may produce a different LLM tool call. In that case the old
  approval is not reused and a new approval is required.
- Real-LLM HITL behavior is **NOT VERIFIED** in this environment because no
  DeepSeek/HKGAI API key is configured. Runtime, persistence, state transitions,
  tools and demos are exercised with deterministic Mock decisions.
