# AgentForge Architecture (Phase 1–2)

## 1. Scope

Phase 1 provides the multi-agent runtime; Phase 2 adds real tool execution and Docker
sandbox isolation. The critical path is complete:

- humans can talk privately to agents and publish goals to a group,
- a Coordinator dynamically plans a task graph with the configured LLM,
- tasks are assigned by capability and executed through real message routing,
- agents can delegate work within hard budgets,
- agents can request real tools (files, sandboxed Python, search, local Git) through a
  unified dispatcher and reason over the structured results,
- every tool call is schema-validated, permission-checked, timed out and audited,
- results are synthesised, independently reviewed, and finalised,
- every message, task transition, LLM call, tool call, and metric is persisted.

Phase 2 provides the Docker sandbox implementation and it is verified on this machine
(Docker Desktop 29.9.2; 4/4 integration tests; real prime-script execution).
Full HITL, memory evolution, and a web UI remain future phases; interfaces and TODO
markers are explicit.

## 2. Layer diagram

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ CLI / Runtime API                                                        │
│  demo | demo-tools | run | chat | repl | tools | tool-calls | metrics    │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────┐
│ AgentForgeRuntime                                                        │
│ Settings → Provider → Store → Events → Memory → Workspaces → Sandboxes   │
│ → Tools → ToolExecutor → Agents → Bus → Orchestrator                     │
└───────────────┬───────────────────────────────┬──────────────────────────┘
                │                               │
┌───────────────▼──────────────┐   ┌────────────▼─────────────────────────┐
│ TaskOrchestrator             │   │ MessageBus                           │
│  plan validation             │   │  direct / group / task / result      │
│  capability assignment       │   │  inbox delivery + replay             │
│  DAG execution               │   └────────────┬─────────────────────────┘
│  bounded delegation          │                │
│  synthesis/review/finalise   │   ┌────────────▼─────────────────────────┐
│  metrics + audit             │   │ Agents                               │
└───────────────┬──────────────┘   │  Coordinator / Research /            │
                │                  │  Coding / Reviewer                   │
                │                  │  complete | delegate | tool_call     │
                │                  └────────────┬─────────────────────────┘
                │                               │
┌───────────────▼───────────────────────────────▼─────────────────────────┐
│ ToolExecutor (permissions, schemas, approvals, timeout, audit)          │
│  File/Artifact │ Python │ Search │ Git │ ApprovalGate                   │
├─────────────────────────────────────────────────────────────────────────┤
│ SandboxManager → DockerSandbox / private workspace per agent            │
├─────────────────────────────────────────────────────────────────────────┤
│ LLM providers (DeepSeek / HKGAI / Mock) │ Memory │ SQLite │ JSONL       │
└─────────────────────────────────────────────────────────────────────────┘
```

Dependency rule: models do not depend on infrastructure. Agents depend on provider,
memory, and tool interfaces. The orchestrator depends on agent and messaging
abstractions. Only `runtime.py` wires concrete implementations together.

## 3. Package responsibilities

| Package | Responsibility |
|---|---|
| `models/` | Pydantic v2 contracts: messages, conversations, tasks, plans, actions, executions, workflows, agents. |
| `config/` | `Settings` from environment/`.env`; safety limits; path resolution. |
| `llm/` | `BaseLLMProvider`; async DeepSeek and HKGAI clients over an OpenAI-compatible protocol; deterministic mock; factory. |
| `persistence/` | Thread-safe SQLite repository and append-only JSONL event logger. |
| `memory/` | Per-agent memory interface; in-memory and SQLite implementations; keyword+recency recall. |
| `messaging/` | Conversation lifecycle and durable message routing/broadcasting. |
| `agents/` | `BaseAgent` (inbox, per-agent task-state mirror, memory/tool interfaces), four roles, profile defaults, registry, prompt/action protocol. |
| `orchestration/` | Plan validation, capability matching, task state machine, DAG executor, budgets, metrics integration. |
| `evaluation/` | Completion rate, timing, token, tool, delegation and depth metrics. |
| `tools/` | Tool contracts, registry, permissions, approvals, dispatcher, private workspaces, file/artifact/Python/search/Git tools. |
| `sandbox/` | Docker sandbox, CLI runner, lifecycle manager, resource limits. |
| `runtime.py` | Assembly of provider, store, tools, sandboxes, agents, messaging and orchestrator. |
| `cli.py` | Human interface, formatted output, tool audit view, REPL. |

## 4. Domain contracts

### Message

| Field | Meaning |
|---|---|
| `message_id` | Globally unique ID |
| `conversation_id` | Direct or group conversation |
| `sender` / `receiver` | `human`, `system`, or agent ID; group receiver is the conversation ID |
| `message_type` | direct, group, task_request, task_result, delegation, plan, system, error |
| `content` | Non-empty text |
| `timestamp` | UTC |
| `correlation_id` | Workflow ID for traceability |
| `task_id` | Optional task linkage |
| `in_reply_to` | Optional reply linkage |
| `metadata` | Extensible structured context |

### Task

Required fields: `task_id`, `workflow_id`, `description`, `required_capabilities`,
`assigned_agent`, `dependencies`, `status`, `result`, `error`, timestamps, `depth`,
and free-form metadata (`kind`, `origin`, assignment rationale, delegation state).

Statuses: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `BLOCKED`.

### Execution plan

The Coordinator returns:

```json
{
  "goal_summary": "one sentence",
  "subtasks": [
    {
      "key": "unique key",
      "description": "self-contained work item",
      "required_capabilities": ["research"],
      "depends_on": ["another key"],
      "kind": "research|coding|analysis|writing|generic"
    }
  ]
}
```

The model is validated with Pydantic, then the DAG is validated for unknown
dependencies, self-dependencies, cycles, duplicate keys, and size limits. Invalid
output triggers one repair prompt. A second failure fails the workflow; there is no
hard-coded fallback plan in real-API mode.

### Agent action

```json
{"action": "complete", "content": "...", "reasoning": "..."}
{"action": "tool_call", "tool_calls": [
  {"name": "python_execute", "arguments": {"code": "...", "filename": "primes.py"}}],
 "reasoning": "..."}
{"action": "delegate", "content": "...", "delegation": {"reason": "...",
  "subtasks": [{"description": "...", "required_capabilities": ["research"]}]}}
{"action": "fail", "error": "...", "content": "..."}
```

Plain-text responses are accepted as `complete` with a warning. Malformed JSON that
claims to be an action is rejected, so delegation or tool use cannot be silently
downgraded.

## 5. Orchestration lifecycle

### 5.1 Group goal

```text
Human
  │ publish goal
  ▼
Group conversation (durable GROUP message)
  │
  ▼
Coordinator plan_goal(goal, live agent cards)
  │ LLM call(s), JSON parse, DAG validation, one repair attempt
  ▼
CapabilityAssigner (full coverage required)
  │
  ▼
Task DAG: planned tasks + synthesis + optional review + finalisation
  │
  ├─ TASK_REQUEST (coordinator → agent, direct)
  ├─ agent LLM execution with dependency results + private memory
  │    ├─ structured tool_call → ToolExecutor (permissions/schema/approval/timeout)
  │    ├─ [TOOL_RESULTS] returned to agent → next LLM turn
  │    └─ loop until complete / delegate / fail (bounded)
  ├─ TASK_RESULT (agent → coordinator, direct)
  ├─ optional DELEGATION (agent → group; child tasks created)
  ├─ BLOCKED parent resumes after children
  │
  ▼
Coordinator synthesis → Reviewer audit → Coordinator final report
  │
  ▼
Group TASK_RESULT (final report) + workflow metrics + audit records
```

### 5.2 Private chat

```text
Human → direct conversation → agent inbox
     → role prompt + recent transcript + memory → LLM
     → direct reply persisted and shown to the human
```

Private chat is not task execution and does not create a workflow. It still records
an `agent_executions` row (`purpose=chat:<role>`) and a conversation memory entry.

### 5.3 Governance tasks

After the LLM-generated plan is validated, the platform appends:

1. **synthesis** — assigned to the agent covering `synthesis` (default Coordinator),
   depending on all planned tasks.
2. **review** — assigned to the agent covering `review` (default Reviewer), depending
   on the synthesis plus all planned tasks, so it sees raw results and the draft.
3. **finalize** — assigned to the agent covering `synthesis`, depending on the
   synthesis and review.

These are explicit platform policies, not a disguised fixed task decomposition. The
decomposition itself is LLM-generated. `AGENTFORGE_ENABLE_FINAL_REVIEW=false` removes
the review task and links finalisation directly to synthesis.

### 5.4 Execution algorithm

```text
task_map = root + planned + governance tasks
while work tasks are not all terminal:
    resume BLOCKED parents whose delegated children are all terminal
        - all children completed → re-run parent with child results
        - any child failed       → fail parent
    for each PENDING task:
        - any dependency failed → fail task ("dependency failed")
        - all dependencies completed → execute task
    if no progress → fail workflow (deadlock guard)
```

Execution is sequential in the current implementation for determinism and clear tracing. The graph is
independent of the executor, so parallel ready-task execution can be added later
without changing the domain model.

### 5.5 Task state machine

```text
PENDING ──► RUNNING ──► COMPLETED
   │           │  │
   │           │  └──► BLOCKED ──► RUNNING (delegation resumed)
   │           │              └──► FAILED
   │           └──► FAILED
   └──► BLOCKED
   └──► FAILED
```

`COMPLETED` and `FAILED` are terminal. Every transition is validated; illegal
transitions raise `InvalidTaskTransition`.

### 5.6 Budgets and loop prevention

| Control | Default | Enforced by |
|---|---|---|
| Max planned subtasks | 8 | Plan validation |
| Max delegation depth | 3 | Delegation handler before child creation |
| Max delegation rounds per task | 2 | Parent task metadata |
| Max total tasks per workflow | 60 | Plan materialisation and delegation |
| Max execution steps (task LLM runs) | 30 | Executor before each task attempt |
| Max execution time | — | Reserved for Phase 6 (token/wall-clock budget) |

A blocked parent is resumed only when all children are terminal, which prevents
self-referential waiting. Cycles are rejected before execution. The no-progress
guard catches any unanticipated deadlock.

## 6. Messaging routing

| Message | Conversation | Delivery |
|---|---|---|
| Human → Agent | direct | Agent inbox |
| Human → Group | group | All agent participants |
| Agent → Agent | direct | Target agent inbox |
| Agent → Group | group | All agent participants except sender |
| System → Group | group | All agent participants |
| Agent → Human | direct | Persisted; no in-process inbox |

Every message is written to SQLite before delivery. Agents de-duplicate by
`message_id`, and task execution consumes the `TASK_REQUEST` matching its `task_id`,
so group notices in the inbox do not interfere with task routing. `messages` /
`messages --all` / `messages --conversation` provide ordered replay.

## 7. Persistence

SQLite (WAL mode, thread-safe repository) stores:

- `conversations` — type, title, participants, metadata.
- `messages` — full message envelope, content, correlation/task IDs, metadata.
- `tasks` — description, capabilities, assignment, dependencies, status, result,
  error, depth, timestamps, metadata.
- `agent_executions` — one row per LLM invocation: workflow/task/agent, purpose,
  provider/model, start/end, status, error, tokens, latency, metadata.
- `workflows` — goal, conversation, status, final result, error, timestamps, metrics.
- `memory_entries` — agent-scoped namespace, kind, content, importance, metadata.
- `tool_calls` — one row per dispatcher invocation: tool, arguments, status, output,
  error, duration, permission decision, approval status/id, permissions, timestamps.
- `artifacts` — controlled cross-agent artifact metadata and hash.

`data/events.jsonl` is an append-only audit stream (`workflow_started`,
`planning_attempt`, `goal_published`, `task_assigned`, `task_started`,
`task_completed`, `task_failed`, `task_delegated`, `agent_execution`, `tool_call`,
`tool_approval`, `workflow_completed`, `workflow_failed`, `message_sent`, ...).

The database is local and disposable; `data/` is Git-ignored. No API keys are
persisted.

## 8. Metrics

Computed from durable records at workflow end:

- **Task completion rate** = completed substantive tasks / total substantive tasks.
  The synthetic root task is excluded.
- **Execution time** = workflow wall-clock duration and the sum of task durations.
- **LLM calls** = planning attempts + all task-loop turns (private chats are recorded
  but not part of a workflow).
- **Prompt/completion tokens** = sum from provider usage.
- **Delegation count** and **max depth**.
- **Tool calls**, **tool failures**, and **summed tool time**.
- **Failed/blocked counts** and error reasons.

Mock timings are real measured durations but are tiny; they validate instrumentation,
not model latency.

## 9. LLM provider layer

`BaseLLMProvider.complete(messages, purpose=...)` returns a normalized
`LLMResponse` with content, provider, model, usage, latency, finish reason, and safe
raw metadata.

- **DeepSeek**: async HTTP `/chat/completions`, bearer auth, configurable base URL,
  model, temperature, max tokens, timeout, retries. Retryable statuses: 408/409/429/
  500/502/503/504 plus transport/timeouts, exponential backoff (0.5s → 4s).
- **HKGAI**: same protocol; base URL and model are mandatory and must be supplied by
  the course. Not validated against a live endpoint yet.
- **Mock**: purpose-driven deterministic fixtures (`plan`, `execute:<kind>:<role>`,
  `chat:<role>`), accurate call records, optional scripted responses, optional
  latency. It is explicitly labelled as non-evaluable output.

Provider construction validates credentials. Selecting a real provider without a key
raises `ProviderConfigurationError`; the runtime never silently switches to mock.

## 10. Memory design

Per-agent isolation is enforced in two dimensions: `agent_id` and `namespace`.
Current memory namespaces:

- `experience` — one compact memory per task outcome, including kind/action/result.
- `conversation` — private chat summaries.

Recall combines token overlap with recency/importance. Every agent receives only its
own memories in its prompt.

Phase 3 extension points (already isolated behind `MemoryStore`):

- reflection summaries and strategy extraction,
- importance scoring and decay,
- forgetting/consolidation,
- embedding-based semantic retrieval,
- procedural memory reuse across workflows,
- measurable memory-evolution experiments.

## 11. Tool execution architecture

Agents may choose tools, but only `ToolExecutor` executes them. A tool call is a
structured action (`action=tool_call`) with a name and arguments; it is never a free
text command.

Dispatcher pipeline:

1. tool lookup;
2. per-agent permission policy (default deny);
3. JSON-Schema argument validation;
4. approval gate for destructive permissions;
5. timeout (`asyncio.wait_for`);
6. tool execution with validated Pydantic arguments;
7. structured exception handling;
8. output schema validation;
9. output size cap;
10. SQLite `tool_calls` + JSONL audit.

The result (success, error, timeout, denied, pending approval) is returned to the
agent as `[TOOL_RESULTS]`, marked untrusted, and the agent continues reasoning. Tool
calls, LLM turns and task results are all separately persisted; tool-loop token and
latency costs appear in workflow metrics.

For synthesis, review and finalisation, the orchestrator builds `ToolEvidence` from
the persisted `tool_calls` audit of the task's transitive dependency/delegation
closure. It is injected as structured `[TOOL_EVIDENCE]` into the agent prompt. This is
the authoritative source for stdout, stderr, exit code, success, duration and sandbox
metadata; Mock reports and real LLM reports both consume it instead of scraping tool
prose. A task also stores `tool_call_ids` in its metadata for traceability.

`ToolSpec` declares input/output schemas, timeout, permissions, approval and
untrusted-output flags. Default permissions are role-specific; `file_delete` and
`git_push` always require approval. See `docs/TOOL_SYSTEM.md`.

## 12. Private workspaces and artifact transfer

Each agent has `<workspace_root>/<agent_id>/`. Public file tools only accept `.md` and
`.txt`. `WorkspaceManager` rejects absolute paths, drive letters, `..`, control
characters, Windows reserved device names, trailing spaces/dots, and any symlink
component; the resolved path must remain under the agent root. Writes are atomic
(temp + `os.replace`), size-capped, and audited.

There is no file tool capable of reading another agent's workspace. Cross-agent data
moves only through `share_artifact` (copy to a central immutable store with SHA-256)
and `fetch_artifact` (hash-verified copy into the caller's own workspace). The
isolation tests cover direct traversal rejection and the controlled share/fetch path.

## 13. Docker sandbox

`DockerSandbox` runs one container per agent with only that agent's workspace mounted
at `/workspace`:

- non-root user (`65534:65534`); root configuration is rejected;
- `--network none` by default;
- CPU, memory/swap, PID and wall-clock limits;
- read-only container root + noexec/nosuid `/tmp` tmpfs;
- `--cap-drop ALL` and `no-new-privileges`;
- no Docker socket, no privileged mode, no host environment/API keys;
- stale-container cleanup; container removal on timeout to prevent orphans;
- `SandboxManager` closes all running sandboxes at runtime shutdown.

`python_execute`/`python_test` stage `.py` files in the private workspace and use only
the Docker sandbox. If Docker is missing, they return a structured
`Docker sandbox unavailable` error. There is no host subprocess fallback.

Docker integration was verified on 2026-11-08 with Docker Desktop 29.9.2: 4/4 Docker
tests passed, `demo-tools` executed the prime script and returned the recorded stdout
and exit code, and `docker ps -a` showed no leftover containers. See
`docs/SANDBOX_SECURITY.md` for the threat model, flags, verification commands and
residual risks.

## 14. Search and Git tools

Search uses a `SearchProvider` interface:

- `MockSearchProvider` is deterministic, offline, explicitly labelled `mock: true`,
  and uses reserved `.invalid` URLs;
- `HttpSearchProvider` calls a configurable JSON endpoint with retries and timeouts.

`fetch_webpage` validates scheme, hostname, credentials, port and DNS answers, blocks
localhost/private/special-use/metadata addresses, validates every redirect, caps
redirects and response bytes, and allows only text/JSON/XML content types. Web content
is marked untrusted and never treated as instructions.

Git tools are registered only when `AGENTFORGE_GIT_REPO_ROOT` is configured. They use
`git -C <root>` with argument lists, never a shell; add/commit require explicit paths
inside the repository; commit is scoped to those paths. `git_push` is always
approval-gated and pending by default. The Git environment is scrubbed of tokens,
secrets, keys and SSH agent variables. Tests use only a temporary local bare remote.

## 15. Human-in-the-loop boundary

Phase 2 introduces an `ApprovalGate` interface used by destructive tools. The default
`PendingApprovalGate` returns `PENDING_APPROVAL` without executing the action; tests
use explicit approved/denied gates. There is no interactive approval channel,
pause/resume workflow, clarification loop or permission-scope UI yet. Those belong to
Phase 4; no fake HITL behaviour is implemented.

## 16. Security and safety

- Secrets from environment/`.env`; never logged, persisted, echoed or injected into
  containers.
- No OpenAI/Anthropic endpoints or SDKs.
- Provider errors are sanitized to status code plus truncated response text.
- LLM output is never trusted for control flow: plan structure, dependencies,
  capabilities, delegation limits, tool names, tool arguments, file paths, URLs and
  Git refs are validated by the platform.
- Tool output is untrusted data; prompts state this and tool results are wrapped.
- Every tool call is audited with permission and approval decisions.
- Docker flags enforce isolation and quotas; Docker absence fails closed.
- SQLite has no authentication; it is a local course-project store.

## 17. Why not LangGraph?

LangGraph is allowed by the project brief, but a fixed graph would risk mistaking a
static flow for autonomous decomposition. The executor receives an LLM-generated DAG,
validates it, and executes it with explicit budgets. Phase 2 adds a bounded tool loop
inside each task without changing the orchestration contract. A future
`LangGraphOrchestrator` adapter can implement the same `run_goal` contract while
reusing models, messaging, tools, sandboxing and persistence.

## 18. Extension checklist

1. **New agent:** extend `AgentRole`, add a `BaseAgent` subclass with role
   instructions, register it, and grant capability/permission tokens.
2. **New provider:** implement `BaseLLMProvider`, validate configuration in the
   factory, and test the protocol with `httpx.MockTransport`.
3. **New tool:** define Pydantic args/output models, subclass `BaseTool`, set
   permissions/timeout/approval/untrusted flags, register it, grant permissions, and
   add schema/success/failure/audit tests.
4. **New sandbox backend:** implement `BaseSandbox` with equivalent isolation and
   quotas; keep the no-host-fallback rule.
5. **Memory evolution:** implement a richer `MemoryStore` (reflection, decay,
   consolidation, embeddings) without changing agents.
6. **Full HITL:** connect `ApprovalGate` and a clarification gateway to interactive
   CLI/UI flows and resumable BLOCKED tasks.
7. **Concurrency:** execute independent ready tasks and independent tool calls with
   `asyncio.gather` while preserving state-machine and audit semantics.
8. **Evaluation:** add real-API, Docker, and live-search acceptance runs with clear
   PASS/SKIPPED/NOT VERIFIED reporting.
