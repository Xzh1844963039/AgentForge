# AgentForge

**A memory-evolving multi-agent collaborative platform with autonomous task delegation, real tool execution, and Docker sandbox isolation.**

AgentForge is the final project for the HKUST NLP/Agent course. It is built incrementally; Phase 1 delivered the multi-agent runtime, and Phase 2 adds a validated tool-execution framework and a Docker-backed sandbox.

## Current status

| Phase | Status |
|---|---|
| Phase 1 — Runtime, messaging, DAG orchestration, persistence, CLI | ✅ complete |
| Phase 2 — Tool dispatch, private workspaces, Docker sandbox, Python/Search/Git tools | ✅ implemented; Docker integration **not verified** on this machine (Docker CLI unavailable) |
| Phase 3 — Memory evolution | ⏳ next |
| Phase 4 — Full human-in-the-loop | ⏳ planned |

**Verified on 2026-11-07:** `175 passed, 5 skipped` in the full pytest suite, `76%` statement coverage, Ruff F/E9 clean. The 5 skips are 4 Docker-dependent tests and 1 Windows symlink-privilege test. Docker integration is **NOT VERIFIED**, not faked.

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

---

## Quick start (Windows)

### 1. Prerequisites

- Python **3.11+** (verified with Python 3.12.10)
- Git (for version control and local Git-tool tests)
- Docker Desktop + WSL2 **only** for `python_execute`/`python_test` and `agentforge demo-tools`
- PowerShell or Git Bash

No API key is required for the deterministic demo and most tests. Docker is absent on the current development machine; the Docker parts report a prerequisite failure instead of silently executing on the host.

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

### 6. Run the tests

```powershell
pytest -q
pytest -m docker -q        # only with Docker running
```

Expected on the current machine:

```text
175 passed, 5 skipped
```

Optional coverage:

```powershell
pytest --cov=agentforge --cov-report=term -q
# TOTAL: 4637 statements, 1100 missed, 76% coverage
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
│   ├── memory/          per-agent memory interfaces
│   ├── models/          Pydantic contracts (messages, tasks, tools, workflows)
│   ├── persistence/     SQLite repository (tasks, tool_calls, artifacts, ...)
│   ├── evaluation/      workflow metrics including tool usage
│   ├── config/          centralized environment settings
│   ├── runtime.py       dependency wiring and public API
│   └── cli.py           CLI and REPL
├── tests/               180 collected: unit, integration, protocol, CLI, Docker-gated
├── examples/            mock demo, file-tool demo, real-provider example
├── docs/                architecture, plan, Phase 1/2 reports, tool & sandbox security
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
- SQLite + JSONL observability, CLI, deterministic demos, 175 passing tests.

Not implemented / pending:

- **Docker integration on this machine** — Docker CLI missing; tests skipped; `demo-tools` reports the prerequisite.
- **Live search** — HTTP provider tested with `httpx.MockTransport`, no live endpoint configured.
- **Real remote Git push** — only a temporary local bare remote was tested; default approval gate prevents pushes.
- **Real LLM tool-calling** — no DeepSeek/HKGAI key; mock decisions are deterministic fixtures.
- **Memory evolution** — reflection, decay, consolidation, semantic retrieval are Phase 3.
- **Full HITL** — interactive approval/pause/resume is Phase 4; Phase 2 only has a gate interface.
- **Web UI**, parallel tool execution, custom seccomp/AppArmor profiles, and cost-budget enforcement are future work.

---

## Docker verification commands

On a machine with Docker Desktop + WSL2:

```powershell
docker info
python -m pytest -m docker -q
python -m agentforge demo-tools
```

Expected Docker tests: sandbox start/stop, non-root UID, network blocked, per-agent workspace isolation, configured CPU/memory/PID limits, and real prime-script execution.

The current machine output:

```text
docker: command not found
Docker CLI not found on PATH. Install Docker Desktop with WSL2...
```

This is recorded honestly in [docs/PHASE2_REPORT.md](docs/PHASE2_REPORT.md); no Docker test was marked passed without Docker.

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
- [docs/PHASE1_REPORT.md](docs/PHASE1_REPORT.md)
- [docs/PHASE2_REPORT.md](docs/PHASE2_REPORT.md)
- [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)

## License

MIT (course project).
