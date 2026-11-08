# AgentForge Tool System

**Phase 2 — real tool execution through a unified dispatcher**

## 1. Design goals

1. Agents may choose tools autonomously, but never execute them directly.
2. Every invocation is schema-validated, permission-checked, timed out, and audited.
3. Tool success and failure are both returned to the agent as structured data so it can reason and retry.
4. Tool execution is provider-agnostic: DeepSeek, HKGAI and the deterministic mock all use the same JSON action protocol.
5. Dangerous actions (delete, push) require an explicit approval decision.
6. Tool output is untrusted data, never a source of authority.

## 2. Components

| Component | File | Responsibility |
|---|---|---|
| `ToolSpec` | `tools/base.py` | Name, description, input/output JSON Schema, timeout, permissions, approval flag, untrusted flag |
| `BaseTool` | `tools/base.py` | Typed arguments/output models and `run()` implementation |
| `ToolRegistry` | `tools/base.py` | Registration, lookup, prompt catalog |
| `ToolExecutionContext` | `tools/base.py` | Agent, workflow, task, private workspace, sandbox |
| `ToolPermissionPolicy` | `tools/policy.py` | Default-deny per-agent permission grants and approval requirements |
| `ApprovalGate` | `tools/approval.py` | Approval interface, argument digest, `ApprovalRequired` suspension signal |
| `ApprovalService` | `interaction/approval_service.py` | Persistent parameter-bound approvals, TTL, one-time consumption |
| `ToolExecutor` | `tools/executor.py` | The only dispatcher: permissions, schemas, approvals, replay, timeout, output cap, audit |
| `ToolCallRecord` | `models/tool.py` | Durable audit record |
| `WorkspaceManager` | `tools/workspace.py` | Secure private file paths and atomic I/O |
| `ArtifactStore` | `tools/artifact_store.py` | Controlled cross-agent file transfer |
| `DockerSandbox` | `sandbox/docker.py` | Isolated Python execution |

Agents receive a `ToolExecutor` and a `ToolRegistry`. They never receive a path to another agent's workspace, and they cannot call the handler function of a tool directly.

## 3. Contracts

### 3.1 Tool specification

Each tool declares:

```python
class BaseTool:
    name: ClassVar[str]
    description: ClassVar[str]
    args_model: ClassVar[type[BaseModel]]       # JSON Schema generated
    output_model: ClassVar[type[BaseModel] | None]
    permissions: ClassVar[list[ToolPermission]]
    timeout_seconds: ClassVar[float]
    requires_approval: ClassVar[bool]
    untrusted_output: ClassVar[bool]
```

At construction, the tool builds a `ToolSpec`:

- `input_schema = args_model.model_json_schema()`
- `output_schema = output_model.model_json_schema()`
- permissions, timeout, approval and untrusted flags

Argument models use `ConfigDict(extra="forbid")`, so misspelled or extra fields are rejected.

### 3.2 Agent tool-call action

The LLM must return a single JSON object:

```json
{
  "action": "tool_call",
  "tool_calls": [
    {"name": "read_file", "arguments": {"path": "notes.md"}}
  ],
  "reasoning": "Need the current note content."
}
```

Each call has a unique `call_id`. Multiple calls in one turn are executed sequentially. The platform enforces a per-task tool-call budget (`AGENTFORGE_MAX_TOOL_CALLS_PER_TASK`, default 6).

### 3.3 Tool result

```json
{
  "status": "success|error|timeout|denied|pending_approval",
  "output": {"...": "structured tool output"},
  "error": null,
  "duration_seconds": 0.014,
  "metadata": {"tool_name": "read_file", "untrusted_output": false}
}
```

The result is appended to the agent's conversation as a `[TOOL_RESULTS]` message. The agent then continues with another LLM call. Successful and failed executions follow the same path; there is no "pretend success" shortcut.

## 4. Dispatcher pipeline

`ToolExecutor.execute(tool_call, context)` performs, in order:

1. **Lookup** — unknown tools return `error`.
2. **Permission policy** — all declared permissions must be granted; otherwise `denied`.
3. **Input schema validation** — JSON Schema (`Draft 2020-12`); invalid arguments return a detailed error and never reach the tool.
4. **Replay cache** — on a resumed task, a matching prior SUCCESS for the same workflow/task/tool/arguments returns the recorded output with `replayed=true`; the tool body is not called.
5. **Approval** — if the tool or its permissions require approval:
   - no token → persist a `PENDING` `ApprovalRequest`; runtime default raises `ApprovalRequired` and suspends the task; no side effect;
   - token → validate tool name, canonical argument digest, workflow/task/agent, status and expiry, then consume once;
   - `REJECTED`/`EXPIRED`/`CANCELLED` → `denied`, no side effect;
   - `APPROVED` → continue.
6. **Timeout** — `asyncio.wait_for` with the tool's timeout.
7. **Execution** — tool handler with validated Pydantic arguments.
8. **Exception handling** — unexpected exceptions become structured `error` results, not crashes.
9. **Output validation** — success output must satisfy the tool's output schema.
10. **Output cap** — oversized output is truncated with `truncated: true` metadata.
11. **Audit** — SQLite `tool_calls` + JSONL `tool_call` event, including duration, permission decision, approval status, and arguments.

## 5. Permission matrix

Default grants (see `tools/policy.py`):

| Permission | Coordinator | Research | Coding | Reviewer |
|---|:---:|:---:|:---:|:---:|
| `file_read` | ✅ | ✅ | ✅ | ✅ |
| `file_write` | ✅ | ✅ | ✅ | — |
| `file_delete` | — | — | ✅ (approval) | — |
| `python_execute` | — | — | ✅ | ✅ |
| `network_read` | ✅ | ✅ | ✅ | ✅ |
| `git_read` | — | — | ✅ | ✅ |
| `git_write` | — | — | ✅ | — |
| `git_push` | — | — | ✅ (approval) | — |
| `artifact_read` | ✅ | ✅ | ✅ | ✅ |
| `artifact_write` | ✅ | ✅ | ✅ | — |

Approval is mandatory for `file_delete`, `git_push`, `update_file`, and
`write_file overwrite=true` regardless of grant. In the runtime, `ApprovalService`
persists the request and pauses the workflow until a human decision; tokens are bound
to exact arguments and scope and consumed once. Tests also use `AutoApprovalGate` to
exercise immediate approved/denied paths. An approval never grants a missing
permission.

## 6. Agent tool loop

`BaseAgent.execute_task()`:

```text
build task prompt + tool catalog
repeat up to (max_tool_calls + 3) LLM turns:
    call provider
    parse action
    if action != tool_call:
        return complete / delegate / fail
    if tool executor or context missing:      -> fail honestly
    if tool budget would be exceeded:          -> fail honestly
    append assistant raw response
    execute each tool call through ToolExecutor
    append [TOOL_RESULTS] as untrusted data
continue reasoning
```

Every LLM turn is recorded as an `agent_executions` row, so tool-loop token and latency costs are visible. Tool calls are recorded separately in `tool_calls`. The final task result includes the agent's final action content; the task result then flows through the normal DAG and governance review.

### 6.1 Structured evidence for synthesis, review and final report

Task prose alone is not sufficient evidence. The orchestrator:

1. computes the task's transitive dependency closure, including tasks created by agent-initiated delegation;
2. reads the corresponding `tool_calls` rows from SQLite;
3. converts them to `ToolEvidence` records containing tool, agent, task, status, success, output, error, duration, permissions, approval state and sandbox metadata;
4. injects them into synthesis/review/finalize prompts as a JSON `[TOOL_EVIDENCE]` block;
5. records `tool_call_ids` and `tool_call_count` on the producing task.

Report generation must use this evidence, not regexes over LLM prose. The Mock provider builds its prime report from the recorded stdout/stderr/exit code/duration/container and distinguishes success, empty stdout, nonzero exit, missing exit code, exception, timeout and multiple calls. Real LLM providers receive the same evidence block. Reviews use the recorded facts: a failed/timeout call produces `REJECTED`; a successful call is approved with issues. No fixed "Docker is required" warning is emitted when the audit shows a real successful execution.

## 7. File tools

Tools: `list_files`, `read_file`, `write_file`, `update_file`, `delete_file`, `search_files`.

Rules:

- Only `.md` and `.txt` are accepted by the public file tools.
- Each agent has a private root: `<workspace_root>/<agent_id>/`.
- Absolute paths, drive letters, `..`, NUL/control characters, Windows reserved device names (`CON`, `NUL`, `COM1`, ...), trailing spaces/dots and unsupported characters are rejected.
- Every path component is checked for symlinks; the resolved path must remain under the agent root.
- Writes are atomic (temp file + `os.replace`), with a per-file byte cap.
- Reads are capped and report truncation.
- `write_file` refuses to overwrite unless `overwrite=true`; explicit overwrite requires approval.
- `update_file` requires an existing file and supports replace/append; both modes require approval.
- `delete_file` requires approval.
- Listing and search return relative POSIX paths and are capped.
- There is no tool that can read another agent's workspace. Cross-agent data moves only through artifacts.

## 8. Artifact transfer

`share_artifact(path)` copies a Markdown/TXT file from the caller's private workspace into a central immutable artifact store and returns an `artifact_id`, SHA-256 and size.

`fetch_artifact(artifact_id, dest_path)` verifies the stored hash and writes a copy into the caller's own workspace. There is no direct file path access to the artifact store from agents.

This provides a controlled alternative to direct cross-agent reads and is covered by isolation tests.

## 9. Python tools

- `python_syntax_check` uses `ast.parse` on the host. It never executes code.
- `python_execute` and `python_test` stage a `.py` file inside the caller's private workspace and execute it inside `DockerSandbox`.
- If Docker is missing, the tool returns an explicit error (`Docker sandbox unavailable`); it never falls back to `subprocess` on the host.
- stdout, stderr, exit code, timeout flag, duration and script path are returned to the agent.
- A nonzero exit is a tool `error` result, but the agent still receives stdout/stderr and can fix and retry.
- Sandbox limits: CPU, memory, PIDs, wall-clock timeout, output cap, read-only root, no network by default, non-root user, dropped capabilities.

## 10. Search tools

`web_search`:

- provider interface supports a deterministic `MockSearchProvider` (default, no network) and a configurable `HttpSearchProvider`;
- results contain title, URL, snippet and source;
- mock results are explicitly labelled `mock: true`;
- no fabricated real results: the mock provider's URLs use the reserved `.invalid` domain.

`fetch_webpage`:

- only http/https;
- blocks localhost, private/special-use IPs, metadata hostnames, credentials in URLs, and non-80/443 ports;
- resolves DNS and rejects any non-public address;
- validates every redirect target, limits redirects and response bytes;
- allows only text/html, text/plain, JSON/XML content types;
- marks content as untrusted in the tool result and prompt.

Residual risk: DNS rebinding between validation and the actual connection is not fully eliminated in Phase 2. See `docs/SANDBOX_SECURITY.md`.

## 11. Git tools

- Authorized repository root comes from `AGENTFORGE_GIT_REPO_ROOT`; tools are not registered without it.
- Commands are `git -C <root> <args...>` with an argument list, never a shell.
- Read: status, diff, log.
- Write: add and commit require explicit file paths within the repository; path traversal and absolute paths are rejected.
- `git_commit` stages the explicit paths and commits only that scope.
- `git_push` always requires approval. In the default runtime it persists a pending request and pauses the workflow; no push occurs until a human approves the exact command/scope. No credentials are injected; a credential-scrubbed environment is used.
- Tests only use a temporary local bare remote, never a real remote.

## 12. Audit and metrics

SQLite `tool_calls` columns include: tool call ID, workflow, task, agent, tool, arguments, status, output, error, duration, permission decision, approval status/id, permissions, start/end, metadata.

JSONL `tool_call` events mirror status, duration, approval status and error. HITL
transitions also emit `approval_requested`, `approval_decided`, `approval_consumed`
and `tool_effect_replayed`. CLI:

```powershell
agentforge tools
agentforge tool-calls --workflow <id> --agent coding
agentforge approvals list --workflow <id>
agentforge approvals show <approval_id>
```

Workflow metrics add `tool_calls`, `tool_failures`, `tool_seconds`, approval
requests/outcomes/rate/wait time, clarifications/wait time, pauses, resumes,
cancellations, delegated completion/success and reassignments.

## 13. Extending the tool system

1. Create a Pydantic args model (`extra="forbid"`) and output model.
2. Subclass `BaseTool`; set name, description, models, permissions, timeout, approval/untrusted flags.
3. Implement `async run(arguments, context)`.
4. Register it in `tools/builtins.py`.
5. Grant the permission to the roles that need it in `tools/policy.py`.
6. Add schema, permission, success, failure and audit tests.
7. For destructive actions, keep `requires_approval=True` or make the permission approval-required.

Never:
- execute raw shell strings,
- trust LLM-provided paths outside the agent workspace,
- return tool output without size limits,
- bypass `ToolExecutor`.
