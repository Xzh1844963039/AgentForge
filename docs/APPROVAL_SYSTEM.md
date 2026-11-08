# Approval System

The approval system enforces destructive and privileged operations only after an
explicit, parameter-bound human decision. The final check is performed inside
`ToolExecutor` through the configured `ApprovalGate`; prompts and agent claims are
never treated as authorization.

## 1. When approval is required

`ToolPermissionPolicy` requires approval when either the tool spec declares
`requires_approval=True` or the tool needs one of these permissions:

- `file_delete`
- `git_push`

Built-in examples: `delete_file`, `update_file`, `write_file` with
`overwrite=true`, and `git_push`. `update_file` is an in-place content modification,
so its tool spec requires approval for both replace and append modes. Approval is
additive to role permissions: an agent that lacks the underlying permission is denied
before an approval request is created.

## 2. Approval request fields

`ApprovalRequest` persists:

| Field | Meaning |
|---|---|
| `approval_id` | Unique request id |
| `workflow_id`, `task_id`, `agent_id` | Exact execution scope |
| `tool_name`, `action` | Requested operation |
| `arguments` | Exact sanitized arguments shown to the human |
| `argument_digest` | Canonical SHA-256 of sorted JSON arguments |
| `risk_level` | `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` |
| `reason`, `summary`, `details` | Human-readable justification |
| `status` | `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `CANCELLED` |
| `decision`, `decided_by`, `comment` | Human decision audit |
| `created_at`, `expires_at`, `decided_at`, `consumed_at` | Lifecycle timestamps |

## 3. Lifecycle

```text
PENDING ──approve──► APPROVED ──consume once──► executed
   │                    │
   │                    └─ arguments changed → token invalid → new request
   ├─reject──────────► REJECTED ──► tool denied, no execution
   ├─TTL elapsed─────► EXPIRED  ──► tool denied, no execution
   └─workflow cancel─► CANCELLED
```

- Approval does not execute the tool immediately. Execution occurs only on the
  next task attempt.
- A pending approval pauses the workflow and returns the task to `PENDING`.
- The workflow cannot resume while an approval is still pending.
- Approving a request that already expired fails with an explicit state error.
- A consumed token cannot be reused, even by the same task.
- Cancelling a workflow cancels its pending approvals.

## 4. Argument binding

Arguments are canonicalized with sorted keys and compact JSON, then hashed with
SHA-256. The token is valid only when all of the following match:

1. approval id,
2. tool name,
3. argument digest,
4. workflow id,
5. task id,
6. agent id.

If the model resubmits a different path, content or command, the digest differs
and the dispatcher treats the old approval as unusable. It creates a fresh
approval request; the previous human decision is not silently expanded.

## 5. One-time consumption

`InteractionRepository.mark_approval_consumed` performs a conditional SQLite update:

```sql
UPDATE approvals
SET consumed_at = ?
WHERE approval_id = ? AND consumed_at IS NULL AND status = 'approved'
```

Only the caller whose update changes a row may execute the tool. Concurrent
consumption attempts are rejected. The tool audit records both the pending request
and the final execution/denial.

## 6. Final enforcement point

`ToolExecutor.execute` order:

1. resolve tool;
2. evaluate role permissions (deny by default);
3. validate input JSON Schema;
4. check the successful-effect replay cache;
5. determine whether this specific call needs approval (tool spec, destructive
   permission, or `write_file overwrite=true`);
6. if approval is required:
   - with an approval token: validate binding, status, expiry and consume once;
   - without a token: persist a pending request and raise `ApprovalRequired`;
7. execute the tool;
8. validate output, cap size, persist the audit record.

There is no code path where an LLM response alone bypasses step 2 or step 5.

## 7. CLI

```text
agentforge approvals list [--workflow ID] [--status STATUS]
agentforge approvals show <approval_id>
agentforge approvals approve <approval_id> [--comment TEXT]
agentforge approvals reject <approval_id> [--comment TEXT]
agentforge workflows resume <workflow_id>
```

`show` prints the exact arguments, digest, risk, expiry, decision, comment and
consumption time. REPL commands: `/approvals`, `/approve`, `/reject`, `/resume`.

## 8. Audit and metrics

Every transition writes `approval_requested`, `approval_decided`,
`approval_consumed` or `workflow_cancelled` events. Workflow metrics include
request count, approved/rejected/expired counts, approval rate, rejection rate and
average human wait time.

## 9. Verified behavior

Tests cover:

- persistence and CLI display;
- approval execution exactly once;
- rejection with no side effect;
- expiry with no execution;
- wrong digest, wrong agent/scope and token reuse rejection;
- approved action cannot bypass role permissions;
- replay cache prevents duplicate side effects after pause/resume;
- approval cancellation when a workflow is cancelled.

The deterministic demos use a Mock LLM decision and real file tools. Real-LLM
approval behavior is **NOT VERIFIED** without a provider API key.
