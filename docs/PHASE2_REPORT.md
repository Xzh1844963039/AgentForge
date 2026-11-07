# Phase 2 Report — Tool Execution and Docker Sandbox Isolation

**Date:** 2026-11-07
**Phase:** 2 — Tool Execution & Docker Sandbox Isolation
**Environment:** Windows 10.0.26200 (x64), Python 3.12.10, pytest 8.4.2, jsonschema
4.26.0, Docker **not installed**, no WSL distribution, no Podman.
**Status:** Implementation complete; **Docker integration NOT VERIFIED** on this
machine. Non-Docker tool paths are verified with real files, Git and subprocess tests.

---

## 1. Executive summary

Phase 2 adds a real tool-execution layer and a Docker-backed sandbox to the Phase 1
runtime. Agents now request tools with a structured `tool_call` action; every request
goes through a single dispatcher that enforces JSON schemas, per-agent permissions,
approval gates, timeouts, output limits and durable audit. Agents receive the real
structured result, including failures, and continue reasoning.

Implemented tool families:

- private Markdown/TXT file tools with traversal/symlink/Windows-device protections;
- controlled artifact transfer between agents;
- `python_syntax_check`, `python_execute`, `python_test` (execution only in Docker);
- mock and HTTP search providers with SSRF-safe page fetching;
- local Git status/diff/log/add/commit/push with push approval;
- CLI inspection: `tools`, `tool-calls`, and `demo-tools`.

The Docker sandbox is fully implemented with one container and one private workspace
per agent, a non-root user, no network by default, CPU/memory/PID/time limits,
read-only root, dropped capabilities, no-new-privileges and no Docker socket. Because
Docker is unavailable here, Docker integration tests are **SKIPPED**, the Docker
end-to-end primes demo is **NOT VERIFIED**, and there is no host-side Python fallback.

---

## 2. Completed features

### 2.1 Tool framework

- `ToolSpec`: name, description, JSON input/output schema, timeout, permissions,
  approval flag, untrusted-output flag, output cap.
- `BaseTool`: typed Pydantic arguments/output and async `run`.
- `ToolRegistry`: registration, lookup, prompt catalog.
- `ToolExecutionContext`: agent, workflow, task, private workspace, sandbox.
- `ToolPermissionPolicy`: default-deny per-agent grants; approval-required permissions.
- `ApprovalGate`: pending/approved/denied decisions; default gate is pending.
- `ToolExecutor`: lookup → permission → schema → approval → timeout → execution →
  output schema → output cap → SQLite/JSONL audit.
- `ToolCallRecord`: durable audit with permission and approval state.
- Structured result statuses: `success`, `error`, `timeout`, `denied`,
  `pending_approval`.

### 2.2 File system tools

`list_files`, `read_file`, `write_file`, `update_file`, `delete_file`,
`search_files`.

- Only `.md` and `.txt` are accepted.
- Private root per agent: `<data_dir>/workspaces/<agent_id>/`.
- Rejects absolute paths, drive letters, `..`, control/NUL characters, Windows
  reserved device names, trailing spaces/dots and unsupported characters.
- Rejects symlink components; verifies the resolved path is under the agent root.
- Atomic writes; per-file byte cap; read truncation reporting; list/search caps.
- `write_file` refuses overwrite by default; `update_file` requires an existing file.
- `delete_file` requires approval.
- Every call is audited by the dispatcher.

### 2.3 Artifact transfer

- `share_artifact` copies a Markdown/TXT file from the caller's workspace into a
  central immutable store with SHA-256 and size metadata.
- `fetch_artifact` verifies integrity and writes a copy into the caller's own
  workspace.
- There is no direct path access to another agent's workspace.

### 2.4 Python tools

- `python_syntax_check`: static `ast.parse` only; no execution.
- `python_execute` / `python_test`: stage `.py` in the private workspace and execute
  inside the Docker sandbox.
- Return stdout, stderr, exit code, timeout flag, duration and script path.
- A nonzero exit is a structured tool error that still carries stdout/stderr for the
  agent to reason over and retry.
- If Docker is missing, the result is a structured `Docker sandbox unavailable`
  error. There is no host `subprocess` fallback.

### 2.5 Docker sandbox

`DockerSandbox` and `SandboxManager` implement:

- one named container per agent, one private bind-mounted workspace;
- non-root user `65534:65534`; root configuration rejected;
- `--network none` by default;
- CPU, memory/swap, PID and wall-clock limits;
- read-only root + noexec/nosuid `/tmp` tmpfs;
- `--cap-drop ALL`, `no-new-privileges:true`;
- no privileged mode, no Docker socket, no host env/API keys;
- stale container cleanup, `docker inspect` health check, container removal on
  timeout, manager shutdown cleanup;
- no host fallback.

### 2.6 Search tools

- `SearchProvider` interface with `MockSearchProvider` (offline, deterministic,
  explicitly labelled, reserved `.invalid` URLs) and `HttpSearchProvider`
  (configurable JSON endpoint, API key, timeout, retry).
- `web_search` returns title, URL, snippet and source; results are untrusted.
- `fetch_webpage` blocks localhost, private/special-use IPs, cloud metadata hosts,
  embedded credentials and non-80/443 ports; validates DNS answers and every redirect;
  caps redirects and response bytes; allows text/JSON/XML; marks content untrusted.
- No live search endpoint was configured; HTTP behavior is tested with
  `httpx.MockTransport`.

### 2.7 Git tools

`git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_push`.

- Only an explicitly authorized `AGENTFORGE_GIT_REPO_ROOT` is usable; tools are not
  registered otherwise.
- Argument-list execution, never a shell.
- Paths must be relative, inside the repo, without `..`/absolute paths.
- Add/commit require explicit path scopes.
- Credential-scrubbed environment; no token/secret/key/SSH variables.
- Push always requires approval; default returns `pending_approval` and does not run.
- Commit identity is deterministic (`AgentForge <agentforge@localhost>`).

### 2.8 Runtime integration

- `BaseAgent.execute_task` now runs a bounded tool loop:
  - LLM action → tool call(s) → dispatcher → `[TOOL_RESULTS]` → next LLM turn;
  - complete/delegate/fail ends the loop;
  - all turns are recorded in `agent_executions`;
  - tool calls are recorded in `tool_calls`.
- The orchestrator passes a tool context (agent, task, workflow, workspace, sandbox)
  and records each LLM turn.
- Workflow metrics add tool calls, tool failures and summed tool time.
- Tool-call results flow into the normal task result, synthesis and reviewer chain.
- Agent-initiated delegation with tools is verified: delegation count = 1, child
  research used `web_search`, parent resumed and used `write_file`.

### 2.9 CLI

New commands:

```text
agentforge tools                 # tool contracts, permissions, timeouts
agentforge tool-calls            # audit records with filters
agentforge demo-tools            # Docker/Python end-to-end demo
```

`demo`, `run`, `repl`, `tasks`, `messages`, `history`, `result`, `metrics` still work.
Workflow rendering now includes a tool execution table and tool metrics.

---

## 3. Architecture and integration

```text
Human goal → Runtime → Orchestrator → Coordinator plan
  → capability assignment → TASK_REQUEST → Agent
      → LLM tool_call → ToolExecutor
          → policy/schema/approval/timeout → tool
          → SQLite tool_calls + JSONL
      → [TOOL_RESULTS] → next LLM turn
      → COMPLETE / DELEGATE / FAIL
  → task result → synthesis → review → final report
```

The dispatcher is the only execution path. Agent prompts contain the live tool
catalog, permissions, schemas and the rule that tool output is untrusted. The LLM
never executes a tool directly and cannot bypass the permission policy by naming a
path or command.

Tool calls are not a fixed sequence: the model chooses them, observes results, and can
retry or change approach. The Mock provider uses deterministic fixtures for the demo,
but the file operations, tool dispatcher, audit, Python staging and (with Docker)
execution are real.

---

## 4. Sandbox isolation and security

Implemented flags:

```text
--user 65534:65534
--network none                (default)
--cpus <limit>
--memory <MB>m --memory-swap <same>
--pids-limit <n>
--read-only
--tmpfs /tmp:rw,noexec,nosuid,size=<MB>m
--cap-drop ALL
--security-opt no-new-privileges:true
--mount type=bind,source=<agent workspace>,target=/workspace
```

No `--privileged`, no Docker socket, no host env, no API keys, no extra capabilities.
See `docs/SANDBOX_SECURITY.md` for the threat model, residual risks and verification
commands.

Host-side workspace defenses:

- traversal/absolute/Windows-reserved-name checks;
- symlink component rejection and resolved-root containment;
- atomic writes; size caps; extension allow-list;
- controlled artifact transfer only;
- delete and push approval gates.

---

## 5. Tests actually executed

### Commands and results

```text
python -m pytest -q
175 passed, 5 skipped in 34.32s

python -m pytest --cov=agentforge --cov-report=term -q
175 passed, 5 skipped in 37.35s
TOTAL: 4637 statements, 1100 missed, 76% coverage

python -m ruff check src tests --select F,E9
All checks passed
```

### Skips (honest status)

| Test | Reason | Status |
|---|---|---|
| `test_docker_integration_execution_and_isolation` | Docker CLI/daemon unavailable | SKIPPED |
| `test_docker_integration_limits_are_configured` | Docker unavailable | SKIPPED |
| `test_python_execute_docker_integration` | Docker unavailable | SKIPPED |
| `test_phase2_primes_end_to_end` | Docker unavailable | SKIPPED |
| `test_symlink_escape_rejected` | Windows denied symlink creation for the test user (WinError 1314) | SKIPPED |

No Docker or live-network test was marked PASS. Protocol tests use
`httpx.MockTransport`; Git tests use temporary local repositories only.

### New test coverage

- Tool registry, duplicate/unknown tools, prompt catalog.
- JSON Schema argument validation and output validation.
- Dispatcher success/failure/timeout/denial/pending approval.
- Permission policy, approval metadata, output truncation, audit and event stream.
- File read/write/update/list/search/delete; extension/path/size/overwrite rules;
  cross-agent read rejection; symlink test skipped.
- Artifact share/fetch, ownership, integrity failure, size/extension limits.
- Docker run-argument lockdown; fake-runner lifecycle; timeout; root user rejection;
  manager workspace separation; Docker integration tests skipped.
- Python syntax check; fake sandbox success/failure/timeout; no-host-fallback sentinel;
  Docker Python integration skipped.
- Search mock/HTTP, retries, redirects, response caps, SSRF (localhost, private IPs,
  metadata, IPv6 loopback, DNS private answers, ports, credentials).
- Git status/diff/log/add/commit, explicit scope, path rejection, local bare remote
  push pending/approved/denied, injection-like refs, credential scrubbing.
- Runtime agent tool loop with real files, tool result propagation, metrics.
- Workspace isolation through the runtime and controlled artifact transfer.
- Delegation with tools: `delegation_count == 1`, `web_search` + `write_file`.
- Tool failures (Docker missing) fail the task and never create a host sentinel.
- CLI `tools`, `tool-calls`, `demo-tools` prerequisite.

---

## 6. End-to-end demos

### 6.1 Required Phase 2 demo — primes/Python/Docker

Command:

```powershell
python -m agentforge demo-tools
```

Actual result on this machine:

```text
exit code 2
Docker prerequisite missing
Docker CLI not found on PATH. Install Docker Desktop with WSL2...
No host-side Python fallback is used.
```

Status: **NOT VERIFIED** because Docker is absent. The implementation and tests are
present; the demo fails closed rather than executing on the host.

On a Docker-enabled machine, the expected flow is:

1. Human publishes the goal to the group.
2. Coordinator plans a coding task.
3. CodingAgent calls `python_syntax_check`, then `python_execute` with `primes.py`.
4. The script is staged in the private workspace and executed in the non-root,
   network-disabled container.
5. stdout, stderr and exit code are returned to the agent.
6. Synthesis → Reviewer audit → Coordinator Markdown report.
7. Tool calls and metrics are persisted and visible in `tool-calls`.

Run `docker info`, `pytest -m docker -q`, and `agentforge demo-tools` on that machine
to establish PASS/FAIL.

### 6.2 Non-Docker file-tool demo — verified

```powershell
python examples/tool_file_demo.py
```

Actual result:

```text
Workflow: completed
Tool calls:
  write_file   success
  read_file    success
File exists: True
File: .../workspaces/coding/notes/demo.md
Metrics:
  total_tasks: 4
  completed_tasks: 4
  task_completion_rate: 1.0
  llm_calls: 7
  tool_calls: 2
  tool_failures: 0
```

This verifies the real Runtime → agent → ToolExecutor → private filesystem →
tool result → agent reasoning path, without Docker.

### 6.3 Isolation verification

- Host-level: Agent B cannot read Agent A's workspace; traversal is rejected; a shared
  artifact can be fetched only after publication and hash verification.
- Docker-level: test implemented and SKIPPED because Docker is unavailable.

### 6.4 Delegation verification

The tool integration test uses a coding task that delegates a research subtask. The
child runs `web_search`; the parent resumes and runs `write_file`. Metrics show
`delegation_count = 1`, `max_depth = 2`; both tool calls are audited.

---

## 7. Known issues and limitations

1. **Docker integration unverified** — the largest open item. Docker CLI is missing;
   no WSL distribution or Podman is available. Tests are skipped, not faked.
2. **Symlink-escape runtime test skipped** — Windows denied symlink creation to the
   test user. Static path checks and component rejection are implemented; runtime
   verification is pending on a platform with symlink permission.
3. **No live search verification** — the HTTP provider is tested with MockTransport;
   no live endpoint/key was configured.
4. **No real LLM tool-calling verification** — no DeepSeek/HKGAI key. Mock tool
   decisions are deterministic fixtures; real-model schema adherence is unmeasured.
5. **No real remote Git push** — tests use a temporary local bare remote; the default
   approval gate prevents pushes.
6. **Approvals are pending-only** — the interactive human channel is Phase 4.
7. **No parallel tool execution** — multiple tool calls in one turn run sequentially.
8. **No disk quota** — per-file and listing limits exist, but total workspace disk use
   is not quota-enforced.
9. **No custom seccomp/AppArmor profile** — relies on Docker defaults plus cap-drop,
   no-new-privileges, read-only root and network none.
10. **Linux UID/workspace ownership caveat** — container user 65534 may require host
    permission adjustment on Linux; Windows Docker Desktop usually maps transparently.
11. **DNS rebinding residual risk** — web-fetch validates DNS, then httpx resolves
    again; a future proxy/pinned-IP design should close this.
12. **Image provenance** — `python:3.11-slim` is pulled by tag, not digest; no signature
    verification.
13. **Docker image pull** — `demo-tools` requires the host to pull the image if absent.
14. **Tool output/JSON-schema edge cases** — unusual Pydantic schemas may need
    additional tests; current tools are covered.
15. **No web UI, no memory evolution, no full HITL** — by phase design.

---

## 8. How to run

```powershell
cd D:\5202nlp课程project
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

# Phase 1 demo
python -m agentforge demo

# Tool inspection
python -m agentforge tools
python -m agentforge tool-calls

# Non-Docker file-tool demo
python examples/tool_file_demo.py

# Docker demo (requires Docker Desktop + WSL2 running)
docker info
python -m pytest -m docker -q
python -m agentforge demo-tools

# Full tests
pytest -q
pytest --cov=agentforge --cov-report=term -q
```

---

## 9. Docker verification checklist (for a Docker-enabled machine)

- [ ] `docker info` succeeds.
- [ ] `docker run --rm python:3.11-slim python -c "print('ok')"` succeeds.
- [ ] `pytest -m docker -q` runs and passes.
- [ ] Sandbox Python execution returns expected stdout and exit code.
- [ ] `os.getuid()` is 65534 (non-root).
- [ ] Network is blocked with default `--network none`.
- [ ] Agent B cannot read Agent A's file.
- [ ] `docker inspect` shows memory/PID/network/read-only limits.
- [ ] `agentforge demo-tools` completes and `tool-calls` shows
      `python_syntax_check` + `python_execute`.
- [ ] No host-side Python fallback occurs if Docker is stopped mid-run.

---

## 10. Git

Phase 2 changes are committed locally in coherent commits; no push to any remote is
performed. Commit hashes are reported in the final hand-off message and `git log`.
The existing Phase 1 history is preserved.

---

## 11. Recommended next phase

Proceed to **Phase 3 — Memory Evolution**:

1. extend `MemoryStore` with reflection/salience/decay and consolidation;
2. add embedding-based retrieval with per-agent isolation;
3. measure experience reuse on repeated tasks;
4. keep deterministic mock tests;
5. separately, on a Docker-enabled machine, run the Phase 2 Docker acceptance checklist
   and replace the SKIPPED statuses with real PASS/FAIL results.

Do not claim Phase 2 Docker isolation as verified until those commands pass.
