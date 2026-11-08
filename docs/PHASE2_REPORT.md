# Phase 2 Report — Tool Execution and Docker Sandbox Isolation

**Date:** 2026-11-08
**Phase:** 2 — Tool Execution & Docker Sandbox Isolation
**Environment:** Windows 10.0.26200 (x64), Python 3.12.10, pytest 8.4.2,
jsonschema 4.26.0, Docker Desktop 29.9.2 (daemon running)
**Status:** Complete and verified, including Docker integration and real sandbox
execution. Tool evidence propagation was fixed after a report-generation defect.

---

## 1. Executive summary

Phase 2 adds a real tool-execution layer and a Docker-backed sandbox to the Phase 1
runtime. Agents request tools with a structured `tool_call` action; every request goes
through one dispatcher that enforces JSON schemas, per-agent permissions, approval
gates, timeouts, output limits and durable audit. Agents receive real structured
results—including failures—and continue reasoning.

Verified outcomes:

```text
Docker integration tests: 4 passed
Full test suite:          186 passed, 2 skipped
Coverage:                 78% (4835 statements, 1062 missed)
python_execute:           success
stdout:                   Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]
docker ps -a:             no AgentForge containers left
```

The 2 skips are:

1. a Windows symlink-escape test (the OS denied symlink creation for the test user);
2. the Docker-missing failure-path test (skipped because Docker was available).

No Docker or live-network result is fabricated.

### Report-generation incident and fix

The user observed that `python_execute` succeeded, but the final report displayed:

```text
The sandbox returned: (not available)
Exit code: unknown.
```

Root cause and fix are detailed in §3. The final report now shows the real stdout,
stderr, exit code, duration, script path and sandbox container from the persisted
`tool_calls` audit.

---

## 2. Completed features

### Tool framework

- `ToolSpec`: name, description, JSON input/output schemas, timeout, permissions,
  approval flag, untrusted-output flag, output cap.
- `BaseTool`, `ToolRegistry`, `ToolExecutionContext`, `ToolPermissionPolicy`,
  `ApprovalGate`, `ToolExecutor`, `ToolResult`, `ToolCallRecord`, `ToolEvidence`.
- Dispatcher order: lookup → permissions → input schema → approval → timeout →
  execution → output schema → output cap → SQLite/JSONL audit.
- Structured statuses: success, error, timeout, denied, pending_approval.
- Default-deny per-agent permission matrix; destructive actions require approval.

### File and artifact tools

`list_files`, `read_file`, `write_file`, `update_file`, `delete_file`,
`search_files`, plus `share_artifact` / `fetch_artifact`.

- Private workspace per agent: `<data_dir>/workspaces/<agent_id>/`.
- Public file tools accept only `.md` / `.txt`.
- Rejects absolute paths, drive letters, `..`, control/NUL characters, Windows
  reserved device names, trailing spaces/dots, unsupported characters.
- Rejects symlink components; resolved path must remain under the agent root.
- Atomic writes; per-file and listing/search limits.
- Delete requires approval; cross-agent data uses hash-verified artifacts only.

### Python tools

- `python_syntax_check`: static `ast.parse`, no execution.
- `python_execute` / `python_test`: stage `.py` in the private workspace and execute
  inside Docker only.
- Return stdout, stderr, exit code, timeout flag, duration and script path.
- Docker missing → explicit `Docker sandbox unavailable`; no host fallback.

### Docker sandbox

- One container and one private bind-mounted workspace per agent.
- Non-root user `65534:65534`; root configuration rejected.
- `--network none` by default; CPU/memory/swap/PID/wall-clock limits.
- Read-only root; noexec/nosuid `/tmp` tmpfs.
- `--cap-drop ALL`; `no-new-privileges`; no privileged mode; no Docker socket;
  no host env/API keys.
- Stale-container cleanup; health check; container removal on timeout; manager
  shutdown cleanup.

### Search and Git tools

- `web_search` / `fetch_webpage` with offline Mock provider and configurable HTTP
  provider; SSRF guard; redirect/size/content-type limits; untrusted output.
- `git_status`, `git_diff`, `git_log`, `git_add`, `git_commit`, `git_push` restricted
  to an authorized repository, argument-list execution, path scoping,
  credential-scrubbed environment, and mandatory push approval.

### Agent/runtime integration

- Bounded tool loop in `BaseAgent`: LLM → tool_call → dispatcher → `[TOOL_RESULTS]`
  → next turn.
- Every LLM turn and every tool call is persisted separately.
- Workflow metrics include tool calls, failures and summed tool time.
- Structured `[TOOL_EVIDENCE]` from SQLite is passed to synthesis, review and
  finalization tasks.
- CLI: `tools`, `tool-calls`, `demo-tools`; tool tables in workflow rendering.

---

## 3. Tool-result evidence propagation (defect and fix)

### 3.1 Required chain

```text
ToolResult
  → Agent tool loop (tool_results)
  → Task result + task metadata (tool_call_ids)
  → persisted tool_calls audit (SQLite)
  → [TOOL_EVIDENCE] in synthesis/review/finalize prompts
  → final Markdown report
```

### 3.2 Root cause

The failure was **data/context loss plus Mock prose dependence**, not a Docker or
python_execute failure:

1. **Finalization lacked raw execution context.** Finalize depends on synthesis and
   review; its direct dependency results contained only those two narrative outputs,
   not the coding task or raw `ToolResult`. The synthesis/review Mock fixtures were
   fixed prose and did not quote stdout or exit code.
2. **The final Mock summary scraped prose with regexes.** It searched the prompt for
   `"Primes up to 20: ..."` and `"Exit code: ..."`. Those strings were absent from the
   finalize prompt, so it fell back to `"(not available)"` and `"unknown"`.
3. **The reviewer used a fixed environment warning.** It claimed Docker availability
   was the residual risk even when a real successful sandbox run had occurred.

This was reproducible with the old code even though the audit contained the correct
stdout, exit code, duration and container metadata.

### 3.3 Fix

- Added `ToolEvidence` derived from persisted `ToolCallRecord` rows. It includes
  tool, agent, task, status, success, output, error, duration, permissions, approval
  state, timestamps and sandbox metadata (container/script/truncation).
- `TaskOrchestrator` computes the transitive dependency closure (including delegated
  child tasks) and loads tool calls from SQLite for those tasks. No prose scraping.
- Agent prompts now include a structured `[TOOL_EVIDENCE]` JSON block with the rule
  that numeric/status facts are authoritative and text fields are untrusted data.
- Tasks store `tool_call_ids` and `tool_call_count` in metadata for traceability.
- Mock synthesis/review/finalize now construct their reports from `[TOOL_EVIDENCE]`:
  - exact stdout/stderr/exit code/duration/script/container are quoted;
  - success, nonzero exit, missing exit code, exception and timeout produce honest
    verdicts;
  - empty stdout is shown as `(empty)`, not `(not available)`;
  - missing evidence is explicitly reported and never replaced with fabricated data.
- Non-prime synthesis/review/finalize reports append a persisted-audit evidence
  appendix; a failing tool forces a `REJECTED` review verdict.
- Reviewer decisions now use recorded execution facts; the fixed Docker warning is
  gone for successful runs.

### 3.4 Before/after

Before:

```text
The sandbox returned:
(not available)

Exit code: unknown.
```

After (actual demo-tools output):

```text
3. Execution evidence
- Tool calls recorded: 2
- python_execute status: success
- Exit code: 0
- Script path: scripts/primes.py
- Sandbox container: agentforge-sandbox-coding-dd7a67e1
- Tool duration: 0.348s

stdout:
Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]

stderr:
(empty)

4. Review
Verdict: APPROVED_WITH_ISSUES. ... persisted tool audit ...
```

### 3.5 Verification of preserved fields

The Docker end-to-end test now asserts the persisted `tool_calls` row contains:

- stdout exactly `Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]`
- stderr `""`
- exit code `0`
- success `True`
- measured duration
- script path `scripts/primes.py`
- sandbox metadata container `agentforge-sandbox-coding-*`

The synthesis, review and final result must each contain the real stdout; review must
not contain the old Docker-availability warning; final must not contain
`(not available)` or `Exit code: unknown`.

---

## 4. Architecture and integration

```text
Human goal → Coordinator plan → capability assignment
  → TASK_REQUEST → Agent LLM
      → tool_call → ToolExecutor
          → permission/schema/approval/timeout
          → tool execution
          → SQLite tool_calls + JSONL
      → [TOOL_RESULTS] → next LLM turn
  → task result + tool_call_ids
  → synthesis/review/finalize prompts include [TOOL_EVIDENCE]
  → final report
```

Structured evidence is loaded only through the dependency closure and delegation
lineage, preserving the controlled data-flow boundary. Direct cross-agent workspace
reads remain impossible; artifact transfer remains the only file-sharing path.

---

## 5. Sandbox isolation verification

Docker tests (4/4 passed):

- sandbox start/stop/health;
- real Python execution and stdout;
- non-root UID 65534;
- blocked network under `--network none`;
- Agent A file not readable in Agent B's container;
- `docker inspect` confirms network mode, read-only root, memory and PID limits,
  and `CapDrop=["ALL"]`.

`docker ps -a` after tests showed no AgentForge containers.

---

## 6. Tests actually executed

```text
python -m pytest -q
186 passed, 2 skipped in 56.23s

python -m pytest -m docker -q
4 passed, 183 deselected in 18.33s

python -m pytest --cov=agentforge --cov-report=term -q
186 passed, 2 skipped in 59.72s
TOTAL: 4835 statements, 1062 missed, 78% coverage

python -m ruff check src tests --select F,E9
All checks passed
```

New evidence tests in `tests/test_report_evidence.py`:

1. normal execution quotes real stdout/exit/duration/container;
2. empty stdout is `(empty)`, not missing;
3. nonzero exit rejected and reported;
4. exception evidence does not fabricate stdout;
5. timeout reported and rejected;
6. multiple tool calls all visible;
7. missing exit code is not treated as success;
8. report ignores contradictory task prose when audit evidence disagrees.

Cross-agent integration test now verifies:

- coding task metadata contains 2 `tool_call_ids`;
- synthesis/review/finalize prompts contain `[TOOL_EVIDENCE]`;
- write/read file evidence and content reach the final report;
- final report appends the persisted audit evidence.

---

## 7. End-to-end demos

### 7.1 Required primes/Python/Docker demo — PASS

```powershell
python -m agentforge demo-tools
# exit 0
```

Observed:

```text
Docker: 29.9.2
python_syntax_check  success  0.001s
python_execute       success  2.100s  Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]
Review verdict: APPROVED_WITH_ISSUES
Task completion: 100% (4/4)
Tool calls / failures: 2 / 0
```

Final report includes real stdout, exit code `0`, stderr `(empty)`, duration, script
path, container name and audit IDs. `docker ps -a` showed no leftover containers.

### 7.2 Non-Docker file-tool demo — PASS

```powershell
python examples/tool_file_demo.py
```

Real write/read tool calls succeed; final report includes the persisted evidence
appendix. Only LLM decisions are deterministic mock fixtures.

### 7.3 Isolation and delegation

- Direct cross-agent workspace read rejected.
- Artifact share/fetch verified with SHA-256.
- Delegation with tools: `delegation_count = 1`, `max_depth = 2`, child `web_search`,
  parent `write_file`.

---

## 8. Known issues and limitations

1. **Windows symlink runtime test skipped** — OS denied symlink creation for the test
   user; implementation rejects symlink components, runtime verification pending on a
   platform with symlink permission.
2. **Live search not verified** — HTTP provider tested with MockTransport; no live
   endpoint/key configured.
3. **Real LLM tool-calling not verified** — no DeepSeek/HKGAI key; Mock decisions are
   deterministic fixtures.
4. **Real remote Git push not performed** — local bare remote only; push approval
   remains pending by default.
5. **Approval channel is pending-only** — full interactive HITL is Phase 4.
6. **Tool evidence scope** — evidence is limited to the task dependency/delegation
   closure by design; unrelated branches are not shared. Tool arguments are retained
   in SQLite but not injected into final-report prompts (only output/status/metadata).
7. **Sequential tool execution** — multiple calls per turn run sequentially.
8. **No total disk quota / custom seccomp profile** — per-file limits and Docker
   defaults are used.
9. **DNS rebinding residual risk** in `fetch_webpage`.
10. **Linux UID mapping** — container 65534 may need host workspace permission
    adjustment on Linux; Windows Docker Desktop worked.
11. **Image provenance** — image pulled by tag, not digest.
12. **Generic non-prime Mock reports** — the main narrative remains a deterministic
    fixture; the appended evidence appendix is real and failure-aware. Real LLM mode
    receives the same structured evidence for narrative generation.

---

## 9. How to run

```powershell
cd D:\5202nlp课程project
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

python -m agentforge demo
python examples/tool_file_demo.py

docker info
python -m pytest -m docker -q
python -m agentforge demo-tools

python -m agentforge tools
python -m agentforge tool-calls

pytest -q
pytest --cov=agentforge --cov-report=term -q
```

---

## 10. Docker verification checklist

- [x] `docker info` succeeds (29.9.2).
- [x] Docker image is available/executable.
- [x] `pytest -m docker -q` → 4 passed.
- [x] Sandbox Python execution returns expected stdout and exit 0.
- [x] `os.getuid() == 65534`.
- [x] Network blocked under `--network none`.
- [x] Agent B cannot read Agent A's file.
- [x] `docker inspect` shows memory/PID/network/read-only/cap-drop limits.
- [x] `agentforge demo-tools` completes and shows `python_syntax_check` + `python_execute`.
- [x] No leftover containers after tests/demo.
- [ ] Mid-run daemon-stop behavior (not explicitly exercised).

---

## 11. Git

The fix is committed locally with the existing Phase 2 history preserved. No remote
push is performed. Hashes are reported in the final hand-off message and `git log`.

---

## 12. Recommended next phase

Proceed to **Phase 3 — Memory Evolution**:

1. extend `MemoryStore` with reflection/salience/decay/consolidation;
2. add embedding-based retrieval with per-agent isolation;
3. measure experience reuse on repeated tasks;
4. keep deterministic mock tests;
5. retain the `[TOOL_EVIDENCE]` contract as the audit-grounding mechanism for reports.

Do not claim live search or real-LLM tool-calling until those acceptance runs exist.
