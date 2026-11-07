# AgentForge Architecture (Phase 1)

## 1. Scope

Phase 1 provides a production-shaped foundation for a multi-agent collaborative
platform. It is intentionally small but complete along the critical path:

- humans can talk privately to agents and publish goals to a group,
- a Coordinator dynamically plans a task graph with the configured LLM,
- tasks are assigned by capability and executed through real message routing,
- agents can delegate work within hard budgets,
- results are synthesised, independently reviewed, and finalised,
- every message, task transition, execution, and metric is persisted.

Not in scope for Phase 1: Docker isolation, real tools, GitHub/search integrations,
human approval gates, memory evolution, and a web UI. Interfaces and explicit TODOs
mark the extension points.

## 2. Layer diagram

```text
┌─────────────────────────────────────────────────────────────────────┐
│ CLI / Runtime API                                                   │
│  agentforge demo | run | chat | repl | tasks | messages | metrics   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│ AgentForgeRuntime                                                   │
│  Settings → Provider → SQLiteStore → EventLogger → Memory → Tools   │
│  → AgentRegistry → ConversationManager → MessageBus → Orchestrator  │
└───────────────┬───────────────────────────────┬─────────────────────┘
                │                               │
┌───────────────▼──────────────┐   ┌────────────▼─────────────────────┐
│ TaskOrchestrator             │   │ MessageBus                       │
│  plan validation             │   │  direct / group / task / result  │
│  capability assignment       │   │  inbox delivery + replay         │
│  DAG execution               │   └────────────┬─────────────────────┘
│  bounded delegation          │                │
│  synthesis/review/finalise   │   ┌────────────▼─────────────────────┐
│  metrics + audit             │   │ Agents                           │
└───────────────┬──────────────┘   │  Coordinator / Research /        │
                │                  │  Coding / Reviewer               │
                │                  │  structured actions              │
                │                  └────────────┬─────────────────────┘
                │                               │
┌───────────────▼───────────────────────────────▼─────────────────────┐
│ Shared infrastructure                                               │
│  LLM providers (DeepSeek / HKGAI / Mock) │ Memory │ Tools │ Sandbox │
│  SQLite repositories │ JSONL event log │ Pydantic models            │
└─────────────────────────────────────────────────────────────────────┘
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
| `evaluation/` | Completion rate, timing, token, delegation and depth metrics. |
| `tools/` | Tool contract and registry. No production tools in Phase 1. |
| `sandbox/` | Sandbox contract and explicit `DockerSandbox` NotImplemented placeholders. |
| `runtime.py` | Assembly, private chat, group goals, queries, lifecycle. |
| `cli.py` | Human interface, formatted output, REPL. |

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
{"action": "delegate", "content": "...", "delegation": {"reason": "...",
  "subtasks": [{"description": "...", "required_capabilities": ["research"]}]}}
{"action": "fail", "error": "...", "content": "..."}
```

Plain-text responses are accepted as `complete` with a warning. Malformed JSON that
claims to be an action is rejected, so delegation cannot be silently downgraded.

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

Execution is sequential in Phase 1 for determinism and clear tracing. The graph is
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

`data/events.jsonl` is an append-only audit stream (`workflow_started`,
`planning_attempt`, `goal_published`, `task_assigned`, `task_started`,
`task_completed`, `task_failed`, `task_delegated`, `agent_execution`,
`workflow_completed`, `workflow_failed`, `message_sent`, ...).

The database is local and disposable; `data/` is Git-ignored. No API keys are
persisted.

## 8. Metrics

Computed from durable records at workflow end:

- **Task completion rate** = completed substantive tasks / total substantive tasks.
  The synthetic root task is excluded.
- **Execution time** = workflow wall-clock duration and the sum of task durations.
- **LLM calls** = planning attempts + task executions (private chats are recorded but
  not part of a workflow).
- **Prompt/completion tokens** = sum from provider usage.
- **Delegation count** and **max depth**.
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
Phase 1 namespaces:

- `experience` — one compact memory per task outcome, including kind/action/result.
- `conversation` — private chat summaries.

Recall combines token overlap with recency/importance. Every agent receives only its
own memories in its prompt.

Phase 2 extension points (already isolated behind `MemoryStore`):

- reflection summaries and strategy extraction,
- importance scoring and decay,
- forgetting/consolidation,
- embedding-based semantic retrieval,
- procedural memory reuse across workflows,
- measurable memory-evolution experiments.

## 11. Tools and sandbox (explicit TODOs)

`ToolRegistry` and `BaseTool` define name/description/JSON-schema contracts, but no
production tool is registered in Phase 1. LLM tool-calling loops are Phase 3.

`BaseSandbox` defines start/stop, Python execution, and `.md`/`.txt` read/write.
`DockerSandbox` raises `NotImplementedError` for every operation. **No sandbox
isolation or private filesystem is claimed in Phase 1.** Phase 3 will add one
container/workspace per agent, quotas (CPU/memory/time/network), and approval gates
for writes.

## 12. Human-in-the-loop boundary

Phase 1 has no approval/pause/resume workflow. The agent action protocol reserves
`fail` for honest abandonment and the task model has `BLOCKED` for delegation waits.
Phase 4 will add a human-interaction gateway, permission scopes, clarification
messages, and resumable blocked tasks. No fake HITL behaviour is implemented now.

## 13. Security and safety

- Secrets from environment/`.env`; never logged, persisted, or echoed.
- No OpenAI/Anthropic endpoints or SDKs.
- Provider errors sanitized to status code plus truncated response text.
- LLM output is never trusted for control flow: plan structure, dependencies,
  capabilities, delegation depth, and task counts are validated by the platform.
- Tool output and sandbox execution are not implemented, so there is no false claim
  of isolation.
- Prompt-injection hardening for future tools is a Phase 3/5 requirement.
- SQLite has no authentication; it is a local course-project store.

## 14. Why not LangGraph in Phase 1?

LangGraph is allowed by the project brief, but a fixed graph would make it easy to
mistake a static flow for autonomous decomposition. Phase 1 uses a small explicit
executor whose graph is **generated at runtime** from the LLM plan and validated
before execution. This keeps autonomy, auditability, and tests transparent. A future
`LangGraphOrchestrator` adapter can implement the same `run_goal` contract while
reusing models, messages, agents, and persistence.

## 15. Extension checklist for later phases

1. Add a new agent: extend `AgentRole`, add a `BaseAgent` subclass with role
   instructions, register it, and give it capability tokens.
2. Add a provider: implement `BaseLLMProvider`, add factory validation, add protocol
   tests with `httpx.MockTransport`.
3. Add a tool: implement `BaseTool`, register it in the runtime, and add an LLM
   tool-calling loop with permission checks.
4. Add a sandbox: implement `BaseSandbox` with Docker, quotas, and workspace
   isolation; wire per-agent instances.
5. Add memory evolution: implement a richer `MemoryStore` without changing agents.
6. Add HITL: introduce an interaction gateway and a new task status handling path
   that resumes BLOCKED tasks with human input.
7. Add concurrency: execute independent ready tasks with `asyncio.gather` while
   preserving the same state machine and persistence semantics.
