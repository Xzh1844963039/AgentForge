# AgentForge Development Plan

**Project:** AgentForge — A Memory-Evolving Multi-Agent Collaborative Platform with
Autonomous Task Delegation, Tool Execution, and Sandboxed Execution

**Updated:** 2026-11-09

The project is delivered in phases. Each phase ends with a runnable, tested
artifact and explicit acceptance criteria. Later phases extend earlier contracts
rather than replacing them.

---

## Phase 1 — Runtime foundation and multi-agent orchestration ✅ Complete

### Objectives

- Python 3.11+ project, dependencies, logging, Git.
- Pydantic contracts for agents, messages, conversations, tasks, plans, actions,
  executions, workflows.
- Unified LLM provider interface: DeepSeek, HKGAI, deterministic Mock.
- Four agents: Coordinator, Research, Coding, Reviewer, with inboxes, per-agent task
  state, memory and tool interfaces.
- Direct, group and agent-to-agent messaging with durable replay.
- LLM-generated task DAG, capability assignment, bounded delegation, failure
  propagation, synthesis, independent review, finalisation.
- SQLite + JSONL observability; CLI; deterministic demo; pytest suite.

### Result

- Phase 1 completed and reported in `docs/PHASE1_REPORT.md`.
- All Phase 1 tests continue to pass after Phase 2.

---

## Phase 2 — Tool execution and Docker sandbox isolation ✅ Complete (Docker verified)

### Objectives

- Unified tool execution framework: BaseTool, ToolRegistry, ToolExecutor, ToolResult,
  ToolExecutionContext, ToolPermissionPolicy.
- Structured tool calls from agents, real dispatcher results, iterative reasoning.
- File tools with private per-agent workspaces and traversal/symlink protections.
- Docker sandbox per agent with resource, privilege and network isolation.
- Python execute/test/syntax tools; no host execution fallback.
- Search providers with SSRF-safe fetching; local Git tools with push approval.
- Tool audit, metrics, CLI inspection, end-to-end integration.

### Delivered

- `ToolSpec` JSON input/output schemas, timeout, permissions, approval flag.
- Dispatcher pipeline: lookup → permissions → schema → approval → timeout → execute →
  output validation → output cap → SQLite/JSONL audit.
- File tools: list/read/write/update/delete/search; `.md`/`.txt`; private workspace;
  path traversal, absolute path, symlink component, Windows reserved-name and size
  protections; atomic writes; delete approval.
- Artifact tools for controlled cross-agent transfer.
- Python tools: `python_execute`, `python_test`, `python_syntax_check`; Docker-only
  execution; stdout/stderr/exit code/duration/timeout returned to the agent.
- DockerSandbox: non-root, network-none default, CPU/memory/PID/time limits,
  read-only root, tmpfs, cap-drop ALL, no-new-privileges, no Docker socket, per-agent
  bind mount, lifecycle cleanup, timeout container removal.
- Search: MockSearchProvider (offline and labelled), configurable HttpSearchProvider,
  URLGuard SSRF protection, redirect/size limits, untrusted-content marking.
- Git: status/diff/log/add/commit/push with argument lists, repository scoping,
  explicit commit paths, credential scrubbing, mandatory push approval.
- Agent tool loop and runtime/orchestrator integration; tool calls and LLM turns are
  persisted; metrics include tool calls/failures/time.
- CLI: `demo-tools`, `tools`, `tool-calls`; tool tables in workflow output.
- New tests: tool framework, file/artifact isolation, Docker command/lifecycle,
  Python tools, search/SSRF, Git/approval, agent-tool integration, delegation with
  tools, CLI.
- Documentation: `TOOL_SYSTEM.md`, `SANDBOX_SECURITY.md`, `PHASE2_REPORT.md`.

### Verification

```text
pytest -q
186 passed, 2 skipped in ~56s

pytest --cov=agentforge --cov-report=term -q
TOTAL: 4835 statements, 1062 missed, 78% coverage
```

Skips:

- 1 Windows symlink-escape test — the OS denied symlink creation to the test user;
  the implementation checks every path component and rejects symlinks.
- 1 Docker-missing failure-path test — skipped because Docker was available for the
  verification run.

Docker end-to-end demo `agentforge demo-tools` passes (exit 0) and returns the real
stdout `Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]`, exit code 0 and sandbox
container metadata. The non-Docker file-tool demo (`examples/tool_file_demo.py`) also
passes with real file writes/reads and two audited tool calls.

### Acceptance criteria

| Criterion | Result |
|---|---|
| Tool calls are structured and dispatched centrally | ✅ |
| Schema/permission/timeout/audit enforced | ✅ |
| Private workspaces and path-escape defenses | ✅ (symlink runtime test SKIPPED) |
| No direct cross-agent reads | ✅ |
| Tool results feed back to agents | ✅ |
| Delegation with tools, count > 0 | ✅ |
| Python never executes on host | ✅ failure-path test |
| Docker isolation | ✅ 4/4 Docker integration tests PASS; no leftover containers |
| Search SSRF defenses | ✅ unit tests |
| Local Git + push approval | ✅ local bare remote tests |
| Regression | ✅ all Phase 1 tests pass |

### Open items

- Tool evidence propagation is implemented and regression-tested; keep the
  `[TOOL_EVIDENCE]` contract stable in later phases.
- Live search endpoint validation (HTTP provider only tested with MockTransport).
- Live real-LLM tool-calling acceptance (no DeepSeek/HKGAI key).
- Real remote Git push is intentionally not performed; local bare remote tested.
- Full interactive approval/pause/resume was delivered in Phase 4.

---

## Phase 3 — Memory evolution and experience reuse ✅ Complete (pipeline verified)

**Goal:** turn per-agent episodic storage into an evolving, evidence-based memory
system, without modifying the Phase 2 tool/sandbox/orchestrator core.

### Delivered

- Memory taxonomy: `EPISODIC`, `SEMANTIC`, `PROCEDURAL`, `WORKING`.
- Evidence-first reflection from SQLite `tool_calls` and `ToolEvidence`, never chat
  text.
- `MemorySource` provenance: workflow/task/tool-call IDs, evidence digest, LLM
  provider/mock flag, real vs mock tool execution.
- Verification levels: `REAL_EXECUTION`, `MOCK_SIMULATION`, `UNVERIFIED`.
- Procedural candidates keyed by agent/capabilities/kind/tool sequence/arguments;
  `support_count` counts distinct workflows.
- Promotion only for REAL_EXECUTION with ≥2 distinct workflows and confidence ≥0.8.
- Mock procedures stored only as provisional/low-confidence and excluded from default
  retrieval.
- Local hashing embedding retrieval + keyword/importance fallback, verification
  filtering, provenance prefixes and prompt budget.
- Per-agent + namespace isolation; no cross-agent recall.
- Importance decay, dedupe/consolidation, working-memory TTL, forgetting.
- Retrieval feedback matched to real tool actions; confidence increases/decreases.
- Controlled with/without-memory experiment; same task/model/tools/budget, only
  recall differs.
- CLI: `memory list/search/stats/candidates/feedback/clear/experiment`.
- Separate `memory_evolution.db`; Phase 2 audit tables untouched.
- Docs: `MEMORY_SYSTEM.md`, `PHASE3_REPORT.md`.

### Verification

```text
pytest -q
205 passed, 2 skipped in 66.26s

pytest --cov=agentforge --cov-report=term -q
TOTAL: 5977 statements, 1326 missed, 78% coverage
agentforge.memory: 93% coverage

experiment:
  candidate support = 2
  promoted verification = REAL_EXECUTION, confidence 0.85
  with memory = notes/verified.md; without memory = notes/default.md
  procedural retrieved with=True without=False
  action_changed = True
  feedback used/success = True/+0.03
  pipeline_verified = True
  real LLM learning = NOT VERIFIED
```

### Acceptance criteria

| Criterion | Result |
|---|---|
| Memory built on Tool Audit | ✅ |
| Source/verification recorded | ✅ |
| Only real evidence becomes high-confidence procedural | ✅ |
| Mock pipeline validation without real-learning claim | ✅ |
| Retrieval changes subsequent action | ✅ real file-tool path change |
| Controlled ablation | ✅ same model/task/tools/budget |
| Agent/namespace isolation | ✅ |
| Decay/consolidation/forgetting | ✅ |
| Real-API performance | ⏳ NOT VERIFIED |

### Open items / limitations

- Real-LLM memory learning performance NOT VERIFIED (no API key).
- Local hashing embeddings, not a learned semantic model; quality not benchmarked.
- Procedural matching is exact on sequence/argument hints; fuzzy transfer future.
- Feedback uses task time windows; concurrent tasks need task-scoped retrieval.
- No cross-agent memory sharing by design.
- Optional embedding provider/API is future work.

### Risks

- Memory pollution and prompt bloat; mitigated by verification gates, support
  thresholds, prompt budget and forgetting.
- Mock evidence contamination; mitigated by provisional low-confidence state and
  default exclusion.
- Exact-match rigidity; evaluated only on repeated tasks in this phase.

---

## Phase 4 — Full human-in-the-loop, permissions, pause/resume ✅ Complete (runtime verified)

**Goal:** put the human in control of risky, ambiguous, or long-running work.

### Delivered

1. **Persistent approvals:** `ApprovalRequest` with workflow/task/agent scope, exact
   arguments, canonical SHA-256 digest, risk, reason, expiry, decision and consumption;
   approve/reject/cancel/expire APIs; one-time atomic consumption.
2. **Final permission enforcement:** `ToolExecutor` checks role permissions before
   approval handling; an approval cannot grant a missing permission or different
   arguments. All decisions are persisted in `tool_calls`.
3. **Interactive approval CLI/REPL:** `approvals list/show/approve/reject`; `show`
   prints arguments, digest, risk, expiry and decision history.
4. **Clarification:** `ClarificationRequest` lifecycle `OPEN/ANSWERED/EXPIRED/CANCELLED`;
   pauses the workflow; answer persisted and injected as `[CLARIFICATION_ANSWER]`;
   `clarification list/show/answer` and REPL commands.
5. **Pause/resume/cancel:** control checks before LLM turns and tool dispatches; tasks
   suspended to `PENDING`; workflow persisted as `PAUSED`; resume validates decisions,
   recovers crash-left `RUNNING` tasks and replays deterministically; cancellation
   marks tasks, closes approvals/clarifications and stops Docker sandboxes.
6. **Idempotent replay:** successful tool effects are returned from SQLite for matching
   workflow/task/tool/argument digest after resume, preventing duplicate side effects.
7. **Delegation:** `reject_assignment` action, bounded automatic reassignment to
   another fully capable agent, replacement task rewiring, `task_reassigned` events,
   and two-level A2A delegation E2E.
8. **Task-scoped memory feedback:** retrieval events and feedback carry exact
   workflow/task/execution IDs; concurrent and cancelled workflows cannot
   cross-contaminate confidence.
9. **Metrics:** approval requests/outcomes/rate/wait, clarifications/answered/wait,
   pause/resume/cancel counts, delegated completion/success and reassignments.
10. **Docs/demos:** `HITL_ARCHITECTURE.md`, `APPROVAL_SYSTEM.md`,
    `WORKFLOW_LIFECYCLE.md`, `PHASE4_REPORT.md`, `examples/phase4_demos.py`.

### Verification

```text
pytest -q
223 passed, 2 skipped

pytest -m docker -q
4 passed, 221 deselected

python examples/phase4_demos.py
A: paused → rejected → completed; file preserved
B: paused → answered → completed
C: two-level delegation completed (depths 2 and 3)
```

Docker sandbox, persistence and replay are real; LLM decisions in the demos are
deterministic Mock fixtures. Real-LLM HITL behavior is NOT VERIFIED.

### Acceptance criteria

| Criterion | Result |
|---|---|
| Approval pauses real execution and persists | ✅ |
| Human sees exact action/arguments/digest/risk | ✅ CLI + SQLite |
| Approval bound to tool/args/workflow/task/agent | ✅ |
| Changed arguments invalidate the approval | ✅ |
| Rejection/expiry execute nothing | ✅ |
| Duplicate request/resume does not repeat successful side effects | ✅ replay cache test |
| Final permission check in ToolExecutor/ApprovalGate | ✅ |
| Approval cannot expand permission scope | ✅ |
| Clarification pauses and answer resumes after restart | ✅ |
| Pause/resume handles restart and crash-left tasks | ✅ |
| Cancel stops new work, closes HITL state, cleans Docker | ✅ |
| Delegation accept/reject/reassign/multi-level E2E | ✅ |
| Task-scoped memory feedback isolation | ✅ concurrency test |
| CLI/REPL approval, clarification and workflow controls | ✅ |
| Regression suite and Docker tests | ✅ |

### Open items / risks

- Real-LLM HITL and resume behavior require a DeepSeek/HKGAI API key.
- Interruption is cooperative at control points; an in-flight tool is not preempted.
- Raw LLM conversation state is not checkpointed; deterministic replay plus the
  effect cache provides safety.
- Prompt fatigue could be reduced later with batch/group approvals.

---

## Phase 5 — Benchmarking, real-LLM validation, reliability ✅ Complete

**Goal:** measure unknown-task completion, reliability and cost honestly rather
than adding features.

### Delivered

- `agentforge.benchmark` package: typed schema, dataset loader, isolated runtime
  factory, verifiers, metrics, failure analyzer, runner, report/export, ablation
  and live acceptance.
- 34 internal dev tasks across 10 categories, each with objective checks or an
  explicit manual-review requirement.
- 8 live tasks with fixture actions removed; live calls require a key and explicit
  `--allow-live-api`.
- Resumable batches with JSON/JSONL/CSV/Markdown exports and Git commit capture.
- A/B/C ablation with separate learning/evaluation tasks.
- 11 reliability/fault-injection tests and 6 benchmark tests.
- High-risk intent/audit reconciliation (`NEEDS_RECONCILIATION`), blocking replay
  when the side-effect outcome is unknown.
- SQLite indexes and tool-catalog cache with before/after measurements.
- `agentforge benchmark list|run|compare|report|failures|live`.

### Verification

```text
Full suite: 242 passed, 2 skipped
Fixture benchmark: 34 runs, 33 passed, 1 needs review, 0 failed
A/B/C: 6/6 passes each; multi-agent ~20% more tokens; memory retrieval 5,
       memory used 0 (fixture does not consume retrieved content)
Ruff F/E9: clean
Docker: 5/5
Live LLM: NOT VERIFIED (no key; no paid call)
```

### Acceptance criteria

| Criterion | Result |
|---|---|
| Objective, non-self-reported success | ✅ verifier-based |
| 30+ tasks across all categories | ✅ 34 tasks |
| Single/multi/memory ablation | ✅ A/B/C framework + run |
| Live acceptance framework, no silent spend | ✅ explicit gate; NOT VERIFIED |
| Fault injection including audit crash window | ✅ 11 tests + Phase 4 |
| Measured optimization, no correctness loss | ✅ indexes/cache measured |
| Honest Mock/real labeling | ✅ reports and docs |
| Regression | ✅ 242 passed, 2 skipped |

**Real-LLM capability remains NOT VERIFIED.** Phase 6 must not treat fixture
scores as model performance.

---

## Phase 6 — External integrations and real-provider hardening (next)

**Goal:** validate real search/GitHub/LLM behavior, run live acceptance with
explicit authorization, and harden external I/O.

### Deliverables

1. **Live search:** validate the configured HTTP provider, citations and source
   quality; add a search-provider acceptance suite.
2. **GitHub:** repository inspection, branch/commit/push with least-privilege
   credentials, dry-run and confirmation; never force-push.
3. **Real LLM tool-calling:** DeepSeek/HKGAI acceptance runs, schema-repair strategies,
   rate-limit handling, context budgeting.
4. **Markdown/TXT deliverables:** write reports through file/artifact tools.
5. **Evaluation harness:** completion rate, quality rubric, latency, token/cost,
   failure taxonomy, tool-call statistics.
6. **Security:** prompt-injection and tool-output adversarial tests; secret scanning.

### Acceptance criteria

- Search results have retrievable sources; no fabricated citations.
- GitHub actions require approval, use a dedicated branch, and are audited.
- Real API runs complete the target scenario and record actual tokens.
- Mock and real runs are visibly separated.
- Secrets never appear in logs, events, prompts, or Git history.

### Risks

- API quotas/cost; per-workflow budgets.
- Irreversible GitHub actions; dry-run, branch protection, confirmation.
- External content injection; treat all tool output as untrusted.

---

## Phase 7 — Final acceptance and scalability

**Goal:** demonstrate correctness, usefulness, and readiness.

### Deliverables

1. **Benchmark suite:** multi-agent vs single-agent baselines; completion, quality
   rubric, latency, tokens, delegation/tool overhead.
2. **Concurrency:** independent ready tasks and independent tool calls in parallel;
   race-free persistence/inbox handling.
3. **Scalability:** more agents/capabilities, database growth/retention, provider
   stress tests.
4. **Reliability:** crash recovery, idempotent task/tool execution, retries,
   dead-letter handling, schema migrations.
5. **Security review:** sandbox escape tests, path traversal, secret leakage,
   prompt-injection scenarios, dependency audit.
6. **Documentation/demo:** final report, reproducible setup, architecture diagrams,
   limitations, polished CLI.
7. **Final presentation:** live demo and evidence table.

### Acceptance criteria

- End-to-end scenarios run with mock and real providers.
- Evaluation reports are reproducible and honestly label mock/skipped runs.
- Concurrent execution preserves task outcomes and audit history.
- Known limitations are documented; no unimplemented capability is claimed.
- Final demo runs from a clean machine following the README.

### Risks

- Evaluation subjectivity; automated checks + human rubric.
- Scope creep; freeze features before hardening.
- Time; prioritize correctness and auditability.

---

## Cross-phase engineering rules

1. **No fabricated results.** Mock/provider failures/task outcomes/tool outcomes must
   never be presented as real success.
2. **Contracts first.** Extend `BaseAgent`, `BaseLLMProvider`, `MemoryStore`,
   `BaseTool`, or `BaseSandbox`; do not fork the runtime.
3. **Budgets everywhere.** Every new loop (tools, retries, delegation, search) gets a
   hard limit and an audit record.
4. **Durable by default.** State transitions, messages, approvals, tool calls, and
   artifacts are persisted before side effects are trusted.
5. **Tests before claims.** Every phase ships pytest coverage and a runnable demo.
6. **Secrets stay out.** Environment only; redact logs/events; never inject into
   sandboxes or Git commands.
7. **Fail closed.** Missing Docker, missing credentials, unsafe URLs, or missing
   approvals produce explicit errors, not fallbacks.
8. **Docs stay current.** README, architecture, plan, and phase reports are updated.

---

## Recommended next step

Proceed to **Phase 6 — External integrations and real-provider hardening**:

1. run authorized DeepSeek/HKGAI acceptance tests for planning, tools, approvals,
   clarification and resume behavior;
2. execute the live benchmark with explicit permission and a cost cap; compare
   against the fixture baseline without conflating them;
3. validate live search and citation grounding;
4. harden GitHub/Git operations with least-privilege credentials and dry runs;
5. add prompt-injection/tool-output adversarial tests and secret scanning;
6. preserve Phase 4/5 invariants: final enforcement in `ToolExecutor`, exact argument
   binding, task-scoped memory feedback, durable lifecycle transitions, objective
   benchmark verification and reconciliation before replay.

Real-LLM HITL, memory learning, live benchmark performance and resume quality remain
explicitly NOT VERIFIED until those API acceptance runs exist. Do not auto-advance.
