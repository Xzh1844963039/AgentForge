# AgentForge

**A memory-evolving multi-agent collaborative platform with autonomous task delegation, real tool execution, and Docker sandbox isolation.**

AgentForge is the final project for the HKUST NLP/Agent course. It is built incrementally; Phase 1 delivered the multi-agent runtime, and Phase 2 adds a validated tool-execution framework and a Docker-backed sandbox.

## Current status

| Phase | Status |
|---|---|
| Phase 1 — Runtime, messaging, DAG orchestration, persistence, CLI | ✅ complete |
| Phase 2 — Tool dispatch, private workspaces, Docker sandbox, Python/Search/Git tools | ✅ complete; Docker integration verified (4/4) |
| Phase 3 — Evidence-first memory evolution, procedural promotion, feedback, ablation | ✅ complete (pipeline verified; real-LLM learning NOT VERIFIED) |
| Phase 4 — Full human-in-the-loop | ⏳ next |

**Verified on 2026-11-08:** `205 passed, 2 skipped` in the full pytest suite with Docker running, `78%` statement coverage (`93%` for `agentforge.memory`), Ruff F/E9 clean. The 2 skips are one Windows symlink-privilege test and the Docker-missing failure-path test. Docker integration: **4/4 passed**. Real-LLM memory learning is **NOT VERIFIED** (no API key).

Phase 2 highlights:

- A unified `ToolExecutor` with JSON-Schema validation, per-agent permissions, timeouts, output limits, approval gates, and durable audit.
- Agents request tools through structured `tool_call` actions, receive real structured results, and continue reasoning.
- Private per-agent Markdown/TXT workspaces with path-traversal, absolute-path, symlink, Windows-device-name, size and count protections.
- `python_execute`, `python_test`, `python_syntax_check`; Python execution only inside Docker, never on the host.
- `DockerSandbox`: non-root, no network by default, CPU/memory/PID/time limits, read-only root, dropped capabilities, no-new-privileges, no Docker socket, per-agent bind mount.
- `web_search` / `fetch_webpage` with mock and configurable HTTP providers plus SSRF blocking, redirect limits, response caps, and untrusted-content marking.
- Local `git_status/diff/log/add/commit/push` with parameterized commands, repository scoping, credential scrubbing, and a mandatory approval gate for push.
- Controlled cross-agent artifact transfer; direct workspace reads are impossible.
- Tool calls are persisted in SQLite and shown by the CLI (`tools`, `tool-calls`).
- Evidence-first memory evolution with episodic/semantic/procedural/working types,
  audited provenance, verification levels, embedding retrieval, decay/forgetting,
  procedural promotion, retrieval feedback and a controlled with/without-memory
  experiment.

---

## Quick start (Windows)

### 1. Prerequisites

- Python **3.11+** (verified with Python 3.12.10)
- Git (for version control and local Git-tool tests)
- Docker Desktop + WSL2 **only** for `python_execute`/`python_test` and `agentforge demo-tools`
- PowerShell or Git Bash

No API key is required for the deterministic demo and most tests. Docker Desktop/WSL2 is required for Python execution and `demo-tools`; the verified environment used Docker 29.9.2 with the daemon running. If Docker is unavailable, the sandbox fails closed rather than executing on the host.

### 2. Install

PowerShell:

```powershell
cd D:\5202nlp课程project
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

If activation is blocked:

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

### 3. Run the Phase 1 demo (no Docker/API key)

```powershell
python -m agentforge demo
```

This runs the required multi-agent research/implementation/review scenario through the real MessageBus, task DAG, persistence, and metrics. LLM content is deterministic mock fixture data; routing, task transitions, delegation, storage and metrics are real.

### 4. Run the Phase 2 file-tool demo (no Docker)

```powershell
python examples/tool_file_demo.py
```

This invokes the real Runtime and ToolExecutor: CodingAgent writes `notes/demo.md` and reads it back. The tool calls, file contents, durations, audit rows and metrics are real; only LLM decisions are mock fixtures.

### 5. Run the Phase 2 Docker demo (requires Docker)

```powershell
docker info
python -m agentforge demo-tools
```

Goal:

> Create a Python script that calculates prime numbers, execute it, verify the output, and produce a Markdown report summarizing the implementation and test results.

The CodingAgent calls `python_syntax_check` and `python_execute`; the script runs inside the non-root, network-disabled Docker sandbox. stdout, stderr, exit code, script path and tool audit are shown. **If Docker is unavailable, `demo-tools` exits with code 2 and prints the prerequisite; there is no host fallback.**

### 6. Run the Phase 3 memory experiment (no Docker required)

```powershell
python -m agentforge memory experiment
```

This runs two learning workflows with REAL private-workspace file tools, promotes a
procedural memory after two distinct real workflows, then runs the same task with and
without memory. The verified run changes the tool argument from
`notes/default.md` to `notes/verified.md` and records `used=True success=True` feedback.
LLM decisions are a deterministic Mock policy, so the result proves the memory
pipeline, **not real-LLM learning performance**.

### 7. Run the tests

```powershell
pytest -q
pytest -m docker -q        # only with Docker running
```

Expected on the current machine (Docker running):

```text
205 passed, 2 skipped
```

Optional coverage:

```powershell
pytest --cov=agentforge --cov-report=term -q
# TOTAL: 5977 statements, 1326 missed, 78% coverage
# agentforge.memory: 93% coverage
```

---

## Configuration

Copy `.env.example` to `.env`; never commit `.env`.

```powershell
Copy-Item .env.example .env
```

### LLM providers

| Provider | Value | Network | Use |
|---|---|---|---|
| Mock | `mock` | No | Tests and deterministic demos |
| DeepSeek | `deepseek` | Yes | Real LLM planning/execution |
| HKGAI | `hkgai` | Yes | Course HKGAI endpoint |

Only DeepSeek and HKGAI are used as real providers. No OpenAI/Anthropic SDK or endpoint is used.

```dotenv
AGENTFORGE_LLM_PROVIDER=deepseek
AGENTFORGE_DEEPSEEK_API_KEY=sk-...
AGENTFORGE_DEEPSEEK_BASE_URL=https://api.deepseek.com
AGENTFORGE_DEEPSEEK_MODEL=deepseek-chat
```

`DEEPSEEK_API_KEY` is accepted as an alias. Selecting a real provider without credentials fails explicitly; it never silently falls back to mock.

HKGAI requires `AGENTFORGE_HKGAI_BASE_URL` and `AGENTFORGE_HKGAI_MODEL` from the course; live HKGAI validation is pending.

### Tools and sandbox

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_TOOLS_ENABLED` | `true` | Register tools and enable the agent tool loop |
| `AGENTFORGE_MAX_TOOL_CALLS_PER_TASK` | `6` | Hard tool-call budget |
| `AGENTFORGE_TOOL_OUTPUT_LIMIT_BYTES` | `65536` | Dispatcher output cap |
| `AGENTFORGE_MAX_FILE_BYTES` | `262144` | Per-file read/write/artifact cap |
| `AGENTFORGE_MAX_WORKSPACE_FILES` | `200` | List/search result cap |
| `AGENTFORGE_WORKSPACE_ROOT` | `<data>/workspaces` | Private workspace root |
| `AGENTFORGE_ARTIFACTS_ROOT` | `<data>/artifacts` | Controlled artifact store |
| `AGENTFORGE_SANDBOX_IMAGE` | `python:3.11-slim` | Sandbox image |
| `AGENTFORGE_SANDBOX_CPU_LIMIT` | `1.0` | CPU quota |
| `AGENTFORGE_SANDBOX_MEMORY_MB` | `256` | Memory quota |
| `AGENTFORGE_SANDBOX_PIDS_LIMIT` | `64` | PID quota |
| `AGENTFORGE_SANDBOX_TIMEOUT_SECONDS` | `30` | Per-execution timeout |
| `AGENTFORGE_SANDBOX_NETWORK_ENABLED` | `false` | Container network (default off) |
| `AGENTFORGE_SANDBOX_CONTAINER_USER` | `65534:65534` | Non-root user (root is rejected) |
| `AGENTFORGE_SANDBOX_READ_ONLY_ROOT` | `true` | Read-only container root |
| `AGENTFORGE_SANDBOX_TMPFS_SIZE_MB` | `64` | `/tmp` tmpfs size |

### Search

`AGENTFORGE_SEARCH_PROVIDER=mock` is the default and makes no network requests. `http` requires a configurable JSON endpoint:

```dotenv
AGENTFORGE_SEARCH_PROVIDER=http
AGENTFORGE_SEARCH_ENDPOINT=https://your-search-service.example/v1/search
AGENTFORGE_SEARCH_API_KEY=...
AGENTFORGE_SEARCH_TIMEOUT=15
AGENTFORGE_WEB_FETCH_MAX_BYTES=262144
AGENTFORGE_WEB_FETCH_MAX_REDIRECTS=3
```

Expected search response: `{"results": [{"title", "url", "snippet", "source"}]}`. Page fetching blocks localhost, private/special-use IPs, cloud metadata hostnames, credentials in URLs, non-80/443 ports, oversized responses and excessive redirects. Live search is **not verified** in this environment.

### Local Git tools

```dotenv
AGENTFORGE_GIT_REPO_ROOT=D:/path/to/authorized/repo
AGENTFORGE_GIT_TIMEOUT=30
```

Git tools are disabled when no authorized root is configured. Commands use argument lists (no shell), explicit file scopes for add/commit, and a credential-scrubbed environment. `git_push` always requires approval; the default gate returns `PENDING_APPROVAL` and performs no push. No real remote is pushed by tests; the push test uses a temporary local bare repository.

### Memory evolution

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_MEMORY_EVOLUTION_ENABLED` | `true` | Reflect on workflows after completion |
| `AGENTFORGE_MEMORY_RECALL_ENABLED` | `true` | Allow agents to retrieve memory |
| `AGENTFORGE_MEMORY_FILE` | `<data>/memory_evolution.db` | Separate memory database |
| `AGENTFORGE_MEMORY_RECALL_LIMIT` | `6` | Max entries per prompt |
| `AGENTFORGE_MEMORY_PROMPT_BUDGET_CHARS` | `3000` | Prompt budget |
| `AGENTFORGE_MEMORY_INCLUDE_MOCK` | `false` | Include Mock memories (default deny) |
| `AGENTFORGE_MEMORY_INCLUDE_UNVERIFIED` | `false` | Include unverified memories |
| `AGENTFORGE_MEMORY_PROMOTION_THRESHOLD` | `2` | Distinct real workflows needed |
| `AGENTFORGE_MEMORY_PROMOTION_CONFIDENCE` | `0.8` | Minimum promotion confidence |
| `AGENTFORGE_MEMORY_DECAY_HALF_LIFE_DAYS` | `30` | Importance half-life |
| `AGENTFORGE_MEMORY_FORGET_THRESHOLD` | `0.05` | Forgetting threshold |
| `AGENTFORGE_MEMORY_WORKING_TTL_SECONDS` | `3600` | Working-memory TTL |
| `AGENTFORGE_MEMORY_EMBEDDING_DIMENSIONS` | `256` | Local hashing vector size |
| `AGENTFORGE_MEMORY_STORE_MOCK_PROCEDURES` | `true` | Store provisional Mock procedures |

Memory is evidence-first: entries reference persisted `tool_calls` IDs and carry
`REAL_EXECUTION`, `MOCK_SIMULATION`, or `UNVERIFIED`. Only real evidence with repeated
support becomes high-confidence procedural memory. See
[docs/MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md).

---

## CLI reference

```text
agentforge demo                        Phase 1 deterministic demo
agentforge demo-tools                  Phase 2 Docker/Python end-to-end demo
agentforge run "<goal>"                Workflow with the configured provider
agentforge chat <agent_id>             Private chat
agentforge repl                        Interactive shell
agentforge agents                      Agent list and capabilities
agentforge tools                       Tool contracts, permissions, timeouts
agentforge tool-calls                  Tool audit records
agentforge memory list/search/stats    Phase 3 memory inspection
agentforge memory experiment           Controlled with/without-memory ablation
agentforge tasks [--workflow ID]       Task status
agentforge conversations               Conversation list
agentforge messages [--all]            Messages / transcript replay
agentforge history                     Workflow history
agentforge result <workflow_id>        Final report
agentforge metrics [workflow_id]       Completion, timing, tokens, tools
```

Agent IDs: `coordinator`, `research`, `coding`, `reviewer`.

REPL: `/agents`, `/tools`, `/tool-calls [workflow_id]`, `/dm`, `/goal`, `/tasks`,
`/messages`, `/conversations`, `/history`, `/result`, `/metrics`, `/quit`.

Global flags `--data-dir`, `--provider`, `--log-level` apply to every command.

---

## Tool system at a glance

Agents cannot execute tools directly. The LLM emits a structured action:

```json
{"action": "tool_call", "tool_calls": [
  {"name": "write_file", "arguments": {"path": "notes.md", "content": "# Notes"}}
]}
```

The `ToolExecutor` then:

1. resolves the tool and checks the per-agent permission policy (default deny),
2. validates arguments against the tool's JSON Schema,
3. requests human approval for destructive permissions such as file deletion and git push,
4. executes with a hard timeout and output cap,
5. validates structured output,
6. records the call in SQLite (`tool_calls`) and JSONL,
7. returns stdout/structured output/error to the agent, which continues reasoning.

Synthesis, review and finalization receive a structured `[TOOL_EVIDENCE]` block built
from the persisted `tool_calls` audit (stdout, stderr, exit code, success, duration,
script path and sandbox container). Reports quote those recorded facts instead of
scraping model prose; Mock content remains deterministic but is evidence-grounded.

Built-in tools:

| Tool | Purpose |
|---|---|
| `list_files`, `read_file`, `write_file`, `update_file`, `delete_file`, `search_files` | Private Markdown/TXT workspace; delete requires approval |
| `share_artifact`, `fetch_artifact` | Controlled cross-agent file transfer |
| `python_syntax_check` | Static parse only (no execution) |
| `python_execute`, `python_test` | Python inside the Docker sandbox only |
| `web_search`, `fetch_webpage` | Search provider + SSRF-guarded fetch |
| `git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_push` | Authorized local repository; push requires approval |

See [docs/TOOL_SYSTEM.md](docs/TOOL_SYSTEM.md) for contracts, the permission matrix and the agent loop.

---

## Sandbox security at a glance

Each agent has its own workspace and Docker container:

- container user `65534:65534`; root is rejected by configuration;
- `--network none` by default;
- `--cpus`, `--memory`/`--memory-swap`, `--pids-limit`, wall-clock timeout;
- `--read-only` root with a noexec `/tmp` tmpfs;
- `--cap-drop ALL` and `no-new-privileges`;
- only the agent's private workspace is bind-mounted at `/workspace`;
- no Docker socket, no privileged mode, no host environment/API keys;
- stale container cleanup and container kill on timeout;
- Python files are staged into the private workspace; no host execution fallback.

See [docs/SANDBOX_SECURITY.md](docs/SANDBOX_SECURITY.md) for the threat model, flags, verification commands and residual risks.

---

## Memory evolution at a glance

Phase 3 memory is derived from the SQLite `tool_calls` audit, not chat text. Each
entry records workflow/task/tool-call provenance and a verification level. Only
`REAL_EXECUTION` procedures supported by at least two distinct workflows are promoted
to high confidence; Mock evidence remains provisional and is excluded from default
retrieval.

The controlled experiment runs two learning workflows with real file tools, promotes a
procedure, then runs the same task with and without memory. In the verified run:

```text
with memory:    write_file(path="notes/verified.md")
without memory: write_file(path="notes/default.md")
action changed: YES
feedback:       used=True success=True delta=+0.03
real LLM learning effect: NOT VERIFIED (Mock decision policy)
```

See [docs/MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md) and
[docs/PHASE3_REPORT.md](docs/PHASE3_REPORT.md).

---

## Project layout

```text
.
├── src/agentforge/
│   ├── agents/          BaseAgent tool loop, four roles, registry
│   ├── orchestration/   planning, DAG execution, delegation, metrics
│   ├── messaging/       conversations and message bus
│   ├── llm/             provider interface, DeepSeek, HKGAI, mock
│   ├── tools/           dispatcher, policy, approvals, file/artifact/Python/search/Git tools
│   ├── sandbox/         Docker sandbox, command runner, manager
│   ├── memory/          evidence-first evolution, embeddings, retrieval, feedback, experiment
│   ├── models/          Pydantic contracts (messages, tasks, tools, workflows)
│   ├── persistence/     SQLite repository (tasks, tool_calls, artifacts, ...)
│   ├── evaluation/      workflow metrics including tool usage
│   ├── config/          centralized environment settings
│   ├── runtime.py       dependency wiring and public API
│   ├── cli.py           CLI and REPL
│   └── cli_memory.py    memory list/search/stats/clear/experiment
├── tests/               207 collected: unit, integration, protocol, CLI, Docker-gated
├── examples/            mock/file-tool/memory demos, real-provider example
├── docs/                architecture, plan, Phase 1/2/3 reports, tool/sandbox/memory docs
├── data/                runtime data (ignored): DB, JSONL, workspaces, artifacts
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Persistence and observability

SQLite tables: `conversations`, `messages`, `tasks`, `agent_executions`, `workflows`, `memory_entries`, `tool_calls`, `artifacts`.

Each tool call records: agent, workflow, task, tool name, arguments, status, output, error, duration, permission decision, approval status/id, permissions, timestamps and metadata. JSONL events mirror lifecycle and tool activity.

Metrics now include tool calls, tool failures and summed tool time alongside completion rate, workflow/task duration, LLM calls, tokens, delegation count and depth.

---

## What is implemented vs. not

Implemented:

- Human↔Agent private chat, Human→Group goals, Agent→Agent, Agent→Group.
- LLM-generated task DAG, capability assignment, bounded delegation.
- Tool calls through a unified dispatcher with real results and iterative reasoning.
- Private file tools, artifacts, Docker sandbox, Python execution (Docker-gated), search providers, local Git tools.
- Approval gates for file deletion and git push (default: pending).
- Structured tool evidence from the SQLite audit is propagated into synthesis, review and final report prompts.
- SQLite + JSONL observability, CLI, deterministic demos, 205 passing tests.

Not implemented / pending:

- **Live search** — HTTP provider tested with `httpx.MockTransport`, no live endpoint configured.
- **Real remote Git push** — only a temporary local bare remote was tested; default approval gate prevents pushes.
- **Real LLM tool-calling** — no DeepSeek/HKGAI key; mock decisions are deterministic fixtures.
- **Real-LLM memory learning performance** — NOT VERIFIED; the Phase 3 experiment uses a deterministic Mock decision policy and real file tools.
- **Full HITL** — interactive approval/pause/resume is Phase 4.
- **Web UI**, parallel tool execution, custom seccomp/AppArmor profiles, and cost-budget enforcement are future work.

---

## Docker verification

Verified on 2026-11-08 with Docker Desktop 29.9.2:

```powershell
docker info
python -m pytest -m docker -q
# 4 passed in 18.33s

python -m agentforge demo-tools
# exit 0; real stdout:
# Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]
# Final report: Exit code: `0`; sandbox container recorded; stderr: (empty)

docker ps -a
# no AgentForge containers left
```

The Docker tests cover sandbox start/stop, non-root UID, blocked network, per-agent workspace isolation, configured CPU/memory/PID/read-only limits, and real prime-script execution. The full Phase 3 suite is `205 passed, 2 skipped`; the only skips are the Windows symlink-privilege test and the Docker-missing failure-path test (skipped because Docker was available).

The final report is grounded in the persisted `tool_calls` audit: stdout, stderr, exit code, duration, script path and sandbox container are forwarded as `[TOOL_EVIDENCE]` to synthesis/review/finalize. Mock content is still labelled, but it now quotes recorded facts rather than fixed placeholders.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Docker prerequisite missing` | Install/start Docker Desktop with WSL2; verify `docker info`. |
| `Docker sandbox unavailable` in a tool result | Same; Python tools never fall back to host execution. |
| `tool permission denied` | The agent role lacks the tool permission; see `agentforge tools`. |
| `pending_approval` / `git_push` blocked | Phase 2 has no interactive approval channel; push requires an approved gate. |
| `No database found` | Run a demo first or pass the same `--data-dir`. |
| `no API key is configured` | Fill `.env` or use `AGENTFORGE_LLM_PROVIDER=mock`. |
| PowerShell blocks activation | `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass`. |
| Legacy console garbles output | `$env:PYTHONIOENCODING="utf-8"`; use Windows Terminal. |
| Clean run | Delete `data/` contents except `.gitkeep`, or use a new `--data-dir`. |

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/TOOL_SYSTEM.md](docs/TOOL_SYSTEM.md)
- [docs/SANDBOX_SECURITY.md](docs/SANDBOX_SECURITY.md)
- [docs/MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md)
- [docs/PHASE1_REPORT.md](docs/PHASE1_REPORT.md)
- [docs/PHASE2_REPORT.md](docs/PHASE2_REPORT.md)
- [docs/PHASE3_REPORT.md](docs/PHASE3_REPORT.md)
- [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)

## License

MIT (course project).
