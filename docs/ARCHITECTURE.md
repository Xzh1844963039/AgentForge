# AgentForge Architecture (Phase 1–4)

## 1. Scope

Phase 1 provides the multi-agent runtime; Phase 2 adds real tool execution and Docker
sandbox isolation; Phase 3 adds evidence-first memory evolution, retrieval and feedback;
Phase 4 adds persistent approvals/clarifications and workflow lifecycle control.
The critical path is complete:

- humans can talk privately to agents and publish goals to a group,
- a Coordinator dynamically plans a task graph with the configured LLM,
- tasks are assigned by capability and executed through real message routing,
- agents can delegate work within hard budgets,
- agents can request real tools (files, sandboxed Python, search, local Git) through a
  unified dispatcher and reason over the structured results,
- every tool call is schema-validated, permission-checked, timed out and audited,
- memories are derived from the tool audit with verification levels and provenance,
- retrieval feeds agents, changes actions, and receives task-scoped outcome feedback,
- destructive actions pause the real workflow until a parameter-bound human decision,
- clarifications pause execution and resume with the persisted human answer,
- workflows can be paused, resumed after restart and cancelled with cleanup,
- results are synthesised, independently reviewed, and finalised,
- every message, task transition, LLM call, tool call, HITL decision, memory event and
  metric is persisted.

Phase 2 Docker sandbox verification: Docker Desktop 29.9.2, 4/4 integration tests,
real prime-script execution. Phase 3 memory evolution and Phase 4 HITL are implemented
and their pipelines are verified with Mock LLM decisions + real tools/state; real-LLM
learning and HITL behavior are NOT VERIFIED. A web UI remains future work.

## 2. Layer diagram

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ CLI / Runtime API                                                        │
│  demo | demo-tools | run | chat | repl | tools | tool-calls | metrics    │
│  approvals | clarification | workflows | memory                          │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────┐
│ AgentForgeRuntime                                                        │
│ Settings → Provider → Store → Events → Memory → Workspaces → Sandboxes   │
│ → Tools → ToolExecutor → Agents → Bus → HITL services → Orchestrator     │
└───────────────┬───────────────────────────────┬──────────────────────────┘
                │                               │
┌───────────────▼──────────────┐   ┌────────────▼─────────────────────────┐
│ TaskOrchestrator             │   │ MessageBus                           │
│  plan validation             │   │  direct / group / task / result      │
│  capability assignment       │   │  inbox delivery + replay             │
│  DAG execution               │   └────────────┬─────────────────────────┘
│  delegation/reassignment     │                │
│  pause/resume/cancel         │   ┌────────────▼─────────────────────────┐
│  synthesis/review/finalise   │   │ Agents                               │
│  metrics + audit             │   │  Coordinator / Research /            │
└───────────────┬──────────────┘   │  Coding / Reviewer                   │
                │                  │  complete | delegate | tool_call     │
                │                  │  clarify | reject_assignment        │
                │                  └────────────┬─────────────────────────┘
                │                               │
┌───────────────▼───────────────────────────────▼─────────────────────────┐
│ ToolExecutor (permissions, schemas, approvals, replay, timeout, audit)  │
│  File/Artifact │ Python │ Search │ Git │ ApprovalService                 │
├─────────────────────────────────────────────────────────────────────────┤
│ SandboxManager → DockerSandbox / private workspace per agent            │
├─────────────────────────────────────────────────────────────────────────┤
│ ClarificationService │ WorkflowControlService │ InteractionRepository    │
├─────────────────────────────────────────────────────────────────────────┤
│ LLM providers (DeepSeek / HKGAI / Mock) │ MemoryEvolutionEngine        │
│ EvolutionMemoryStore (separate DB) │ SQLite audit │ JSONL              │
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
| `persistence/` | Thread-safe SQLite repository (including approvals, clarifications, workflow events) and append-only JSONL event logger. |
| `interaction/` | Persisted approval and clarification services plus the interaction repository. |
| `memory/` | Per-agent memory interface; evidence-first evolution, provenance/verification, embeddings, task-scoped retrieval, candidates, decay, feedback and controlled experiment. |
| `messaging/` | Conversation lifecycle and durable message routing/broadcasting. |
| `agents/` | `BaseAgent` (inbox, per-agent task-state mirror, memory/tool interfaces, suspension), four roles, profile defaults, registry, prompt/action protocol. |
| `orchestration/` | Plan validation, capability matching, task state machine, DAG executor, budgets, pause/resume/cancel, reassignment, metrics integration. |
| `evaluation/` | Completion rate, timing, token, tool, delegation, HITL and depth metrics. |
| `tools/` | Tool contracts, registry, permissions, approval token binding, replay cache, dispatcher, private workspaces, file/artifact/Python/search/Git tools. |
| `sandbox/` | Docker sandbox, CLI runner, lifecycle manager, resource limits. |
| `runtime.py` | Assembly of provider, store, HITL services, tools, sandboxes, agents, messaging and orchestrator. |
| `cli.py` | Human interface, formatted output, tool/HITL audit views, REPL. |

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

Statuses: `PENDING`, `RUNNING`, `COMPLETED`, `FAILED`, `BLOCKED`, `CANCELLED`.

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

### 5.5 Task and workflow state machines

```text
PENDING ──► RUNNING ──► COMPLETED
   │           │  │
   │           │  └──► BLOCKED ──► RUNNING (delegation resumed)
   │           │              └──► FAILED
   │           └──► FAILED
   ├──► BLOCKED
   ├──► FAILED
   └──► CANCELLED (any non-terminal state)
```

`COMPLETED`, `FAILED` and `CANCELLED` are terminal. A suspended task returns to
`PENDING` for deterministic replay. Every transition is validated; illegal
transitions raise `InvalidTaskTransition`.

Workflow statuses are `PENDING`, `RUNNING`, `PAUSED`, `BLOCKED`, `COMPLETED`,
`FAILED`, `CANCELLED`. Pausing preserves completed tasks and persists the workflow;
resuming validates human decisions first and recovers tasks left `RUNNING` by a
crash. Cancellation is terminal and cleans up tasks, approvals, clarifications and
sandboxes. See `docs/WORKFLOW_LIFECYCLE.md`.

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
  Successful rows are also the replay cache for resumed tasks.
- `artifacts` — controlled cross-agent artifact metadata and hash.
- `approvals` — exact arguments, canonical digest, workflow/task/agent scope, risk,
  decision, expiry, consumption and comments.
- `clarifications` — question, context, interpretations, status, answer and timestamps.
- `workflow_events` — append-only lifecycle stream used for metrics and CLI.

The separate memory database stores retrieval events and feedback with
`workflow_id`, `task_id` and `execution_id`.

`data/events.jsonl` is an append-only audit stream (`workflow_started`,
`planning_attempt`, `goal_published`, `task_assigned`, `task_started`,
`task_completed`, `task_failed`, `task_delegated`, `agent_execution`, `tool_call`,
`tool_approval`, `workflow_completed`, `workflow_failed`, `message_sent`,
`approval_requested`, `approval_decided`, `approval_consumed`,
`clarification_requested`, `clarification_answered`, `workflow_paused`,
`workflow_resumed`, `workflow_cancelled`, `task_reassigned`, ...).

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
- **Delegation count**, **max depth**, delegated completion/success and reassignments.
- **Tool calls**, **tool failures**, and **summed tool time**.
- **Approval requests/outcomes/rate** and average human wait time.
- **Clarifications/answered** and average human wait time.
- **Pause/resume/cancellation counts** from persisted workflow events.
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

## 10. Memory evolution (Phase 3)

Memory is agent-scoped and namespace-scoped; queries always filter by `agent_id` and
optionally namespace. The original `MemoryStore` interface is preserved, so agents do
not need a Phase 3-specific API.

Evidence flow:

```text
SQLite tool_calls audit → ToolEvidence
  → deterministic reflection
  → episodic / semantic / working memories
  → procedural candidate (tool sequence + argument hints)
  → promotion only for REAL_EXECUTION with ≥2 distinct workflows and confidence ≥0.8
  → EvolutionMemoryStore.recall (embedding + keyword + decay + verification)
  → retrieval event stored with workflow/task/execution IDs
  → [RELEVANT_MEMORY] in agent prompt
  → real tool action → scoped retrieval feedback → confidence update
```

Phase 4 task-scoped feedback replaces the Phase 3 time-window join: retrieval events
and feedback rows carry exact `workflow_id`, `task_id` and `execution_id`, so
concurrent workflows and cancelled workflows cannot cross-contaminate confidence.
The legacy time-window fallback applies only to old unscoped rows.

Verification levels:

- `REAL_EXECUTION` — all referenced tool calls succeeded and came from real tools, with
  persisted tool-call IDs;
- `MOCK_SIMULATION` — at least one mock/simulated tool or synthetic evidence; stored
  as provisional and excluded from default recall;
- `UNVERIFIED` — missing/failed/incomplete audit or legacy chat/task memory.

`llm_is_mock` is recorded separately from `tool_execution`: a deterministic Mock LLM
decision can coexist with real file/Python execution evidence.

Storage is a separate `memory_evolution.db`; Phase 2 tool/sandbox/orchestrator code is
unchanged. Runtime adds the store/engine and a post-workflow hook. Retrieval uses a
deterministic local hashing embedding (learned-embedding quality NOT VERIFIED), caps
prompt characters, labels verification, and records retrieval events. Feedback matches
task tool calls to retrieved procedures and adjusts confidence. Decay, consolidation,
working-memory TTL and forgetting are implemented.

A controlled experiment compares the same task/model/tools/budget with and without
memory: the promoted procedure changes `write_file` from `notes/default.md` to
`notes/verified.md`. This verifies the pipeline with Mock LLM decisions; real-LLM
learning performance is NOT VERIFIED.

## 11. Tool execution architecture

Agents may choose tools, but only `ToolExecutor` executes them. A tool call is a
structured action (`action=tool_call`) with a name and arguments; it is never a free
text command.

Dispatcher pipeline:

1. tool lookup;
2. per-agent permission policy (default deny);
3. JSON-Schema argument validation;
4. successful-effect replay lookup for resumed tasks;
5. approval binding/expiry/one-time consumption, or persist a pending request and
   suspend the task/workflow;
6. timeout (`asyncio.wait_for`);
7. tool execution with validated Pydantic arguments;
8. structured exception handling;
9. output schema validation;
10. output size cap;
11. SQLite `tool_calls` + JSONL audit.

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

## 15. Human-in-the-loop architecture

Phase 4 replaces the placeholder `PendingApprovalGate` with a persistent
`ApprovalService` and adds `ClarificationService` and `WorkflowControlService`.

```text
Tool needs approval
  → ToolExecutor persists ApprovalRequest (tool + arguments + SHA-256 + scope)
  → raises ApprovalRequired
  → BaseAgent raises AgentSuspension("approval")
  → task → PENDING, workflow → PAUSED, agent active task cleared
  → human decides through CLI/REPL
  → workflows resume
  → ToolExecutor validates digest/scope/expiry, consumes token once, executes
```

Clarification and human pause share the suspension path. Cancellation marks
unfinished tasks `CANCELLED`, closes pending approvals/clarifications, stops the
workflow's sandboxes and is terminal.

Resume replays the task from persisted state, not a raw conversation checkpoint.
Every attempt receives a new `execution_id`; `ToolExecutor` returns previously
successful tool outputs for identical `(workflow, task, tool, argument_digest)` calls
with `replayed=True`, so completed side effects are not repeated. Tasks left
`RUNNING` by a crash are reset to `PENDING` and marked `recovered_from_running`.

Approval enforcement is in `ToolExecutor`/`ApprovalGate`, never in prompts.
Permissions are checked first and an approval cannot grant a missing permission or a
different argument set. Details: `docs/HITL_ARCHITECTURE.md`,
`docs/APPROVAL_SYSTEM.md`, `docs/WORKFLOW_LIFECYCLE.md`.

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
5. **Memory work:** add a learned embedding provider and fuzzy procedure transfer;
   keep provenance/verification and exact task-scoped feedback rules.
6. **HITL extensions:** add richer approval policies, delegated approvers or a UI on
   top of `ApprovalService`/`ClarificationService`; keep argument binding and the
   final `ToolExecutor` check intact.
7. **Concurrency:** execute independent ready tasks and independent tool calls with
   `asyncio.gather` while preserving state-machine, replay and audit semantics.
8. **Evaluation:** add real-API, Docker, and live-search acceptance runs with clear
   PASS/SKIPPED/NOT VERIFIED reporting.
