# AgentForge

**A memory-evolving multi-agent collaborative platform with autonomous task delegation, real tool execution, Docker sandbox isolation, durable human-in-the-loop control, and a reproducible benchmark/ablation harness.**

AgentForge is the final project for the HKUST NLP/Agent course (current version **0.6.0**, Phase 6A pre-final). It is built incrementally: Phase 1 delivered the multi-agent runtime, Phase 2 the validated tool framework and Docker sandbox, Phase 3 evidence-first memory evolution, Phase 4 persistent approvals/clarifications and workflow control, and Phase 5 a standardized benchmark, ablation, fault-injection and live-acceptance harness.

## Current status

| Phase | Status |
|---|---|
| Phase 1 — Runtime, messaging, DAG orchestration, persistence, CLI | ✅ complete |
| Phase 2 — Tool dispatch, private workspaces, Docker sandbox, Python/Search/Git tools | ✅ complete; Docker integration verified (4/4) |
| Phase 3 — Evidence-first memory evolution, procedural promotion, feedback, ablation | ✅ complete (pipeline verified; real-LLM learning NOT VERIFIED) |
| Phase 4 — Human-in-the-loop, approvals, clarification, pause/resume/cancel, reassignment | ✅ complete (runtime verified; real-LLM HITL NOT VERIFIED) |
| Phase 5 — Benchmark harness, 34-task dev set, ablation, reliability, performance | ✅ complete (fixture benchmark verified; live LLM NOT VERIFIED) |
| Phase 6A — Provider/structured-output hardening, staged live acceptance, final readiness | ✅ engineering complete; HKGAI Stage 1 passed; real-runtime Stage 2 **PARTIALLY_VERIFIED** (A/B/C live; D–G blocked by gateway follow-up disconnects) |
| Phase 6 — Final submission and authorized live execution | ⏳ awaiting credentials and official test set |

**Verified on 2026-11-10:** `300 passed, 2 skipped` in the full pytest suite with Docker running; Docker integration **5/5 passed**; Ruff F/E9 clean; fixture benchmark **33/34 verified passes + 1 needs-review, 0 failures**. The 2 skips are the Windows symlink-privilege test and the Docker-missing failure-path test. Real-LLM Stage 1 connectivity passed with HKGAI. The compact planner is now wired into the real runtime (`auto` → compact for HKGAI; full retained for other providers), and the agent action prompt was reduced from ~9,640 to ~1,110 chars with a bounded tool ledger. A bounded real Stage 2 run (10/12 authorized requests, 4,057/20,000 tokens) verified live that the real runtime produces a valid plan and dispatches it by capability (A/B/C), but **D–G are still unverified**: every follow-up request in a session (repair or agent action, 1.3k–5.2k chars) was cut by the HKGAI gateway at ~56–64s with no response headers. Stages 3–6, real-LLM HITL and memory reuse remain **NOT VERIFIED**. No key was logged or committed. See `docs/HKGAI_STAGE2_REAL_RUN.md` and `docs/HKGAI_COMPACT_PLANNER.md`.

Phase 6A highlights:

- DeepSeek/HKGAI HTTP compatibility hardened: configurable endpoint/model, full-endpoint URLs, content-part responses, usage parsing, auth/rate-limit/timeout propagation and key redaction.
- Structured action/plan repair is bounded and audited; plain text is not silently treated as task success.
- New hard limits: per-task tokens, workflow LLM calls and repeated identical tool actions.
- Six-stage live acceptance ladder with cumulative request/token budgets and explicit `--allow-live-api`; no key means all stages `NOT_VERIFIED`.
- Live memory protocol separates learning tasks from evaluation and records retrieval/applied IDs and action changes.
- Benchmark correctness audit fixed budget success accounting and added sample-size/token-source caveats.
- New provider, repair, budget, staged-live and correctness tests; final evaluation protocol and submission checklist prepared.

Phase 5 highlights:

- `BenchmarkRunner` runs one task or a whole dataset in per-task isolated databases/workspaces, with resumable batches and JSON/JSONL/CSV/Markdown exports.
- 34 development tasks cover research, code generation, debugging, file editing, planning, delegation, code execution, approvals, clarification and failure recovery; every task has an objective verifier or an explicit manual-review requirement.
- Python tasks are verified by independent Docker-run tests, not by agent prose; missing Docker is reported as skipped, never passed.
- A/B/C ablation compares single-agent, multi-agent and multi-agent + memory under identical tasks, provider, budgets and verifiers.
- Live acceptance requires an API key **and** explicit `--allow-live-api`; without both, all live checks are `NOT_VERIFIED` and a fixture harness check validates the plumbing.
- High-risk tools persist an intent before execution. If the audit write fails after a side effect, the run enters `NEEDS_RECONCILIATION` and replay is blocked — no exactly-once claim.
- Fault-injection tests cover invalid JSON, schema errors, repeated actions, timeouts, Docker death, cancellation, approval expiry, unanswered clarifications, delegation limits, SQLite outage, memory failure, process restart, effect replay and changed approval arguments.
- SQLite query indexes and a tool-catalog cache were added with measured before/after data.

Phase 4 highlights:

- `ApprovalService` persists parameter-bound approval requests with risk, expiry and a canonical argument digest.
- File deletion, in-place updates and explicit overwrites (`write_file overwrite=true`) are approval-gated alongside `git_push`.
- Approval decisions pause the real task and workflow; resuming consumes the token exactly once in `ToolExecutor`.
- Changing tool arguments invalidates the old approval instead of widening its scope.
- `ClarificationService` supports `OPEN → ANSWERED / EXPIRED / CANCELLED`, persists across restart and injects the human answer into the resumed task prompt.
- Workflow `PAUSED`, `CANCELLED` and restart recovery use real control checks before LLM turns and tool dispatches.
- Successful tool effects are replayed from the SQLite audit after pause/resume/crash, preventing duplicate side effects.
- Cancelling a workflow cancels pending approvals/clarifications, marks unfinished tasks cancelled and stops its Docker sandboxes.
- Delegated tasks can be rejected and automatically reassigned to another fully capable agent; two-level A2A delegation is tested end-to-end.
- Memory retrieval feedback is scoped by workflow/task/execution ID instead of a time window.
- Metrics cover approvals, clarifications, pauses, resumes, cancellations, delegation outcomes and reassignments.

Phase 3/2 highlights:

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

### 6. Run the Phase 4 HITL demos (no Docker required)

```powershell
python examples/phase4_demos.py
```

The script runs three end-to-end demos through the real runtime, message bus, task
state machine, approval/clarification services, SQLite audit and replay cache:

- **A — approval rejection:** CodingAgent requests deletion of a real workspace
  file, the human rejects it, the workflow resumes, the file is preserved and the
  tool audit records `pending_approval → denied`.
- **B — clarification:** the agent asks a question, the workflow pauses and
  persists, the human answers `90 days`, and the resumed task receives the answer.
- **C — two-level delegation:** Coding delegates to Research, Research delegates
  to Reviewer, both delegated tasks complete at depths 2 and 3, and the parent
  workflow produces a final report.

Only LLM decisions are deterministic fixtures. Tool execution, file effects,
messages, state transitions, approvals, clarifications and persistence are real.
Real-provider HITL remains **NOT VERIFIED** without an API key.

### 7. Run the Phase 3 memory experiment (no Docker required)

```powershell
python -m agentforge memory experiment
```

This runs two learning workflows with REAL private-workspace file tools, promotes a
procedural memory after two distinct real workflows, then runs the same task with and
without memory. The verified run changes the tool argument from
`notes/default.md` to `notes/verified.md` and records `used=True success=True` feedback.
LLM decisions are a deterministic Mock policy, so the result proves the memory
pipeline, **not real-LLM learning performance**.

### 8. Run the Phase 5 benchmark (no API key; Docker for Python checks)

```powershell
python -m agentforge benchmark list --dataset benchmarks/dev
python -m agentforge benchmark run --dataset benchmarks/dev --output-dir benchmarks/runs/dev_full
python -m agentforge benchmark compare --dataset benchmarks/dev --output-dir benchmarks/runs/ablation_official
python -m agentforge benchmark live --provider deepseek --output-dir benchmarks/runs/live_official
```

The first command lists 34 development tasks. `run` creates isolated per-task
databases/workspaces and writes `report.md`, `runs.jsonl`, `runs.json`,
`runs.csv` and `failures.json`. `compare` runs the A/B/C ablation.
`live` without a key and explicit `--allow-live-api` performs **no paid API
call**; it records all live checks as `NOT_VERIFIED` and validates the harness
with a three-task fixture run.

The fixture provider is a deterministic harness driver. Fixture benchmarks prove
that the runtime, tools, verifiers, budgets and reporting work; they do **not**
measure real-LLM capability. Python tasks are verified with independent real
Docker tests.

Performance evidence:

```powershell
python benchmarks/performance/measure.py
```

### 9. Run Phase 6A live acceptance (requires authorization)

```powershell
# Connectivity only
python -m agentforge benchmark live --provider deepseek --stage 1 --allow-live-api

# Full stage ladder with hard budgets
python -m agentforge benchmark live --provider deepseek --stage all --allow-live-api `
  --max-requests 120 --max-tokens 250000
```

Stages: connectivity → structured output → real tools/Docker → multi-agent
collaboration → HITL/memory → full live benchmark. A stage failure stops later
stages. Without a key **and** `--allow-live-api`, no request is made and all
stages are `NOT_VERIFIED`. The command never uses a ZCode/internal model.

For the current HKGAI Stage 2 gateway-disconnect investigation, a one-request
streaming probe is prepared:

```powershell
python -m agentforge benchmark hkgai-probe --provider hkgai --allow-live-api `
  --max-tokens 256 --timeout 120 --output-dir benchmarks/runs
```

It sends the exact failed Coordinator planning prompt with `stream=true`, no
retries, no tools, max 1 request and a 10,000-token probe budget. It is not run
automatically.

A one-request Compact Planner probe is also available:

```powershell
python -m agentforge benchmark hkgai-compact-planner --provider hkgai `
  --allow-live-api --timeout 120 --output-dir benchmarks/runs
```

It uses the 889-character compact prompt (versus 2,290 for the full planner),
`stream=false`, `max_tokens=768`, no retries, no tools and a 1-request budget.
The authorized run on 2026-11-09 passed in 13.5s with a valid one-subtask plan;
see `docs/HKGAI_COMPACT_PLANNER.md`. Passing this probe does **not** mark full
Stage 2 complete.

The bounded real-runtime Stage 2 acceptance runner is:

```powershell
python -m agentforge benchmark hkgai-stage2 --provider hkgai `
  --allow-live-api --timeout 120 --max-requests 12 --max-tokens 20000 `
  --request-delay 60 --output-dir benchmarks/runs
```

It drives one full runtime workflow (Coordinator → capability dispatcher →
agent action → tool → evidence), caps every request at 1,024 tokens, disables
provider retries, records per-call prompt size/tokens/latency/HTTP status and
stops immediately on a pre-response disconnect. It labels results
`PASSED` only for a fully live A–G run; `MOCK_VALIDATED` for offline scripted
runs; `PARTIALLY_VERIFIED` when only some checks are backed by live evidence.
See `docs/HKGAI_STAGE2_REAL_RUN.md` for the 2026-11-10 run and the gateway
finding.

### 10. Run the tests

```powershell
pytest -q
pytest -m docker -q        # only with Docker running
```

Expected on the current machine (Docker running):

```text
300 passed, 2 skipped
```

The Phase 6A/6B tests add provider request/response/retry/redaction tests,
optional SSE streaming parsing, compact/full planner and agent-protocol
selection, truncation-safe repair, the bounded real Stage 2 runner (budget caps,
stop-on-disconnect, A–G evaluation), bounded structured-output repair,
token/LLM/repeated-action limits, live stage no-key safety, live-budget
enforcement, the live memory harness and benchmark correctness checks.
Phase 4/5 tests remain active.

Optional coverage:

```powershell
pytest --cov=agentforge --cov-report=term -q
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

HKGAI requires `AGENTFORGE_HKGAI_BASE_URL` and `AGENTFORGE_HKGAI_MODEL` from the course. `AGENTFORGE_HKGAI_STREAM=false` is the default; set it to `true` to use SSE streaming. `AGENTFORGE_PLANNER_PROTOCOL=auto` (the default) selects the ~911-character compact planner for HKGAI and the full planner for DeepSeek/mock. `AGENTFORGE_AGENT_PROTOCOL=auto` additionally selects the minimal HKGAI action prompt (~1.1k-char system prompt, one-line tool catalog, bounded tool ledger; policy tasks get a 214-char no-tools prompt) while other providers keep the full action protocol. Use `full`/`compact` to override either explicitly. Compact is the recommended HKGAI mode, but it does not by itself mark full Stage 2 complete — see `docs/HKGAI_STAGE2_REAL_RUN.md`.

### Robustness limits

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_STRUCTURED_OUTPUT_REPAIRS` | `1` | Bounded JSON action/plan repair attempts |
| `AGENTFORGE_REQUIRE_STRUCTURED_ACTIONS` | `true` | Reject plain text in task mode |
| `AGENTFORGE_PLANNER_PROTOCOL` | `auto` | Planner prompt: auto (compact for HKGAI), full, or compact |
| `AGENTFORGE_AGENT_PROTOCOL` | `auto` | Agent action/tool prompt: auto (compact for HKGAI), full, or compact |
| `AGENTFORGE_MAX_TASK_TOKENS` | `120000` | Per-task token guard |
| `AGENTFORGE_MAX_LLM_CALLS_PER_WORKFLOW` | `200` | Workflow-wide LLM-call guard |
| `AGENTFORGE_MAX_REPEATED_TOOL_ACTIONS` | `3` | Stop repeated identical tool calls |

Live acceptance additionally enforces `--max-requests` and `--max-tokens` across
all stages. No paid call is made without a key and explicit `--allow-live-api`.

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

### Human-in-the-loop

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_APPROVAL_TTL_SECONDS` | `3600` | Time before a pending approval expires |
| `AGENTFORGE_CLARIFICATION_TTL_SECONDS` | `86400` | Time before an open clarification expires |
| `AGENTFORGE_MAX_REASSIGNMENTS` | `2` | Max automatic reassignments after a delegated task rejection |
| `AGENTFORGE_WORKFLOW_EVENT_LIMIT` | `1000` | Max lifecycle events returned per workflow |

Approvals and clarifications are stored in the main SQLite database. They contain
the exact action, arguments, canonical SHA-256 argument digest, scope
(workflow/task/agent), risk, expiry and decision. See
[docs/HITL_ARCHITECTURE.md](docs/HITL_ARCHITECTURE.md),
[docs/APPROVAL_SYSTEM.md](docs/APPROVAL_SYSTEM.md) and
[docs/WORKFLOW_LIFECYCLE.md](docs/WORKFLOW_LIFECYCLE.md).

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
agentforge metrics [workflow_id]       Completion, timing, tokens, tools, HITL
agentforge approvals list/show         Inspect persisted approval requests
agentforge approvals approve/reject    Record a human decision
agentforge clarification list/show     Inspect clarification requests
agentforge clarification answer        Answer a clarification
agentforge workflows list/show         Inspect workflow and lifecycle events
agentforge workflows pause/resume      Pause or resume a workflow
agentforge workflows cancel            Cancel and clean up a workflow
agentforge benchmark list              List internal benchmark tasks
agentforge benchmark run               Run one/all tasks, resumable and exported
agentforge benchmark compare           Run A/B/C ablation
agentforge benchmark report            Regenerate Markdown report
agentforge benchmark failures          Show classified failures
agentforge benchmark live [--stage N]   Staged real-LLM acceptance (explicit opt-in)
```

Agent IDs: `coordinator`, `research`, `coding`, `reviewer`.

REPL: `/agents`, `/tools`, `/tool-calls [workflow_id]`, `/dm`, `/goal`, `/tasks`,
`/messages`, `/conversations`, `/history`, `/result`, `/metrics`, `/approvals`,
`/approve`, `/reject`, `/clarifications`, `/answer`, `/workflows`, `/pause`,
`/resume`, `/cancel`, `/quit`.

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
3. reuses a recorded successful effect when replaying a resumed task with identical arguments,
4. for approval-gated operations, validates the bound token and consumes it exactly once,
   or persists a pending request and suspends the workflow,
5. executes with a hard timeout and output cap,
6. validates structured output,
7. records the call in SQLite (`tool_calls`) and JSONL,
8. returns stdout/structured output/error to the agent, which continues reasoning.

Approval enforcement is inside the dispatcher, not in the prompt. An agent role that
lacks `file_delete` is denied before any approval is considered; an approval can
only authorize the exact tool, arguments, workflow, task and agent that were
reviewed.

Synthesis, review and finalization receive a structured `[TOOL_EVIDENCE]` block built
from the persisted `tool_calls` audit (stdout, stderr, exit code, success, duration,
script path and sandbox container). Reports quote those recorded facts instead of
scraping model prose; Mock content remains deterministic but is evidence-grounded.

Built-in tools:

| Tool | Purpose |
|---|---|
| `list_files`, `read_file`, `write_file`, `update_file`, `delete_file`, `search_files` | Private Markdown/TXT workspace; delete, update and explicit overwrite require approval |
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

## Human-in-the-loop at a glance

Phase 4 turns approval gates into an actual workflow protocol:

```text
delete_file requested
  → ToolExecutor persists a PENDING approval bound to exact arguments
  → task set PENDING, workflow PAUSED, agent execution stops
  → human: agentforge approvals show / approve | reject
  → agentforge workflows resume <id>
  → approved token consumed exactly once; or denial returned with no side effect
```

- Clarifications use `agentforge clarification list/show/answer` and resume with the
  answer injected into the task prompt.
- `agentforge workflows pause|cancel` writes a durable control state; a live task
  observes it at the next control check.
- A task left `RUNNING` by a crash is recovered to `PENDING` on resume and replayed.
- The replay cache returns prior successful tool outputs for identical
  workflow/task/tool/arguments instead of executing them twice.
- Cancellation closes pending approvals/clarifications, marks tasks cancelled and
  stops the workflow's Docker sandboxes.
- Workflow metrics count approvals, clarifications, pauses, resumes, cancellations,
  delegated tasks, success rate and reassignments.

See [docs/HITL_ARCHITECTURE.md](docs/HITL_ARCHITECTURE.md),
[docs/APPROVAL_SYSTEM.md](docs/APPROVAL_SYSTEM.md) and
[docs/WORKFLOW_LIFECYCLE.md](docs/WORKFLOW_LIFECYCLE.md).

---

## Benchmarking at a glance

Phase 5 adds a reproducible evaluation harness independent of the product CLI:

```text
BenchmarkDataset (34 JSONL tasks)
  → isolated runtime per task (DB, workspace, memory, budgets)
  → real tools / Docker / HITL state machine
  → independent verifier (file, Python, audit, workflow, graph, manual review)
  → per-run metrics + failure classification
  → JSONL / JSON / CSV / Markdown batch record
```

The internal dev set covers all ten required categories with basic and hard
tasks: research, Python generation, debugging, file editing, planning,
delegation, execution/testing, approval, clarification and failure recovery.
Success is never inferred from `workflow.completed` or model self-report.

Verified fixture result (harness validation, **not LLM performance**):

```text
34 runs: 33 verified passes, 1 needs-review, 0 failures, 0 skips
verified completion: 97.1%
average E2E: 0.722s; median 0.125s; p95 2.234s
```

Ablation with identical tasks/provider/budgets:

| Config | Passed | Avg E2E | Avg tokens |
|---|---:|---:|---:|
| Single agent | 6/6 | 1.104s | 15,819 |
| Multi-agent | 6/6 | 1.112s | 19,085 |
| Multi-agent + memory | 6/6 | 1.135s | 20,098 |

The fixture policy is deterministic and does not consult memory for answers, so
the ablation validates plumbing and measures coordination overhead only. Live
LLM checks remain **NOT_VERIFIED** until a key and explicit `--allow-live-api`
are supplied.

See [docs/BENCHMARK_DESIGN.md](docs/BENCHMARK_DESIGN.md),
[docs/EVALUATION_METRICS.md](docs/EVALUATION_METRICS.md),
[docs/ABLATION_STUDY.md](docs/ABLATION_STUDY.md),
[docs/FAILURE_ANALYSIS.md](docs/FAILURE_ANALYSIS.md) and
[docs/PHASE5_REPORT.md](docs/PHASE5_REPORT.md).

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
│   ├── interaction/     approval/clarification services and HITL repository
│   ├── benchmark/       schema, datasets, verifiers, metrics, runner, ablation, live
│   ├── memory/          evidence-first evolution, embeddings, retrieval, feedback, experiment
│   ├── models/          Pydantic contracts (messages, tasks, tools, workflows, approvals)
│   ├── persistence/     SQLite repository (tasks, tool_calls, artifacts, HITL, reconciliation)
│   ├── evaluation/      workflow metrics including HITL and tool usage
│   ├── config/          centralized environment settings
│   ├── runtime.py       dependency wiring and public API
│   ├── cli.py           CLI and REPL
│   ├── cli_memory.py    memory list/search/stats/clear/experiment
│   └── cli_benchmark.py benchmark CLI commands
├── benchmarks/
│   ├── dev/             34 internal tasks + reproducible generator
│   ├── live/            9 live-acceptance + 2 memory tasks, generators
│   ├── performance/     before/after microbenchmarks
│   └── runs/            compact batch records (workspaces/DBs ignored)
├── tests/               259 collected: unit, integration, protocol, HITL, benchmark, provider, CLI, Docker
├── examples/            mock/file-tool/memory/Phase-4 HITL demos, real-provider example
├── docs/                architecture, plan, Phase 1–5 reports, tools/sandbox/memory/HITL/benchmark
├── data/                runtime data (ignored): DB, JSONL, workspaces, artifacts
├── .env.example
├── pyproject.toml
└── README.md
```

---

## Persistence and observability

SQLite tables: `conversations`, `messages`, `tasks`, `agent_executions`, `workflows`,
`memory_entries`, `tool_calls`, `artifacts`, `approvals`, `clarifications`,
`workflow_events` and `tool_reconciliations` (schema version 4). Benchmark batches
add compact JSONL/JSON/CSV records outside the runtime database.

Each tool call records: agent, workflow, task, tool name, arguments, status, output,
error, duration, permission decision, approval status/id, permissions, timestamps
and metadata. Approval rows retain the reviewed arguments, digest, risk, expiry,
decision, comment and one-time consumption time. Clarification rows retain the
question, context, interpretations and answer. Reconciliation rows retain the
high-risk intent/audit state. JSONL events mirror lifecycle, HITL and tool activity.

Metrics include completion rate, workflow/task duration, LLM calls, tokens, tool
calls/failures/time, delegation count/depth/success, reassignments, approval
outcomes and wait time, clarification count/wait time, and pause/resume/cancel
counts. Benchmark metrics add verified goal success, active vs simulated human
wait, token/tool budgets, retries, memory retrieval/use and live-behavior rates.

---

## What is implemented vs. not

Implemented:

- Human↔Agent private chat, Human→Group goals, Agent→Agent, Agent→Group.
- LLM-generated task DAG, capability assignment, bounded delegation, rejection and reassignment.
- Tool calls through a unified dispatcher with real results and iterative reasoning.
- Private file tools, artifacts, Docker sandbox, Python execution (Docker-gated), search providers, local Git tools.
- Persisted, argument-bound approvals with expiry and one-time consumption.
- Persisted clarifications with `OPEN/ANSWERED/EXPIRED/CANCELLED` lifecycle.
- Workflow pause/resume/cancel with real control checks, restart recovery and Docker cleanup.
- Deterministic task replay and a successful-effect replay cache to prevent duplicate side effects.
- Structured tool evidence from the SQLite audit is propagated into synthesis, review and final report prompts.
- Task-scoped memory retrieval and feedback (workflow/task/execution IDs).
- 34-task objective benchmark, resumable runner, JSON/JSONL/CSV/Markdown exports.
- A/B/C ablation, failure taxonomy, staged live-acceptance framework and 9 live tasks.
- Provider hardening: full-endpoint URLs, content-part responses, usage/auth/rate-limit/timeout handling, key redaction.
- Bounded structured-output repair, per-task token limit, workflow LLM-call limit and repeated-action guard.
- Staged live acceptance with cumulative request/token budgets and explicit authorization.
- Live memory learning/evaluation protocol with retrieval/applied IDs.
- High-risk execute-vs-audit reconciliation (`NEEDS_RECONCILIATION`), blocking blind replay.
- Fault-injection coverage and measured SQLite/catalog optimizations.
- SQLite + JSONL observability, expanded metrics, CLI/REPL, deterministic demos, 257 passing tests.

Not implemented / pending:

- **Live search** — HTTP provider tested with `httpx.MockTransport`, no live endpoint configured.
- **Real remote Git push** — only a temporary local bare remote was tested; approval prevents unapproved pushes.
- **Authorized live DeepSeek/HKGAI stages** — no API key or paid-call authorization; all live stages are `NOT_VERIFIED`.
- **Real-LLM memory learning and live memory reuse** — NOT VERIFIED; the live memory protocol is implemented but not executed.
- **Real-user HITL** — human benchmark scripts are explicitly simulated.
- **Official course test set** — not received; final evaluation protocol is ready.
- **Reconciliation resolution UI** — unresolved high-risk side effects require manual reconciliation; no resolution workflow yet.
- **Web UI**, preemptive mid-tool interruption, bounded DAG concurrency, custom seccomp/AppArmor profiles, and cost-budget enforcement are future work.

---

## Docker verification

Verified on 2026-11-09 with Docker Desktop 29.9.2:

```powershell
docker info
python -m pytest -m docker -q
# 5 passed in 21.72s

python -m agentforge demo-tools
# exit 0; real stdout:
# Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]
# Final report: Exit code: `0`; sandbox container recorded; stderr: (empty)

docker ps -a
# no AgentForge containers left
```

The Docker tests cover sandbox start/stop, non-root UID, blocked network, per-agent workspace isolation, configured CPU/memory/PID/read-only limits, and real prime-script execution. The full Phase 6A suite is `300 passed, 2 skipped`; the only skips are the Windows symlink-privilege test and the Docker-missing failure-path test (skipped because Docker was available). Benchmark Python tasks additionally execute their generated code and independent verifier tests in Docker.

The final report is grounded in the persisted `tool_calls` audit: stdout, stderr, exit code, duration, script path and sandbox container are forwarded as `[TOOL_EVIDENCE]` to synthesis/review/finalize. Mock content is still labelled, but it now quotes recorded facts rather than fixed placeholders.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Docker prerequisite missing` | Install/start Docker Desktop with WSL2; verify `docker info`. |
| `Docker sandbox unavailable` in a tool result | Same; Python tools never fall back to host execution. |
| `tool permission denied` | The agent role lacks the tool permission; see `agentforge tools`. |
| `pending_approval` / workflow paused | Inspect with `agentforge approvals list`, decide it, then `agentforge workflows resume <id>`. |
| `approval ... still pending` on resume | Approve or reject the request first; resume cannot bypass a pending decision. |
| `approval has expired` | The TTL elapsed; the tool was not executed. Resume will ask for a fresh approval if the model retries. |
| Cancelled workflow will not resume | Cancellation is terminal; start a new goal instead. |
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
- [docs/HITL_ARCHITECTURE.md](docs/HITL_ARCHITECTURE.md)
- [docs/APPROVAL_SYSTEM.md](docs/APPROVAL_SYSTEM.md)
- [docs/WORKFLOW_LIFECYCLE.md](docs/WORKFLOW_LIFECYCLE.md)
- [docs/MEMORY_SYSTEM.md](docs/MEMORY_SYSTEM.md)
- [docs/BENCHMARK_DESIGN.md](docs/BENCHMARK_DESIGN.md)
- [docs/EVALUATION_METRICS.md](docs/EVALUATION_METRICS.md)
- [docs/ABLATION_STUDY.md](docs/ABLATION_STUDY.md)
- [docs/FAILURE_ANALYSIS.md](docs/FAILURE_ANALYSIS.md)
- [docs/PHASE1_REPORT.md](docs/PHASE1_REPORT.md)
- [docs/PHASE2_REPORT.md](docs/PHASE2_REPORT.md)
- [docs/PHASE3_REPORT.md](docs/PHASE3_REPORT.md)
- [docs/PHASE4_REPORT.md](docs/PHASE4_REPORT.md)
- [docs/PHASE5_REPORT.md](docs/PHASE5_REPORT.md)
- [docs/REAL_LLM_ACCEPTANCE.md](docs/REAL_LLM_ACCEPTANCE.md)
- [docs/HKGAI_ACCEPTANCE_RUN.md](docs/HKGAI_ACCEPTANCE_RUN.md)
- [docs/HKGAI_STREAMING_DIAGNOSIS.md](docs/HKGAI_STREAMING_DIAGNOSIS.md)
- [docs/HKGAI_CONTROL_EXPERIMENTS.md](docs/HKGAI_CONTROL_EXPERIMENTS.md)
- [docs/HKGAI_TECHNICAL_SUPPORT_REPORT_ZH.md](docs/HKGAI_TECHNICAL_SUPPORT_REPORT_ZH.md)
- [docs/PHASE6A_REPORT.md](docs/PHASE6A_REPORT.md)
- [docs/FINAL_EVALUATION_PROTOCOL.md](docs/FINAL_EVALUATION_PROTOCOL.md)
- [docs/FINAL_SUBMISSION_CHECKLIST.md](docs/FINAL_SUBMISSION_CHECKLIST.md)
- [docs/DEVELOPMENT_PLAN.md](docs/DEVELOPMENT_PLAN.md)

## License

MIT (course project).
