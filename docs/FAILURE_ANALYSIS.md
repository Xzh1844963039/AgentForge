# Failure Analysis and Reliability (Phase 5)

## 1. Failure taxonomy

`FailureAnalyzer` classifies every non-passing run using durable evidence:

| Category | Meaning |
|---|---|
| `planning_failure` | Planner output failed validation or planning raised |
| `permission_denied` | Role/tool permission boundary rejected the work |
| `delegation_limit` | Delegation depth/round limit enforced |
| `dependency_failure` | Upstream task failure propagated through dependencies |
| `tool_schema_error` | Tool arguments failed JSON Schema validation |
| `tool_timeout` | Tool/sandbox execution timed out |
| `task_failed` | Agent returned `fail` or exhausted its action budget |
| `verification_failure` | Artifact/audit/state verifier rejected the result |
| `needs_reconciliation` | High-risk side effect has no trustworthy audit outcome |
| `manual_review_required` | Objective checks passed; human quality review pending |
| `verification_skipped` | Docker/verifier prerequisite unavailable |
| `time_budget_exceeded` | Monotonic solver budget exceeded |
| `runtime_error` | Runner/runtime exception |
| `unknown_failure` | No classified cause; raw evidence retained |

The full benchmark had one `manual_review_required` (open-ended research) and no
unexpected runtime failures. Expected failure-path tasks (dependency failure,
delegation depth, cancellation, permission denial, timeout, schema rejection) are
benchmark **passes** when the verifier confirms the safe behavior.

## 2. Audit-vs-side-effect crash window

Phase 4's replay cache keyed successful effects by
`(workflow, task, tool, argument_digest)`. That is safe only if the success audit
was persisted before a crash. Phase 5 closes the remaining gap:

```text
persist high-risk intent (STARTED)
        │
        ▼
execute destructive tool
        │
        ├─ audit SUCCESS persisted ──► resolved_succeeded (safe to replay cache)
        │
        ├─ audit failure persisted ──► resolved_failed (no blind retry)
        │
        └─ no audit / process crash ─► UNRESOLVED
                                         │
                                         ▼
                               NEEDS_RECONCILIATION
                               replay is forbidden
```

Implementation:

- SQLite table `tool_reconciliations` (schema version 4) with a unique
  `(workflow_id, task_id, tool_name, argument_digest)` key;
- high-risk operations (`delete_file`, `update_file`, `write_file` overwrite,
  `git_push`) persist a `STARTED` intent **before** execution;
- failure to persist the intent refuses execution;
- failure to persist the final audit changes the result to
  `NEEDS_RECONCILIATION`, records `UNRESOLVED`, and the agent aborts the task;
- on any later attempt the executor checks unresolved intent before approval or
  execution and returns `NEEDS_RECONCILIATION` without replaying.

**No exactly-once guarantee is claimed.** The platform can prove that a side
effect was *not* safely audited and refuses to repeat it; a human must reconcile
the external state. For operations where even the intent write cannot be
persisted, high-risk execution fails closed.

Test evidence:
`tests/test_phase5_reliability.py::test_audit_write_failure_enters_reconciliation_and_blocks_replay`
injects an audit-write failure after an observable side effect. The effect runs
once, the run returns `NEEDS_RECONCILIATION`, and a simulated resume leaves the
effect count at one.
`test_high_risk_tool_is_not_executed_without_intent_audit` proves that no intent
means no execution.

## 3. Fault-injection matrix

| # | Fault | Handling | Evidence / status |
|---:|---|---|---|
| 1 | LLM returns invalid JSON/action | structured parse error; task/workflow fails honestly | `test_invalid_json_action_fails_without_fabrication` — PASSED |
| 2 | Tool schema validation failure | tool not called; audited `schema_validation` | `test_tool_schema_validation_rejects_argument_before_tool`; benchmark `execute_invalid_arguments` — PASSED |
| 3 | Agent repeats one action forever | hard tool-call budget stops the loop | `test_repeated_action_is_stopped_by_tool_budget` — PASSED |
| 4 | Tool execution timeout | `asyncio.wait_for` returns `TIMEOUT` | `test_tool_timeout_is_structured`; benchmark `execute_timeout` (sandbox timeout surfaced as error with `timed_out=true`) — PASSED |
| 5 | Docker container killed unexpectedly | sandbox health check fails and restarts container | `test_docker_container_death_is_recovered_by_restart` — PASSED (Docker 29.9.2) |
| 6 | Workflow cancelled mid-run | control check; tasks cancelled; side effect absent | benchmark `recovery_cancel` + Phase 4 cancellation tests — PASSED |
| 7 | Approval expires | executor denies; no execution | Phase 4 `test_expired_approval_cannot_execute_and_reports_expired` — PASSED |
| 8 | Clarification never answered | workflow stays PAUSED with open question | `test_unanswered_clarification_keeps_workflow_paused`; benchmark `clarification_unanswered` — PASSED |
| 9 | Delegation loop / depth | depth and round bounds fail task/workflow | benchmark `delegation_depth_limit` — PASSED |
| 10 | SQLite audit temporarily unavailable | high-risk side effect returns `NEEDS_RECONCILIATION`; replay blocked | reconciliation tests — PASSED |
| 11 | Memory retrieval fails | recall is non-fatal; task continues without memory | `test_memory_recall_failure_is_non_fatal` — PASSED |
| 12 | Process dies mid-workflow | task recovered to PENDING; workflow resumes | `test_restart_after_pause_recovers_persisted_workflow` + Phase 4 restart test — PASSED |
| 13 | Side effect succeeded before pause/crash | success audit/effect replay avoids duplicate | benchmark `recovery_pause_resume` duplicate check; Phase 4 replay test — PASSED |
| 14 | Approved arguments changed | digest scope mismatch rejects token; new decision needed | Phase 4 `test_approval_token_rejects_wrong_digest_scope_and_reuse` — PASSED |

Additional resilience:

- task-scoped memory feedback prevents concurrent/cancelled cross-contamination;
- tool allow-lists are enforced in the dispatcher, not prompts;
- budget violations mark runs failed;
- benchmark task verifiers detect duplicate successful effects.

## 4. Observed failures in the full benchmark

Fixture-provider benchmark: 34 runs, 33 passed, 1 needs review, 0 runtime
failures, 0 timeouts.

| Failure category | Count | Explanation |
|---|---:|---|
| `manual_review_required` | 1 | `research_manual_review` is intentionally open-ended; automatic structure checks pass but quality needs a human |
| runtime/verification failures | 0 | all objective checks passed under the committed fixture run |

Expected failure-path tasks are not counted as failures because their verifier
checks the required safe behavior (denial, dependency propagation, depth limit,
cancellation, honest failure).

## 5. Live-provider failures

No live run was performed. Live checks are `NOT_VERIFIED`; no claim is made about
real-model JSON reliability, tool-calling success, planning validity or reviewer
quality. The live report records all 10 checks as `NOT_VERIFIED` and includes a
three-task fixture harness validation (3/3 passed) to prove the acceptance
framework executes.

## 6. Remaining risks

1. The intent + audit writes are two SQLite operations; a crash between them is
   handled as unresolved, but reconciliation is manual and has no resolution UI.
2. Low-risk tools do not pre-create an intent; a crash after their side effect but
   before audit can still duplicate on replay. High-risk operations are protected.
3. Cooperative interruption does not preempt an in-flight tool/container.
4. Docker startup and external API latency dominate E2E variance.
5. The small dev set can expose regressions but cannot establish general model
   reliability.
