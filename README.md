# AgentForge

**A memory-evolving multi-agent collaborative platform with autonomous task delegation and sandboxed execution.**

AgentForge is a Python multi-agent runtime built for the HKUST NLP/Agent course final project. Phase 1 delivers a runnable, fully testable foundation:

- four specialised agents (Coordinator, Research, Coding, Reviewer),
- LLM-driven dynamic task decomposition and capability-based assignment,
- direct, group, and agent-to-agent messaging with durable replay,
- a validated task DAG with bounded agent-initiated delegation,
- independent review and final synthesis before the result reaches the human,
- per-agent memory namespaces, SQLite persistence, JSONL event logs, and metrics,
- a deterministic offline demo plus an interactive CLI.

> **Phase 1 status (2026-11-07): complete and verified offline.**
> 82 automated tests pass. Real DeepSeek/HKGAI API calls are **Pending** because no
> API key was available during development. Mock output is deterministic test data
> and is never presented as real LLM evaluation.

---

## Quick start (Windows)

### 1. Prerequisites

- Python **3.11+** (verified with Python 3.12.10)
- Git (optional for running, required for version control)
- PowerShell or Git Bash

No API key is required for the offline demo and tests.

### 2. Install

PowerShell:

```powershell
cd D:\5202nlp课程project
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

If PowerShell blocks activation, allow it for the current shell only:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Git Bash:

```bash
cd /d/5202nlp课程project
python -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

### 3. Run the offline demo

```powershell
python -m agentforge demo
```

The demo runs the required Phase 1 scenario end to end:

> "Research the benefits of multi-agent systems, propose a Python implementation
> plan, and review the plan for potential issues."

It prints the execution plan, per-task assignments/status/durations, the independent
review, the final report, metrics, and persistence locations. All content comes from
deterministic mock fixtures; message routing, task transitions, delegation, storage,
and metrics are real.

### 4. Run the tests

```powershell
pytest -q
```

Expected: `82 passed`. Optional coverage report:

```powershell
pytest --cov=agentforge --cov-report=term -q
```

---

## Configuration

Copy `.env.example` to `.env` and edit it. Never commit `.env`.

```powershell
Copy-Item .env.example .env
```

### Provider selection

| Provider | Value | Network | Purpose |
|---|---|---|---|
| Mock | `mock` | No | Deterministic tests and offline demo |
| DeepSeek | `deepseek` | Yes | Real LLM planning/execution |
| HKGAI | `hkgai` | Yes | Real LLM via the course HKGAI endpoint |

Only DeepSeek and HKGAI are supported as real providers. OpenAI/Anthropic APIs are
not used anywhere in the runtime.

### DeepSeek

```dotenv
AGENTFORGE_LLM_PROVIDER=deepseek
AGENTFORGE_DEEPSEEK_API_KEY=sk-...
AGENTFORGE_DEEPSEEK_BASE_URL=https://api.deepseek.com
AGENTFORGE_DEEPSEEK_MODEL=deepseek-chat
```

`DEEPSEEK_API_KEY` is also accepted as a conventional alias. Then run:

```powershell
python -m agentforge run "Research multi-agent systems and propose a Python implementation plan."
```

There is **no silent fallback**: selecting `deepseek` without a key fails with a
clear configuration error rather than quietly using mock data.

### HKGAI

```dotenv
AGENTFORGE_LLM_PROVIDER=hkgai
AGENTFORGE_HKGAI_API_KEY=...
AGENTFORGE_HKGAI_BASE_URL=https://<course-endpoint>
AGENTFORGE_HKGAI_MODEL=<course-model>
```

The base URL and model must be confirmed with the course provider; no endpoint is
invented in the code. Real HKGAI verification is **Pending**.

### Other useful settings

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_TEMPERATURE` | `0.2` | Sampling temperature |
| `AGENTFORGE_MAX_TOKENS` | `2048` | Max completion tokens |
| `AGENTFORGE_REQUEST_TIMEOUT` | `60` | HTTP timeout (seconds) |
| `AGENTFORGE_MAX_RETRIES` | `3` | Retries for retryable HTTP failures |
| `AGENTFORGE_MAX_PLAN_SUBTASKS` | `8` | Maximum planned subtasks |
| `AGENTFORGE_MAX_DELEGATION_DEPTH` | `3` | Maximum task-tree depth |
| `AGENTFORGE_MAX_DELEGATION_ROUNDS` | `2` | Delegation rounds per task |
| `AGENTFORGE_MAX_EXECUTION_STEPS` | `30` | LLM task executions per workflow |
| `AGENTFORGE_MAX_TASKS_PER_WORKFLOW` | `60` | Hard task-count cap |
| `AGENTFORGE_ENABLE_FINAL_REVIEW` | `true` | Add the review governance task |
| `AGENTFORGE_DATA_DIR` | `data` | SQLite/JSONL output directory |
| `AGENTFORGE_LOG_LEVEL` | `INFO` | Console/file log level |

Global CLI flags `--data-dir`, `--provider`, and `--log-level` override configuration
for one command.

---

## CLI reference

```text
agentforge demo                        Deterministic offline end-to-end demo
agentforge run "<goal>"                Run a workflow with the configured provider
agentforge chat <agent_id>             Interactive private chat with one agent
agentforge chat <agent_id> -m "..."    One-shot private message
agentforge repl                        Interactive shell (/goal, /dm, /tasks, ...)
agentforge agents                      List agents and capabilities
agentforge tasks [--workflow ID]       Task status and durations
agentforge conversations               List direct/group conversations
agentforge messages                    List conversations
agentforge messages --all              Latest messages from all conversations
agentforge messages --conversation ID  Replay one transcript
agentforge history                     Workflow history
agentforge result <workflow_id>        Print the final report
agentforge metrics [workflow_id]       Completion rate, timing, tokens, depth
```

Agent IDs: `coordinator`, `research`, `coding`, `reviewer`.

REPL commands: `/agents`, `/dm <agent_id> <message>`, `/goal <text>`,
`/tasks [workflow_id]`, `/messages [conversation_id]`, `/conversations`,
`/history`, `/result <workflow_id>`, `/metrics [workflow_id]`, `/help`, `/quit`.

Use `python -m agentforge ...` if the `agentforge` console script is not on `PATH`.

### Isolated runs

Every command accepts a global data directory:

```powershell
python -m agentforge --data-dir data/run1 demo
python -m agentforge --data-dir data/run1 history
```

This is useful for keeping demos, experiments, and tests separate.

---

## What Phase 1 actually implements

### Agent runtime

- `BaseAgent` with unique ID, name, role, capabilities, provider, inbox, task state,
  memory interface, and tool interface.
- Four roles: Coordinator, Research, Coding, Reviewer.
- Agents return a structured JSON action: `complete`, `delegate`, or `fail`.
  Delegation is executed and bounded by the orchestrator, never trusted blindly.
- Private human→agent chat with persisted replies.

### Dynamic orchestration

1. The Coordinator asks the configured LLM for a structured plan using the **live
   agent/capability catalogue**.
2. The plan is validated: unique keys, known dependencies, acyclic graph, size limit.
3. A capability matcher requires **full coverage** of each task's capabilities.
4. Ready tasks execute in dependency order; requests/results travel through the
   message bus.
5. Agents may delegate subtasks; the parent task is `BLOCKED` and resumes after its
   children finish. Depth, rounds, total tasks, and execution steps are enforced.
6. The platform adds governance tasks: synthesis → independent review → finalisation.
7. The human receives the final report; every step is persisted.

The plan is **not** a hard-coded three-step script. In real-API mode, the structure
and content come from the LLM. The synthesis/review/finalisation phases are explicit
platform policy and are configurable.

### Messaging

- Human → Agent direct chat
- Human → Group goal publication
- Agent → Agent task request/result
- Agent → Group delegation and final report
- Every message has ID, sender, receiver/conversation, timestamp, type, content,
  correlation ID, and optional task ID; transcripts replay in order.

### Persistence and observability

SQLite tables: `conversations`, `messages`, `tasks`, `agent_executions`, `workflows`,
`memory_entries`. Append-only JSONL events capture lifecycle changes for live
debugging. Metrics: task completion rate, completed/failed/blocked counts, summed
task time, workflow duration, LLM calls, prompt/completion tokens, delegation count,
and maximum depth.

Memory is namespaced per agent (`agent_id`) and by kind (`experience`,
`conversation`). Phase 1 records episodic outcomes and retrieves by token overlap +
recency. Reflection, importance decay, forgetting, and semantic retrieval are Phase 2.

### Explicitly not implemented in Phase 1

| Capability | Status |
|---|---|
| Memory evolution / reflection / semantic retrieval | TODO — Phase 2 |
| Docker sandbox / private per-agent filesystem | TODO — Phase 3 |
| Web search / Python execution / file tools | TODO — Phase 3 |
| GitHub commit/push automation | TODO — Phase 5 |
| Human-in-the-loop approval, pause/resume, permissions | TODO — Phase 4 |
| Web frontend | Not planned; CLI only |
| Real DeepSeek/HKGAI acceptance run | Pending: no API key available |

Interfaces for tools, sandboxes, memory, and human interaction are present so later
phases extend rather than rewrite the runtime.

---

## Project layout

```text
.
├── src/agentforge/
│   ├── agents/          BaseAgent, four roles, registry, profiles
│   ├── orchestration/   planner validation, assignment, state machine, engine
│   ├── messaging/       conversation manager and message bus
│   ├── llm/             provider interface, DeepSeek, HKGAI, mock, factory
│   ├── memory/          memory interface, in-memory and SQLite stores
│   ├── tools/           tool contract and registry (Phase 3 tools pending)
│   ├── sandbox/         sandbox contract and explicit Docker TODO
│   ├── evaluation/      workflow metrics
│   ├── models/          Pydantic domain models
│   ├── config/          centralized environment settings
│   ├── persistence/     SQLite repository and JSONL event logger
│   ├── runtime.py       dependency wiring and public runtime API
│   └── cli.py           CLI and interactive REPL
├── tests/               82 pytest tests (unit + integration + CLI subprocess)
├── examples/            programmatic mock and real-provider examples
├── docs/                architecture, plan, Phase 1 report
├── data/                runtime SQLite/JSONL output (ignored by Git)
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Verification

Actual results from this workspace (2026-11-07, Windows, Python 3.12.10):

```text
$ pytest -q
82 passed in 7.91s

$ pytest --cov=agentforge --cov-report=term -q
82 passed in 9.41s
TOTAL 2579 statements, 747 missed, 71% coverage
```

The CLI is tested through real subprocesses (`test_cli.py`), so its lines are not
included in the in-process coverage number. Core modules such as the state machine,
planner, assignment, mock provider, metrics, and event logger are at or near 100%.

Provider protocol tests use `httpx.MockTransport`; no test makes a network call.

---

## Design notes

- **No OpenAI/Anthropic dependency.** The provider layer targets DeepSeek and HKGAI
  only; the OpenAI-compatible wire protocol is just a protocol.
- **Mock is clearly labelled.** `demo` and mock runs print a notice; mock fixtures are
  excluded from formal model quality claims.
- **Capability matching is strict.** A task must be fully covered by one agent.
  Otherwise the workflow fails with a precise error; the planner is instructed to
  split work instead of mis-assigning it.
- **Failure is honest.** Provider errors, malformed actions, dependency failures, and
  budget exhaustion produce failed tasks with reasons and audit records. Nothing is
  silently marked complete.
- **Custom orchestration engine.** Phase 1 uses an explicit executor instead of a
  fixed LangGraph diagram, because the task graph must be LLM-generated and validated
  at runtime. A LangGraph adapter can be added later without changing agent or
  messaging contracts.
- **Secrets.** API keys are read from environment variables / `.env` and are never
  logged or persisted.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for component details and
[docs/PHASE1_REPORT.md](docs/PHASE1_REPORT.md) for the verification report.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `No database found ...` | Run `python -m agentforge demo` first, or pass the same `--data-dir`. |
| `no API key is configured` | Set the key in `.env`, or use `AGENTFORGE_LLM_PROVIDER=mock`. |
| PowerShell blocks venv activation | Run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. |
| Console garbles box characters | Set `$env:PYTHONIOENCODING="utf-8"` or use Windows Terminal. |
| Want a clean run | Delete `data/agentforge.db*` and `data/events.jsonl`, or use a new `--data-dir`. |
| DeepSeek HTTP 401/429 | Check key/quota; retries apply to 408/429/5xx, not to invalid credentials. |

---

## License

MIT (course project).
