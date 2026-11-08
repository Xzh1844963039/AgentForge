# Workflow Lifecycle: Pause, Resume, Cancel and Recovery

Phase 4 implements honest workflow control: state is persisted, execution stops at
real control points, resuming continues from persisted tasks, and cancellation
performs cleanup. A status field alone is never presented as an interruption.

## 1. Workflow states

```text
PENDING ──► RUNNING ──► COMPLETED
               │  │
               │  ├─► FAILED
               │  ├─► PAUSED ──resume──► RUNNING
               │  └─► CANCELLED
               └─► BLOCKED (dependency deadlock / upstream failure handling)
```

| State | Meaning |
|---|---|
| `PENDING` | Created, not started |
| `RUNNING` | Active task graph |
| `PAUSED` | Persisted and waiting for approval, clarification or a human pause |
| `BLOCKED` | Waiting on failed/dependent work |
| `COMPLETED` | Final report produced |
| `FAILED` | Terminal failure |
| `CANCELLED` | Human cancellation; no further work may run |

Task states add `CANCELLED` to the Phase 1 taxonomy. Terminal tasks are
`COMPLETED`, `FAILED` and `CANCELLED`.

## 2. Pause

Human CLI/REPL pause:

```text
agentforge workflows pause <workflow_id>
```

- Sets the workflow to `PAUSED` and writes a `workflow_paused` event.
- A live execution notices the status before its next LLM turn or tool dispatch.
- The currently running task is returned to `PENDING`; the agent's active-task
  marker is cleared.
- Completed tasks and their results remain intact.
- Calling pause on a terminal workflow raises an explicit error.

Approval and clarification pauses use the same persisted mechanism, with reasons
`approval_pending` and `clarification_pending`.

## 3. Resume

```text
agentforge approvals approve <approval_id>     # or reject
agentforge clarification answer <id> <answer>
agentforge workflows resume <workflow_id>
```

`TaskOrchestrator.resume_goal` validates persisted state before doing any work:

1. expire overdue approvals/clarifications;
2. reject resume while an approval is pending or a clarification is open;
3. recover tasks left `RUNNING` after a crash by resetting them to `PENDING` and
   recording `recovered_from_running=True`;
4. attach the approval token and/or clarification answer to the task;
5. set the workflow back to `RUNNING` and record `workflow_resumed`;
6. execute only non-terminal tasks.

Tasks that already completed are not executed again. A successfully completed
workflow is returned unchanged on a second resume request.

## 4. No duplicate side effects

Deterministic task replay plus the tool replay cache provides idempotency:

- Prior successful tool calls for the exact workflow, task, tool and argument
  digest are returned from SQLite with `replayed=True`.
- The tool body is not called again.
- An approval token that was already consumed cannot be used again.
- A side effect that executed before a pause is therefore not repeated.

A task attempt creates a new `execution_id`. Approval and clarification records
retain the exact task scope, while memory feedback uses the final attempt's
execution id.

## 5. Restart recovery

All state needed for resume lives in SQLite:

- workflow and task statuses/results,
- approval requests and consumed timestamps,
- clarification questions and answers,
- tool-call audit including successful outputs,
- workflow lifecycle events.

A new runtime process can open the same data directory, validate the approval or
clarification, and resume the workflow. The tests simulate a process death by
leaving a task `RUNNING`, closing the runtime, creating a new runtime on the same
database and resuming.

## 6. Cancel

```text
agentforge workflows cancel <workflow_id> [--reason TEXT]
```

- Pending approvals are marked `CANCELLED`; open clarifications are cancelled.
- Remaining non-terminal tasks become `CANCELLED` with the cancellation reason.
- The workflow becomes `CANCELLED` and receives no final report.
- Docker sandboxes for agents involved in the workflow are stopped by the runtime.
- A `workflow_cancelled` event is written once.
- Resume of a cancelled workflow returns immediately without executing anything.

Cancellation is cooperative for a live process: it is observed at the next control
check. An in-flight tool call is not falsely claimed to have been terminated.

## 7. Metrics

Pause, resume and cancellation counts come from persisted `workflow_events`.
Metrics also report approval and clarification wait times, approval outcomes,
delegation counts/success and reassignment counts. Duplicate lifecycle events are
avoided by treating the control service as the single writer for external
pause/cancel transitions.

## 8. Honest limitations

- No raw LLM conversation checkpoint is stored; tasks replay with persisted
  context and the replay cache.
- Interruption is checked at control points, not by preemptive thread/process
  termination.
- A model that changes its tool arguments on resume legitimately triggers a new
  approval instead of reusing the old one.
- Real-LLM resume quality is **NOT VERIFIED** in this environment; deterministic
  Mock decisions exercise the runtime, persistence, tools and lifecycle.
