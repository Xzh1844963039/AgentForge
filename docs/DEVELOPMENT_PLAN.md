# AgentForge Development Plan

**Project:** AgentForge — A Memory-Evolving Multi-Agent Collaborative Platform with
Autonomous Task Delegation and Sandboxed Execution

**Updated:** 2026-11-07

The project is delivered in six incremental phases. Each phase ends with a runnable,
tested artifact and explicit acceptance criteria. Later phases must not invalidate
earlier contracts; extension points are defined in `docs/ARCHITECTURE.md`.

---

## Phase 1 — Runtime foundation and multi-agent orchestration ✅ Complete

**Status:** Implemented, tested offline, documented. Real-API acceptance pending.

### Objectives

- Establish the Python 3.11+ project, dependency management, logging, and Git.
- Define Pydantic models for agents, messages, conversations, tasks, plans, actions,
  executions, and workflows.
- Implement a unified LLM provider interface with DeepSeek, HKGAI, and deterministic
  Mock providers; support key/base URL/model/temperature/max tokens/timeout/retry.
- Implement four agents (Coordinator, Research, Coding, Reviewer) with independent
  inboxes, memory interfaces, and tool interfaces.
- Implement direct, group, and agent-to-agent messaging with durable replay.
- Implement LLM-generated task decomposition, plan validation, capability-based
  assignment, dependency-ordered execution, bounded delegation, failure propagation,
  synthesis, independent review, and finalisation.
- Persist conversations, messages, tasks, agent executions, workflows, memory, and
  JSONL lifecycle events.
- Provide a CLI, a deterministic offline demo, and a full pytest suite.

### Delivered

- `src/agentforge/` runtime with the modules listed in the README.
- SQLite schema covering seven tables; JSONL event stream.
- 81 automated tests: unit, integration, protocol, persistence, CLI subprocess.
- Mock demo: 5 substantive tasks, 100% completion, 6 LLM calls, 16 messages,
  5 memory entries, full audit trail.
- Documentation: README, architecture, development plan, Phase 1 report.

### Acceptance criteria — met offline

| Criterion | Result |
|---|---|
| Required demo flow runs without API keys | ✅ |
| Messages and task transitions are real (not simulated text) | ✅ |
| Group goal decomposes into capability-assigned subtasks | ✅ |
| Reviewer independently audits before the final report | ✅ |
| Delegation depth/steps/task limits prevent loops | ✅ |
| Persistence survives runtime restart | ✅ |
| Tests actually executed | ✅ 81 passed |

### Open item

- Real DeepSeek/HKGAI calls were not executed because no API key was available. The
  DeepSeek HTTP contract is covered with `httpx.MockTransport`; HKGAI base URL/model
  verification is pending.

---

## Phase 2 — Memory evolution and experience reuse (next)

**Goal:** turn per-agent episodic storage into an evolving, useful memory system.

### Deliverables

1. **Memory taxonomy:** episodic (task outcomes), semantic (facts), procedural
   (successful strategies), and working memory.
2. **Reflection:** after tasks/workflows, summarise what worked, what failed, and
   which strategy should be reused.
3. **Importance and decay:** salience scoring, time-based decay, consolidation of
   redundant memories, forgetting policy.
4. **Retrieval:** embedding-based semantic search with namespace isolation; fallback
   keyword/recency ranking; token-budgeted prompt injection.
5. **Experience reuse:** agents retrieve relevant procedures before planning and
   record whether reuse helped.
6. **Evaluation:** repeated-task benchmark showing measurable improvement; memory
   ablation (with/without) and contamination checks.
7. **Admin CLI:** `memory list/search/stats/clear` per agent and namespace.

### Acceptance criteria

- Each agent only ever retrieves its own memories.
- Memory writes and reads survive restart.
- A repeated similar task shows a documented improvement or faster path.
- Reflection output is stored with source task/workflow provenance.
- Tests cover decay, ranking, isolation, consolidation, and failure recovery.
- Docs explicitly distinguish implemented evolution from aspirational claims.

### Risks

- Memory pollution and prompt bloat; mitigate with importance thresholds, budgets,
  and provenance.
- Evaluation noise in mock mode; real-API runs must be labelled and separated.
- Embedding cost; make the embedding provider optional and cache results.

---

## Phase 3 — Tools, Docker sandboxes, and private filesystems

**Goal:** let agents act safely on files and code without compromising isolation.

### Deliverables

1. **Tool-calling loop:** provider function/tool schema, registry execution, result
   injection, max tool calls per task, structured tool errors.
2. **Docker sandbox per agent:** one container/workspace, start/stop lifecycle,
   CPU/memory/wall-clock quotas, optional network allow-list.
3. **Private filesystem:** only Markdown (`.md`) and plain text (`.txt`) read/write;
   path traversal prevention; per-agent roots; artifact listing.
4. **Python execution:** sandboxed `python -c`/script execution with stdout/stderr,
   exit code, timeouts, no host credential exposure.
5. **Built-in tools:** text file read/write/list, Python runner, workspace search.
6. **Permission policy:** read-only vs write tools; approval hook interface for
   write-capable actions (full HITL arrives in Phase 4).
7. **Observability:** tool invocations, sandbox lifecycle, resource usage, failures.

### Acceptance criteria

- Agent A cannot read or write Agent B's workspace.
- Path traversal and non-`.md`/`.txt` writes are rejected.
- Python execution is confined to the container and quota-limited.
- Tool failures return structured errors and never crash the workflow.
- Tests run with Docker available; a deterministic non-Docker unit path remains.
- No claim of sandbox security until integration tests demonstrate isolation.

### Risks

- Docker availability on Windows/WSL; provide a clear setup check and skip markers.
- Container startup overhead; reuse containers per workflow with cleanup.
- Prompt injection via file content; treat tool output as untrusted data.

---

## Phase 4 — Human-in-the-loop, permissions, pause/resume

**Goal:** put the human in control of risky, ambiguous, or long-running work.

### Deliverables

1. **Clarification:** agents can request missing information; task becomes BLOCKED,
   workflow pauses, CLI prompts the human, answer is injected, work resumes.
2. **Permission confirmation:** before write/git/network actions, a structured
   approval request is shown with scope and justification.
3. **Pause/resume/cancel:** persist a resumable workflow checkpoint; CLI commands to
   inspect and continue or cancel.
4. **Permission scopes:** per-agent allow-lists (read, write, execute, network,
   github) with default-deny for destructive operations.
5. **Audit:** every approval/denial and human answer is a durable message tied to the
   task/workflow.
6. **CLI UX:** pending approvals and clarifications are visible in `repl`, `tasks`,
   and `status`.

### Acceptance criteria

- A workflow pauses at a human gate and resumes with the supplied answer.
- Denied actions fail safely and are audited.
- Restarting the process does not lose a paused workflow.
- No write-capable tool can bypass the approval path.
- Tests cover approval, denial, cancellation, timeout, and resume.

### Risks

- State-machine complexity; introduce explicit workflow states and migration tests.
- Prompt fatigue; batch approvals and provide sensible grouping.
- Security bypasses; enforce permissions in the tool executor, not only prompts.

---

## Phase 5 — Search, GitHub automation, real-provider hardening

**Goal:** connect the platform to external knowledge and code workflows.

### Deliverables

1. **Internet search tool:** configurable search API, result snippets, source URLs,
   rate limiting, caching, and citation requirements.
2. **GitHub integration:** repository inspection, branch creation, commit, and push.
   Credentials from environment/secret store; least-privilege tokens; confirmation
   before push; no force-push support.
3. **Markdown/TXT deliverables:** write reports and plans safely via the sandbox.
4. **DeepSeek and HKGAI acceptance:** real-API run suites, recorded configuration,
   token/cost/latency reports, retry and timeout validation.
5. **Provider robustness:** streamed or chunked responses if required, schema-repair
   strategies, rate-limit backoff, context-window budgeting.
6. **Evaluation harness:** scripted scenarios, completion rate, quality rubric,
   failure taxonomy, cost per workflow.

### Acceptance criteria

- Search results include retrievable sources; no fabricated citations.
- GitHub actions require approval, operate on a dedicated branch, and are audited.
- Real-API runs complete the required demo scenario and record actual token usage.
- Mock and real runs are visibly separated in reports.
- Secrets never appear in logs, events, prompts, or Git history.

### Risks

- API quota/cost; enforce per-workflow budgets.
- GitHub irreversibility; branch protection, dry-run mode, and human confirmation.
- External content injection; sanitize and treat as untrusted.

---

## Phase 6 — Evaluation, scalability, and final acceptance

**Goal:** demonstrate correctness, usefulness, and production readiness.

### Deliverables

1. **Benchmark suite:** multi-agent vs single-agent baselines; completion rate,
   quality rubric, latency, token cost, delegation overhead.
2. **Concurrency:** parallel execution of independent ready tasks; race-free
   persistence and inbox handling.
3. **Scalability:** more agents/capabilities, provider abstraction stress tests,
   database growth and retention policy.
4. **Reliability:** crash recovery, idempotent task execution, retry policies,
   dead-letter handling, schema migrations.
5. **Security review:** sandbox escape tests, path traversal, secret leakage,
   prompt-injection scenarios, dependency audit.
6. **Documentation and demo:** final report, reproducible setup, architecture diagrams,
   limitations, and a polished CLI experience.
7. **Final project presentation:** live demo and evidence table.

### Acceptance criteria

- End-to-end scenarios run reliably with both mock and real providers.
- Evaluation reports are reproducible and honestly label mock data.
- Concurrent execution produces the same task outcomes and audit history.
- Known limitations are documented; no unimplemented capability is claimed.
- Final demo runs from a clean machine following the README.

### Risks

- Evaluation subjectivity; combine automated checks with a human rubric.
- Scope creep; freeze feature scope before hardening.
- Time; prioritize correctness, auditability, and reproducibility over UI polish.

---

## Cross-phase engineering rules

1. **No fabricated results.** Mock/provider failures/task outcomes must never be
   presented as real model or tool success.
2. **Contracts first.** New capabilities extend `BaseAgent`, `BaseLLMProvider`,
   `MemoryStore`, `BaseTool`, or `BaseSandbox`; they do not fork the runtime.
3. **Budgets everywhere.** Every new loop (tools, retries, delegation, search) gets a
   hard limit and an audit record.
4. **Durable by default.** State transitions, messages, approvals, and tool calls are
   persisted before side effects are trusted.
5. **Tests before claims.** Every phase ships pytest coverage and a runnable demo.
6. **Secrets stay out.** Environment variables only; redact logs and event payloads.
7. **Documentation stays current.** README, architecture, plan, and phase reports are
   updated with each merge.

---

## Recommended next step

Start **Phase 2 (Memory Evolution)** now that Phase 1 contracts are stable:

1. extend `MemoryStore` with reflection/salience/decay without changing agents,
2. add embedding-based retrieval behind a configurable interface,
3. build a repeated-task experiment to measure reuse,
4. keep the deterministic mock path for fast tests,
5. defer Docker/tools until memory behaviour is measurable.
