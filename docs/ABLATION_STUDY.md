# Ablation Study (Phase 5)

## 1. Configurations

| Config | Agent fleet | Memory | Purpose |
|---|---|---|---|
| `A_single_agent` | One all-capability Coordinator | Off | Single-agent baseline through the same ToolExecutor, permissions, sandbox, persistence and governance finalisation |
| `B_multi_agent` | Coordinator + Research + Coding + Reviewer | Off | Multi-agent baseline with independent review |
| `C_multi_agent_memory` | Coordinator + Research + Coding + Reviewer | On | Multi-agent with a learning phase before evaluation |

The single-agent baseline keeps the same tool dispatcher and safety limits. It
uses one agent with the union of fleet capabilities and no independent reviewer;
this is the standard difference between a single-agent and collaborative setup.

## 2. Controlled variables

All configurations use:

- the same evaluation task IDs and initial workspaces;
- the same provider kind and model (`benchmark-fixture` / deterministic policy in
  this run);
- the same temperature and execution budgets;
- the same tool permission framework and per-task tool allow-lists;
- the same verifiers and success criteria.

## 3. Memory protocol

C runs two **learning** tasks first (`file_multi_artifact`,
`file_revision_copy`) with memory evolution enabled. Those runs are excluded from
the evaluation aggregates. Evaluation tasks then run with retrieval enabled and
evolution disabled. The evaluation tasks do not include the learning task
answers, and fixture actions are not pre-written into memory.

Initial shared memory after learning contained **39 entries** as reported by
`EvolutionMemoryStore.total_count()`.

## 4. Evaluation subset

Six non-HITL tasks are used so that the single-agent baseline can execute the
same tasks without special cooperation:

- `research_basic_facts`
- `codegen_add_function`
- `debug_off_by_one`
- `file_exact_create`
- `execute_compute_sum`
- `plan_research_to_coding`

HITL/approval/clarification tasks are evaluated separately in the full benchmark
and would require scripted human interaction in every configuration.

## 5. Actual results (fixture policy)

Command:

```powershell
python -m agentforge benchmark compare --dataset benchmarks/dev --output-dir benchmarks/runs/ablation_official
```

Batch: `ablation_20261109_004629_6x1`

| Config | Runs | Verified passed | Success | Avg E2E | Avg active | Avg tokens | Memory retrievals | Memory used |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| A — single agent | 6 | 6 | 100.0% | 1.104s | 1.079s | 15,819 | 0 | 0 |
| B — multi-agent | 6 | 6 | 100.0% | 1.112s | 1.087s | 19,085 | 0 | 0 |
| C — multi + memory | 6 | 6 | 100.0% | 1.135s | 1.098s | 20,098 | 5 | 0 |

What the numbers show:

- All three configurations solve the same six tasks under the deterministic
  fixture policy. No completion difference is observed.
- Multi-agent runs use roughly 20% more tokens in this subset because synthesis,
  independent review and finalisation are separate LLM turns.
- C performs five real memory retrievals, but the fixture policy does not consult
  retrieved content when choosing actions, so `memory_usage=0`; no learning
  benefit can be claimed.
- Differences in E2E seconds are dominated by Docker startup (which affects every
  Python task similarly) and are not statistically meaningful at n=6.

## 6. Memory equality caveat

The Phase 3 controlled experiment demonstrated that retrieval can change a real
tool argument (`notes/default.md` → `notes/verified.md`) using a purpose-built
memory-aware policy. This Phase 5 ablation did not inject answer-specific memory
or a memory-aware fixture into evaluation tasks, because that would overfit the
benchmark and violate the “do not pre-write evaluation answers” rule.

Therefore:

- **Mock ablation result:** plumbing validated, no performance claim.
- **Real-LLM memory effect:** NOT VERIFIED; requires a live key, a learning split
  and a real model that chooses to reuse the retrieved procedure.

## 7. Repeats and statistics

The framework supports `--repeats N` and reports mean, median, p95 and per-run
distributions. The official run used `N=1` because the fixture policy is
deterministic and repeated runs add no information about LLM variance. With a
live provider, at least 3–5 repeats per task would be needed before discussing
variability; significance would still be limited by the task count.

## 8. Interpretation

This ablation is evidence that:

- the same tasks/budgets can run under all three topologies;
- single-agent and multi-agent paths both produce verifier-passing artifacts;
- multi-agent coordination has measurable token overhead;
- memory retrieval is wired and auditable without contaminating the evaluation.

It is **not** evidence that multi-agent or memory improves real-LLM task
completion. That claim remains NOT VERIFIED until the live acceptance suite is
run with valid DeepSeek/HKGAI credentials and human review of open-ended tasks.
