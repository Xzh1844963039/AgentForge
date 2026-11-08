# Phase 3 Report — Memory Evolution

**Date:** 2026-11-08
**Phase:** 3 — Memory Evolution
**Environment:** Windows 10.0.26200 (x64), Python 3.12.10, pytest 8.4.2,
Docker Desktop 29.9.2 (Phase 2 regression)
**Status:** Complete for the engineering pipeline. Real-LLM learning performance is
**NOT VERIFIED** because no DeepSeek/HKGAI API key was available.

---

## 1. Executive summary

Phase 3 adds an evidence-first memory system to AgentForge. Memory is not generated
from chat text; it is derived from the Phase 2 SQLite `tool_calls` audit and
`ToolEvidence`. Every memory carries a source and one of three verification levels:

- `REAL_EXECUTION`
- `MOCK_SIMULATION`
- `UNVERIFIED`

Only real execution evidence with traceable tool-call IDs can gradually become a
high-confidence `PROCEDURAL` memory. Mock evidence is stored as clearly labelled low-
confidence `PROVISIONAL` memory and is excluded from default agent retrieval.

The phase includes:

- episodic / semantic / procedural / working memory taxonomy;
- deterministic evidence-first reflection;
- candidate support counting across distinct workflows;
- procedural promotion thresholds;
- local hashing embedding retrieval with keyword/importance fallback;
- per-agent and per-namespace isolation;
- importance decay, consolidation, working-memory TTL and forgetting;
- retrieval feedback that adjusts procedural confidence;
- a controlled with/without-memory experiment proving that retrieval changes a real
  tool action;
- CLI commands and a Phase 3 experiment demo.

**Current baseline:** `205 passed, 2 skipped`, `78%` overall coverage, `93%` coverage
for `agentforge.memory`. The two skips are the pre-existing Windows symlink test and
the Docker-missing failure-path test (Docker was available).

---

## 2. Requirements → implementation

| Requirement | Result |
|---|---|
| Memory built on SQLite Tool Audit / ToolEvidence | ✅ Engine reads `tool_calls`, converts to `ToolEvidence`, stores audit IDs/digests |
| Source and verification levels | ✅ `MemorySource` + `REAL_EXECUTION` / `MOCK_SIMULATION` / `UNVERIFIED` |
| Only real evidence becomes high-confidence procedural | ✅ Promotion requires REAL_EXECUTION + 2 distinct workflows + confidence ≥ 0.8 |
| Mock pipeline validation, no real learning claim | ✅ Mock memories provisional/low-confidence; experiment reports real LLM NOT VERIFIED |
| Retrieval changes subsequent action | ✅ Controlled experiment: verified path vs default path |
| With/without-memory controlled experiment | ✅ Same goal/model/tools/budget; only recall differs |
| Isolation follows Agent/Workspace/Namespace design | ✅ All queries scoped by `agent_id` + namespace; no cross-agent recall |
| Real API unavailable → mark performance NOT VERIFIED | ✅ Experiment/Docs explicitly report NOT VERIFIED |

---

## 3. Memory architecture

```text
Phase 2 SQLite tool_calls audit
        │
        ▼
ToolEvidence (recorded status/output/error/duration/metadata)
        │
        ▼
MemoryEvolutionEngine (deterministic, evidence-first)
  ├─ episodic reflection
  ├─ semantic execution fact
  ├─ working memory (TTL)
  └─ procedural candidates
        │  distinct-workflow support
        ▼
promotion: REAL_EXECUTION only, support ≥ 2, confidence ≥ 0.8
        │
        ▼
memory_evolution.db  (separate DB; Phase 2 audit untouched)
        │
        ▼
EvolutionMemoryStore.recall
  ├─ embedding cosine + keyword + decayed importance + verification weight
  ├─ prompt budget + provenance prefixes
  └─ retrieval event
        │
        ▼
Agent prompt [RELEVANT_MEMORY] → action
        │
        ▼
tool_calls audit → feedback matcher → confidence update
```

The Phase 2 Tool/Sandbox/Orchestrator implementations were not rewritten. Runtime
adds the memory store/engine and a post-workflow hook; agents continue to use the
existing `MemoryStore` interface.

---

## 4. Taxonomy and provenance

| Type | Purpose |
|---|---|
| `EPISODIC` | Per-task execution summary with tools/status/results/evidence IDs |
| `SEMANTIC` | A grounded fact from stdout/file path/content |
| `PROCEDURAL` | Repeated successful tool sequence and argument hints |
| `WORKING` | TTL-limited recent context |

`MemorySource` records workflow, task, agent, tool-call IDs, tool names, evidence
digest, LLM provider, whether the LLM was Mock, and whether tool execution was real or
mock. `llm_is_mock` and `tool_execution=real` can coexist: the decision policy may be
Mock while the file/Python execution evidence is real.

---

## 5. Reflection and promotion

Reflection is deterministic: it summarizes the audit. It does not invent lessons.

A procedural candidate is keyed by agent, capabilities, task kind, ordered tool steps,
argument hints and task terms. `support_count` counts distinct workflow IDs. Promotion
requires:

1. all tool calls succeeded;
2. no mock tool evidence;
3. workflow completed;
4. at least 2 distinct real workflows;
5. confidence ≥ 0.8.

A promoted memory content example:

```text
Verified procedure for agent 'coding' (kind=coding; capabilities: coding).
Task terms: a, and, create, it, note, verify.
Successful tool sequence:
  1. write_file(path='notes/verified.md')
  2. read_file(path='notes/verified.md')
Evidence: 2 distinct workflow(s), verification=REAL_EXECUTION.
Prefer this exact sequence and arguments when a task matches ...
```

Mock candidates become `PROVISIONAL`, confidence 0.3, explicitly labelled
`[MOCK SIMULATION - NOT REAL-WORLD VERIFIED]`, and are not returned by default.

---

## 6. Retrieval

`EvolutionMemoryStore` implements the existing `MemoryStore` interface. Retrieval:

- filters by agent, namespace, TTL and verification policy;
- uses a deterministic 256-dimensional hashing embedding;
- combines cosine similarity, keyword overlap, decayed importance, verification
  weight and type boost;
- excludes Mock/Unverified by default;
- caps items and total prompt characters (default 3000);
- prefixes each entry with type, verification, confidence and support;
- records a retrieval event with memory IDs.

Example agent memory line:

```text
[procedural|REAL_EXECUTION|confidence=0.85|support=2] Verified procedure ...
```

The embedding is an offline scaffold, not a learned semantic model. Real learned-
embedding quality is NOT VERIFIED.

---

## 7. Decay, consolidation, forgetting

- Effective importance = importance × exponential half-life decay × verification
  weight × access boost.
- Duplicate entries with the same `(agent_id, dedupe_key)` consolidate and accumulate
  distinct workflow support.
- Expired working memories are removed.
- Old low-value Mock/Unverified entries are pruned below a threshold; promoted real
  procedural memories are retained.
- Real execution memories are not pruned by the default policy.

---

## 8. Feedback loop

Recall events include returned memory IDs. After a task, the engine matches retrieval
events inside the task start/end window to procedural memories:

- procedure sequence/arguments match + all calls succeed → `used=True`, `success=True`,
  confidence +0.03;
- match but failure → confidence -0.10;
- retrieved but not used → `used=False`, no confidence change.

Feedback is persisted in `memory_feedback`; support count remains the number of
**distinct workflows** and is not inflated by feedback.

---

## 9. Controlled experiment

`MemoryExperimentRunner`:

1. runs 2 learning workflows with REAL private-workspace file tools; deterministic Mock
   LLM policy writes `notes/verified.md` and reads it back;
2. promotes the procedure after 2 distinct real workflows;
3. runs evaluation with memory enabled and a control with recall disabled;
4. uses the same task, provider class/model, tool registry, permissions and budgets;
   only memory recall differs;
5. records real tool calls and feedback.

Actual result:

```text
Task: Create a note and verify it
LLM: mock (mock-deterministic-v1)
Learning workflows: 2
Candidate support: 2
Promoted memory: mem_... (REAL_EXECUTION, confidence=0.85)
With memory path: notes/verified.md
Without memory path: notes/default.md
Procedural memory retrieved: with=True without=False
Action changed: YES
Feedback: used=True success=True delta=+0.03
Pipeline verified: YES
Real execution verified: YES
Real LLM learning effect: NOT VERIFIED
```

The path difference is a real tool-call argument difference, proving retrieval changed
the agent's action. The decision policy is a deterministic Mock fixture, so this proves
the pipeline, not real-LLM learning performance.

CLI:

```powershell
python -m agentforge memory experiment
```

---

## 10. Tests and results

```text
python -m pytest -q
205 passed, 2 skipped in 66.26s

python -m pytest --cov=agentforge --cov-report=term -q
205 passed, 2 skipped in 72.24s
TOTAL: 5977 statements, 1326 missed, 78% coverage

python -m pytest --cov=agentforge.memory --cov-report=term-missing -q
205 passed, 2 skipped; agentforge.memory: 93% coverage

python -m ruff check src tests --select F,E9
All checks passed
```

Phase 3 tests cover:

- agent/namespace isolation;
- verification filtering and labels;
- embedding ranking;
- prompt-budget truncation;
- working-memory expiry;
- decay/forgetting;
- consolidation/support across distinct workflows;
- real/mock/failed audit classification;
- procedural promotion only for real evidence;
- retrieval feedback and confidence update;
- no chat-only memory when no audit exists;
- controlled action-change experiment with real file tools;
- CLI list/search/stats/clear/experiment;
- full Phase 1/2 regression including Docker.

The 2 skips are unchanged Phase 2 environment skips (Windows symlink privilege and the
Docker-missing failure-path test skipped because Docker was available).

---

## 11. CLI

```powershell
python -m agentforge memory list --agent coding --type procedural
python -m agentforge memory search "create and verify a note" --agent coding
python -m agentforge memory stats --agent coding
python -m agentforge memory candidates --agent coding
python -m agentforge memory feedback --agent coding
python -m agentforge memory clear --agent coding --confirm
python -m agentforge memory experiment
```

The experiment command prints the promoted memory, with/without action, retrieval
indicator, feedback and the explicit `Real LLM learning effect: NOT VERIFIED` line.

---

## 12. Known limitations

1. **Real-LLM learning performance NOT VERIFIED** — no DeepSeek/HKGAI key. The
   experiment uses a deterministic Mock decision policy.
2. **Local hashing embeddings** — deterministic and testable, but not a learned
   semantic model; retrieval quality is not benchmarked.
3. **Exact procedural matching** — feedback and retrieval rely on exact tool sequence
   and selected argument hints; fuzzy transfer/generalisation is future work.
4. **Mock memories** — stored only as provisional/low-confidence and excluded from
   default retrieval; they must not be used to claim real learning.
5. **LLM provider vs tool execution** — `llm_is_mock=true` with
   `tool_execution=real` is intentional; verification refers to execution evidence,
   not LLM cognitive quality.
6. **Concurrent feedback** — retrieval events are matched by task time window; a fully
   concurrent multi-task agent should use task-scoped retrieval contexts.
7. **No real embedding provider** — the interface is ready, but no external embedding
   service is configured.
8. **No cross-agent memory sharing** — intentional isolation; future sharing must go
   through controlled artifacts/messages.
9. **Phase 2 core untouched** — tools, sandbox and orchestrator were not redesigned;
   runtime has additive memory assembly/hook.

---

## 13. Git

Phase 3 changes are committed locally with the Phase 1/2 history preserved. No remote
push is performed. Commit hashes are reported in the final hand-off message.

---

## 14. Recommended next phase

Proceed to **Phase 4 — Human-in-the-Loop, permissions and pause/resume**:

1. connect `ApprovalGate` to interactive CLI approvals;
2. add clarification requests and resumable `BLOCKED` tasks;
3. add workflow pause/resume/cancel and permission scopes;
4. preserve the memory evidence/feedback contract in paused/resumed flows;
5. keep real-LLM memory performance explicitly NOT VERIFIED until API acceptance runs
   exist.
