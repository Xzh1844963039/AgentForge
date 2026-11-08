# AgentForge Memory System (Phase 3)

## 1. Purpose

Phase 3 turns the per-agent memory namespace into an evidence-first, evolving memory
system. Memory is derived from the Phase 2 SQLite tool audit and `ToolEvidence`, not
from chat text or free-form model claims.

The central rule:

> Only execution evidence with traceable audit records can become high-confidence
> procedural memory.

Mock decisions can exercise the full pipeline, but they cannot be reported as real
learning performance.

## 2. Memory taxonomy

| Type | Meaning | Examples |
|---|---|---|
| `EPISODIC` | What happened in one task/workflow | tools used, status, key output, duration |
| `SEMANTIC` | A grounded fact extracted from an execution | Python stdout, a file path/content observation |
| `PROCEDURAL` | A repeated, successful tool sequence with argument hints | write `notes/verified.md` then read it back |
| `WORKING` | Short-lived recent context | current workflow/task summary, TTL-limited |

All entries are agent-scoped. Namespace is a second isolation dimension (for example
`experience` vs `conversation`). No query can return another agent's memories.

## 3. Verification levels

Every memory carries a `VerificationLevel` and a `MemorySource`:

| Level | Meaning |
|---|---|
| `REAL_EXECUTION` | All referenced tool calls succeeded and came from real tools (file, Docker Python, local Git), with persisted `tool_calls` IDs. The LLM provider may still be Mock; that is recorded separately. |
| `MOCK_SIMULATION` | At least one referenced tool result was a mock/simulated tool (e.g. mock search) or the evidence is a synthetic simulation. Never promoted to high-confidence. |
| `UNVERIFIED` | Missing/failed/incomplete audit, or a legacy chat/task memory with no tool evidence. |

`MemorySource` includes:

- workflow ID, task ID, agent ID;
- tool call IDs and tool names;
- evidence digest;
- LLM provider and whether it was Mock;
- tool execution mode (`real` / `mock` / `none`).

The LLM provider being Mock does **not** erase real file/Python audit evidence. It means
the decision policy is deterministic; the source clearly records both facts.

## 4. Evidence-first reflection

`MemoryEvolutionEngine.process_workflow(workflow_id)`:

1. loads the workflow, tasks and `tool_calls` from SQLite;
2. groups tool calls by task;
3. converts each record to `ToolEvidence`;
4. classifies verification;
5. writes an episodic reflection, one grounded semantic fact (when output exists),
   and a TTL working-memory entry;
6. updates procedural candidates from the actual tool sequence and argument hints;
7. promotes candidates that meet the evidence threshold;
8. applies retrieval feedback and prunes low-value/expired entries.

Reflection is deterministic and evidence-first. It does not ask an LLM to invent a
lesson. An LLM may later narrate the result, but the recorded facts come from audit.

## 5. Procedural promotion

A procedural candidate is keyed by agent, capabilities, task kind, ordered tool steps,
argument hints (path/filename/url/query/remote/branch) and task terms.

- Every successful real task with tools contributes evidence.
- `support_count` counts **distinct workflows**, not repeated processing of one run.
- A candidate is promoted to `PROCEDURAL` only when:
  - verification is `REAL_EXECUTION`;
  - at least `AGENTFORGE_MEMORY_PROMOTION_THRESHOLD` (default 2) distinct workflows
    support it;
  - confidence meets `AGENTFORGE_MEMORY_PROMOTION_CONFIDENCE` (default 0.8).
- Mock candidates are stored only as `PROVISIONAL` with low confidence and an explicit
  `MOCK SIMULATION - NOT REAL-WORLD VERIFIED` label. They are excluded from default
  agent retrieval.
- Failed tasks produce `UNVERIFIED` memories and never a procedural candidate.

Promoted content is generated from the audit:

```text
Verified procedure for agent 'coding' (kind=coding; capabilities: coding).
Successful tool sequence:
  1. write_file(path='notes/verified.md')
  2. read_file(path='notes/verified.md')
Evidence: 2 distinct workflow(s), verification=REAL_EXECUTION.
Prefer this exact sequence and arguments when a task matches ...
```

## 6. Retrieval

`EvolutionMemoryStore` implements the existing `MemoryStore` interface so Phase 1/2
agents use it without changes.

Retrieval:

1. filters by `agent_id`, optional namespace, active TTL and verification policy;
2. builds a local hashing embedding for the query and each memory;
3. scores by cosine similarity, keyword overlap, decayed importance, verification
   weight and memory-type boost;
4. excludes Mock/Unverified by default (`AGENTFORGE_MEMORY_INCLUDE_MOCK=false`);
5. caps entries and total prompt characters;
6. prefixes every entry with type, verification, confidence and support count;
7. records a retrieval event for feedback.

The embedding provider is a deterministic local hashing vector (`local-hashing`).
It is an embedding-based retrieval scaffold; learned-embedding semantic quality is
**NOT VERIFIED** and should be replaced/validated with a real embedding model later.

Example prompt entry:

```text
[procedural|REAL_EXECUTION|confidence=0.85|support=2] Verified procedure for agent ...
```

## 7. Feedback and action influence

Every recall records a `RetrievalEvent` with the returned memory IDs. After a task,
the engine matches events inside the task's start/end window to procedural memories:

- if the actual tool sequence/arguments match the procedure and all calls succeed,
  feedback `used=True, success=True`, confidence increases (default +0.03);
- if used but failed, confidence decreases (default -0.10);
- if retrieved but not used, feedback records `used=False` with no change.

This closes the loop: retrieval → action → observed outcome → confidence update.

The controlled experiment proves the action effect by changing a real tool argument:

- with procedural memory: `write_file(path="notes/verified.md")`;
- without memory: `write_file(path="notes/default.md")`;
- same task, provider class, tool permissions, budgets, and model;
- only memory recall differs.

This is a **pipeline-level** proof using deterministic Mock LLM decisions and real file
tools. Real-LLM learning performance is **NOT VERIFIED** without an API key.

## 8. Importance, decay, consolidation, forgetting

- `effective_importance = importance × 0.5^(age/half_life) × verification_weight × access_boost`.
- Verification weights: REAL 1.0, MOCK 0.55, UNVERIFIED 0.25.
- Duplicate memories with the same `(agent_id, dedupe_key)` consolidate:
  support count increases across distinct workflows and evidence IDs merge.
- Expired working memories are deleted.
- Old low-value Mock/Unverified memories below the forget threshold are pruned;
  promoted real procedural memories are retained.
- Real execution memories are not pruned by the default policy.

## 9. Configuration

| Variable | Default | Meaning |
|---|---|---|
| `AGENTFORGE_MEMORY_EVOLUTION_ENABLED` | `true` | Run reflection after workflows |
| `AGENTFORGE_MEMORY_RECALL_ENABLED` | `true` | Allow agent retrieval |
| `AGENTFORGE_MEMORY_FILE` | `<data>/memory_evolution.db` | Separate memory DB |
| `AGENTFORGE_MEMORY_RECALL_LIMIT` | `6` | Max entries |
| `AGENTFORGE_MEMORY_PROMPT_BUDGET_CHARS` | `3000` | Prompt budget |
| `AGENTFORGE_MEMORY_INCLUDE_MOCK` | `false` | Include Mock memories in recall |
| `AGENTFORGE_MEMORY_INCLUDE_UNVERIFIED` | `false` | Include unverified memories |
| `AGENTFORGE_MEMORY_PROMOTION_THRESHOLD` | `2` | Distinct real workflows |
| `AGENTFORGE_MEMORY_PROMOTION_CONFIDENCE` | `0.8` | Minimum promotion confidence |
| `AGENTFORGE_MEMORY_DECAY_HALF_LIFE_DAYS` | `30` | Decay half-life |
| `AGENTFORGE_MEMORY_FORGET_THRESHOLD` | `0.05` | Forget threshold |
| `AGENTFORGE_MEMORY_WORKING_TTL_SECONDS` | `3600` | Working memory TTL |
| `AGENTFORGE_MEMORY_EMBEDDING_DIMENSIONS` | `256` | Local vector size |
| `AGENTFORGE_MEMORY_STORE_MOCK_PROCEDURES` | `true` | Store provisional Mock procedures (low confidence) |

## 10. CLI

```powershell
agentforge memory list --agent coding --verification REAL_EXECUTION
agentforge memory search "create and verify a note" --agent coding
agentforge memory stats --agent coding
agentforge memory candidates --agent coding
agentforge memory feedback --agent coding
agentforge memory clear --agent coding --confirm
agentforge memory experiment
```

`memory experiment` runs the controlled with/without-memory ablation and prints the
promoted memory, changed tool path, feedback result and the explicit
`Real LLM learning effect: NOT VERIFIED` marker.

## 11. Tests

Phase 3 tests cover:

- agent/namespace isolation;
- verification filtering and labels;
- embedding ranking and prompt budget;
- working-memory expiry;
- decay/forgetting;
- dedupe/consolidation and candidate support across distinct workflows;
- real vs mock vs failed audit classification;
- promotion only for real evidence;
- retrieval-to-action feedback and confidence update;
- cross-agent no-retrieval;
- full controlled experiment with action change and feedback;
- CLI list/search/stats/clear/experiment;
- Phase 1/2 regression (including Docker).

## 12. Limitations

- Local hashing embeddings are deterministic but not a learned semantic model;
  semantic retrieval quality is not benchmarked.
- Real-LLM learning performance is **NOT VERIFIED**; no API key was available.
- Procedural matching is exact on tool sequence and selected argument hints. More
  general similarity/transfer is future work.
- Memory is per agent and per namespace; no cross-agent sharing by design.
- Feedback matching uses task start/end windows; concurrent tasks in one agent would
  require a task-scoped retrieval context (future).
- Working memory is TTL-based, not a full conversational state machine.
- No embedding API provider is configured; the interface is ready for one.
