# AgentForge Development Plan

**Project:** AgentForge — A Memory-Evolving Multi-Agent Collaborative Platform with
Autonomous Task Delegation, Tool Execution, and Sandboxed Execution

**Updated:** 2026-11-07

The project is delivered in six phases. Each phase ends with a runnable, tested
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

## Phase 2 — Tool execution and Docker sandbox isolation ✅ Implemented (Docker integration pending)

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
175 passed, 5 skipped in ~34s

pytest --cov=agentforge --cov-report=term -q
TOTAL: 4637 statements, 1100 missed, 76% coverage
```

Skips:

- 4 Docker integration tests — Docker CLI/daemon unavailable on this machine.
- 1 Windows symlink-escape test — the OS denied symlink creation to the test user;
  the implementation checks every path component and rejects symlinks.

Docker end-to-end demo `agentforge demo-tools` exits with code 2 and prints the
prerequisite; it does **not** run Python on the host. The non-Docker file-tool demo
(`examples/tool_file_demo.py`) passes with real file writes/reads and two audited tool
calls.

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
| Docker isolation | ⏳ implementation complete, integration SKIPPED |
| Search SSRF defenses | ✅ unit tests |
| Local Git + push approval | ✅ local bare remote tests |
| Regression | ✅ all Phase 1 tests pass |

### Open items

- Run Docker integration tests and `demo-tools` on a Docker-enabled machine.
- Live search endpoint validation (HTTP provider only tested with MockTransport).
- Live real-LLM tool-calling acceptance (no DeepSeek/HKGAI key).
- Full interactive approval/pause/resume is Phase 4.

---

## Phase 3 — Memory evolution and experience reuse (next)

**Goal:** turn per-agent episodic storage into an evolving, useful memory system.

### Deliverables

1. **Memory taxonomy:** episodic, semantic, procedural, working memory.
2. **Reflection:** summarise what worked/failed and which strategy should be reused.
3. **Importance and decay:** salience scoring, decay, consolidation, forgetting.
4. **Retrieval:** embedding-based semantic search with per-agent namespace isolation;
   keyword/recency fallback; token-budgeted prompt injection.
5. **Experience reuse:** retrieve relevant procedures before planning and record
   whether reuse helped.
6. **Evaluation:** repeated-task benchmark, memory ablation, contamination checks.
7. **CLI:** memory list/search/stats/clear per agent and namespace.

### Acceptance criteria

- Each agent retrieves only its own memories.
- Memory writes/reads survive restart.
- Repeated similar tasks show documented improvement or a faster path.
- Reflection stores provenance (task/workflow).
- Tests cover decay, ranking, isolation, consolidation, and failure recovery.
- Docs clearly separate implemented evolution from aspirational claims.

### Risks

- Memory pollution and prompt bloat; thresholds, budgets, provenance.
- Evaluation noise in mock mode; real-API runs must be labelled separately.
- Embedding cost; optional provider and caching.

---

## Phase 4 — Full human-in-the-loop, permissions, pause/resume

**Goal:** put the human in control of risky, ambiguous, or long-running work.

### Deliverables

1. **Clarification:** agents request missing information; task pauses; human answer is
   injected; work resumes.
2. **Interactive approval:** connect `ApprovalGate` to the CLI for file deletion,
   git push, network enablement and future write actions; show scope/justification.
3. **Pause/resume/cancel:** persist resumable workflow checkpoints and CLI commands.
4. **Permission scopes:** per-agent allow-lists with default-deny for destructive
   operations; approval decisions persisted.
5. **Audit:** every approval/denial/human answer tied to task/workflow.
6. **CLI UX:** pending approvals and clarifications visible in `repl`, `tasks` and
   `status`.

### Acceptance criteria

- Workflow pauses at a human gate and resumes with the answer.
- Denied actions fail safely and are audited.
- Restarting the process preserves a paused workflow.
- No write-capable tool bypasses approval.
- Tests cover approval, denial, cancellation, timeout, resume.

### Risks

- State-machine complexity; explicit workflow states + migrations.
- Prompt fatigue; batch/group approvals.
- Enforce permissions in the executor, not only prompts.

---

## Phase 5 — External integrations and real-provider hardening

**Goal:** validate real search/GitHub/LLM behavior and harden external I/O.

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

## Phase 6 — Evaluation, scalability, final acceptance

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

Proceed to **Phase 3 — Memory Evolution**, then Phase 4 HITL:

1. extend `MemoryStore` with reflection/salience/decay without changing agents,
2. add embedding-based retrieval behind a configurable interface,
3. build a repeated-task experiment to measure experience reuse,
4. keep deterministic mock tests,
5. before production-like use, run the Docker verification suite on a Docker-enabled
   Windows/WSL2 machine.
