> **Historical Phase 1 report.** Phase 2 subsequently implemented real tool execution and Docker sandbox isolation. See `docs/PHASE2_REPORT.md` for the current status. Docker integration remains unverified on this machine.

# Phase 1 Report — AgentForge

**Date:** 2026-11-07
**Phase:** 1 — Project Initialization and Multi-Agent Runtime
**Environment:** Windows 10.0.26200 (x64), Python 3.12.10, pytest 8.4.2,
pydantic 2.14.3, httpx 0.28.1, Rich 15.0.0, Git 2.53.0
**Status:** Complete and verified offline. Real DeepSeek/HKGAI acceptance **Pending**
(no API key available).

---

## 1. Executive summary

Phase 1 delivers a runnable multi-agent runtime rather than a mock-up. A human can
publish a complex goal into a group conversation; the Coordinator uses the configured
LLM to produce a validated task DAG; a capability matcher assigns task requests that
travel through a real message bus; agents execute with their own memory namespaces;
agents may delegate within hard limits; the Coordinator synthesises results, the
Reviewer audits the draft, and the Coordinator produces the final report. Every
message, task transition, LLM invocation, and metric is persisted in SQLite and
mirrored in a JSONL event log.

The deterministic Mock provider makes the entire flow reproducible without network
access. It is clearly labelled and its content is not a substitute for real LLM
evaluation.

---

## 2. Requirements → implementation

| Requirement | Phase 1 status | Evidence |
|---|---|---|
| Human ↔ Agent private chat | ✅ | `runtime.chat_with_agent`, `chat` CLI, tests |
| Multi-agent + human group chat | ✅ | Group conversation, HUMAN→GROUP message, participant broadcast |
| Autonomous decomposition and assignment | ✅ | LLM plan with live capability catalogue, DAG validation, full-coverage matcher |
| Agent-to-agent delegation | ✅ | Structured `delegate` action, child tasks, BLOCKED parent, resume |
| Human-in-the-loop clarify/approve/pause | ⏳ Not in Phase 1 | Task/model `BLOCKED` foundation; full gateway is Phase 4 |
| Per-agent independent memory | ✅ basic | `agent_id` + namespace isolation; episodic outcomes and chat memory |
| Memory evolution | ⏳ TODO | Interface reserved; reflection/decay/semantic retrieval are Phase 2 |
| Per-agent Docker sandbox and private FS | ⏳ TODO | `BaseSandbox` contract; `DockerSandbox` raises NotImplemented explicitly |
| Internet search, files, Python, GitHub | ⏳ TODO | `ToolRegistry`/`BaseTool` contracts only |
| Task completion rate and execution time | ✅ | `WorkflowMetrics`, CLI `metrics`, workflow history |
| Extensibility, error handling, tests | ✅ | Layered packages, typed errors, 82 passing tests |

The unimplemented capabilities are explicitly marked TODO in code and docs; no
simulated success is reported for them.

---

## 3. What was built

### 3.1 LLM providers

- Unified async `BaseLLMProvider` with normalized `LLMResponse` and token usage.
- `DeepSeekProvider` over an OpenAI-compatible `/chat/completions` endpoint.
- `HKGAIProvider` with mandatory course-supplied base URL/model.
- `MockProvider` with deterministic purpose-specific fixtures, scripted-response
  support, call recording, and optional latency.
- Configurable API key, base URL, model, temperature, max tokens, timeout, retries.
- Retries on 408/409/429/5xx and transport errors with exponential backoff; auth and
  bad-request failures are not retried.
- Configuration errors never silently fall back to mock.

### 3.2 Agents

- `BaseAgent`: unique ID, name, role, capabilities, provider, inbox, task state,
  memory interface, and tool interface.
- `CoordinatorAgent`: LLM planning with one repair retry; synthesis/finalisation
  prompts.
- `ResearchAgent`, `CodingAgent`, `ReviewerAgent`: role-specific instructions.
- Structured action protocol: `complete` / `delegate` / `fail`; plain text is accepted
  only as a completion fallback, malformed action JSON is rejected.
- Registry with role lookup and duplicate protection.

### 3.3 Messaging

- Durable `MessageBus` and `ConversationManager`.
- Direct, group, task-request, task-result, delegation, plan, system, and error
  message types.
- Human→Agent, Human→Group, Agent→Agent, Agent→Group routing.
- Messages carry ID, sender, receiver, conversation, timestamp, type, content,
  correlation ID, task ID, reply link, and metadata.
- Ordered transcript replay; group broadcast; inbox de-duplication.

### 3.4 Orchestration

- LLM-generated plan, not a hard-coded fixed flow.
- Validation: unique keys, known dependencies, self-dependency rejection, cycle
  detection, subtask limit.
- Capability matching requires full coverage by one agent; unmatched work fails with
  an actionable error and no silent misassignment.
- Dependency-ordered DAG execution through the message bus.
- Bounded delegation: max depth, max rounds per task, max tasks, max execution steps.
- Parent task `BLOCKED` until children complete, then resumed with child results.
- Governance tasks: synthesis → independent review → finalisation (review
  configurable).
- Failure propagation, dependency-failure marking, deadlock guard, and workflow
  abort with cleanup.
- Strict task state machine.

### 3.5 Persistence and evaluation

- SQLite WAL database with seven tables: conversations, messages, tasks,
  agent_executions, workflows, memory_entries, schema_meta.
- JSONL event stream for lifecycle auditing.
- Metrics: completion rate, completed/failed/blocked counts, workflow duration,
  summed task duration, LLM calls, tokens, delegation count, max depth.
- Per-agent memory namespaces (`experience`, `conversation`) surviving restart.

### 3.6 CLI and demo

- `demo`, `run`, `chat`, `repl`, `agents`, `tasks`, `conversations`, `messages`,
  `history`, `result`, `metrics`.
- Deterministic demo requires no API key; clear mock notice.
- Interactive REPL with goal submission, private messages, task/message/history
  inspection, and metrics.

---

## 4. Tests actually executed

### Commands

```text
python -m pytest -q
python -m pytest --cov=agentforge --cov-report=term -q
python -m ruff check src tests --select F,E9
```

### Results

```text
82 passed in 7.91s
82 passed in 9.41s
TOTAL: 2579 statements, 747 missed, 71% coverage
Ruff F/E9: All checks passed
```

The CLI is covered by real subprocess tests, which are not instrumented by
in-process coverage. Core modules are at or near full coverage: state machine 100%,
planner 97%, assignment 97%, mock provider 97%, metrics 100%, event logger 100%,
orchestrator 87%, persistence 93%, messaging 89%.

### Test distribution

| File | Tests | Focus |
|---|---:|---|
| `test_agents.py` | 6 | registry, execution, memory use, private chat, inbox |
| `test_assignment.py` | 10 | capability routing, strict coverage, tie-break |
| `test_cli.py` | 2 | real subprocess demo/history/chat/tasks/messages/agents |
| `test_coordinator.py` | 5 | valid plan, repair retry, failure, cycle, no agents |
| `test_deepseek_provider.py` | 5 | HTTP contract, auth, retry, 401, malformed response |
| `test_llm.py` | 12 | mock determinism, factory, env aliases, action parsing |
| `test_memory.py` | 3 | namespace isolation, ranking, restart persistence |
| `test_messaging.py` | 8 | direct, agent-to-agent, group, system, invalid routing |
| `test_models.py` | 6 | validation, IDs, action invariants, plan keys |
| `test_orchestration.py` | 10 | full demo, delegation, depth/steps, failures, restart |
| `test_persistence.py` | 4 | round-trip, upserts, event JSONL |
| `test_planner.py` | 5 | topological order, cycle, unknown/self dependencies, limit |
| `test_state_machine.py` | 6 | legal/illegal transitions, terminal states |

No test makes a network call. DeepSeek protocol tests use `httpx.MockTransport`.

---

## 5. Demo execution

Command:

```powershell
python -m agentforge demo
```

Actual outcome:

```text
Workflow status:        completed
Task completion rate:   100.0% (5/5)
LLM calls:              6  (1 planning + 5 task executions)
Tokens (mock):          10018 prompt / 4417 completion
Delegations / depth:    0 / 1
Messages persisted:     16
Tasks persisted:        6  (5 substantive + synthetic root)
Agent executions:       6
Memory entries:         5
SQLite:                 data/agentforge.db
Event log:              data/events.jsonl
```

Task flow:

| Kind | Agent | Capabilities | Dependencies |
|---|---|---|---|
| research | research | research | — |
| coding | coding | coding | research |
| synthesis | coordinator | synthesis | research, coding |
| review | reviewer | review | synthesis, research, coding |
| finalize | coordinator | synthesis | synthesis, review |

The Reviewer's persisted verdict was `APPROVED_WITH_ISSUES`; the final report
incorporates the review findings. The group transcript contains the human goal, the
Coordinator plan, task-completion notices, and the final report. Direct conversations
contain Coordinator→agent task requests and agent→Coordinator task results.

Mock timings are measured but tiny (workflow ≈ 0.016 s) and validate instrumentation,
not model latency or cost.

---

## 6. Real API status

### DeepSeek

**Not run against the live API. Pending.** No API key was available in the
environment. The HTTP path is verified with an in-process mock transport:

- correct endpoint, bearer authorization, model, temperature, and max tokens,
- successful response and usage parsing,
- retry after HTTP 500 followed by success,
- immediate failure on HTTP 401,
- malformed/unexpected response handling.

To validate for real:

```dotenv
AGENTFORGE_LLM_PROVIDER=deepseek
AGENTFORGE_DEEPSEEK_API_KEY=sk-...
```

then run:

```powershell
python -m agentforge run "Research the benefits of multi-agent systems, propose a Python implementation plan, and review the plan for potential issues."
```

### HKGAI

**Not run. Pending endpoint/model confirmation.** `HKGAIProvider` uses the same
OpenAI-compatible protocol but requires `AGENTFORGE_HKGAI_BASE_URL` and
`AGENTFORGE_HKGAI_MODEL`; no endpoint is invented.

---

## 7. Known limitations and TODOs

### Deliberately out of Phase 1 scope

1. **Memory evolution** — only episodic outcomes + keyword/recency recall. No
   reflection, salience decay, forgetting, consolidation, or embeddings (Phase 2).
2. **Docker sandbox** — interface only; `DockerSandbox` raises `NotImplementedError`.
   No isolation or private filesystem is claimed (Phase 3).
3. **Tools** — registry/contract only; no search, file I/O, Python execution, or
   GitHub (Phase 3/5).
4. **Human-in-the-loop** — no approval/clarification/pause-resume flow (Phase 4).
   `BLOCKED` exists for delegation waits only.
5. **Web frontend** — intentionally absent; Phase 1 is CLI-only.

### Engineering limitations

6. **Sequential execution** — ready tasks run one at a time; parallelism is a future
   enhancement. The DAG model already supports it.
7. **No token/wall-clock budget enforcement** — tokens are measured, but only task,
   depth, round, and step limits are enforced. Add hard cost budgets in Phase 5/6.
8. **Fail-fast task policy** — a failed task fails dependents; partial-result
   synthesis is not implemented yet.
9. **Full-coverage assignment only** — a task requiring capabilities split across
   agents is rejected; the planner must split it. No negotiation/reassignment loop.
10. **No task-level retry after malformed LLM action** — the planner has one repair
    retry; executing agents do not yet. A malformed action fails the task honestly.
11. **Inbox growth** — group and private-chat messages are delivered to agent inboxes
    and not all are consumed; long-running sessions need retention/acknowledgement.
12. **Group chat is coordinator-led** — arbitrary group chatter does not trigger
    every agent; goals are orchestrated through the Coordinator. This is Phase 1
    scope, not autonomous group conversation.
13. **SQLite migrations** — schema version marker exists, but no migration framework.
14. **Coverage gap** — CLI is exercised in subprocess tests but reports 0% under
    in-process coverage; overall measured coverage is 71%.
15. **Windows console** — rich box output may require UTF-8 console settings on
    legacy terminals.

### Real-API risks

16. Real LLMs can return invalid JSON or plan capabilities not in the catalogue;
    planning repair and strict assignment fail safely, but real-model robustness is
    unmeasured until an API run is performed.
17. HKGAI endpoint behavior is unverified.

---

## 8. How to run on Windows

```powershell
cd D:\5202nlp课程project
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"

# offline demo (no API key)
python -m agentforge demo

# tests
pytest -q

# interactive CLI
python -m agentforge repl

# private chat
python -m agentforge chat research

# real provider
Copy-Item .env.example .env
# edit .env: AGENTFORGE_LLM_PROVIDER=deepseek and AGENTFORGE_DEEPSEEK_API_KEY=...
python -m agentforge run "Your goal"
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

---

## 9. Git

- Repository initialized locally; no remote configured.
- `.env`, `.venv`, runtime databases, and JSONL logs are ignored.
- No push performed.
- Three local commits record the runtime/tests, documentation/examples, and the
  per-agent task-state addition; exact hashes are reported in the final Phase 1
  hand-off message and `git log`.

---

## 10. Recommended next phase

Proceed to **Phase 2 — Memory Evolution**:

1. extend `MemoryStore` with episodic/semantic/procedural memory and reflection,
2. add importance/decay/consolidation and embedding-based retrieval,
3. measure experience reuse on repeated tasks with mock and real providers,
4. then move to Phase 3 (tools + Docker sandbox + private `.md`/`.txt` files).

Do not begin Phase 3 tool execution or claim sandbox isolation until Phase 3 tests
demonstrate per-agent isolation and quotas.
