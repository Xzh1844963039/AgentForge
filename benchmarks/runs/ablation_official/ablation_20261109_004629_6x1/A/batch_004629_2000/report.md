# AgentForge Benchmark Report

- Batch: `batch_b82b6883c50d444fa8515358906afad4`
- Dataset: `dev` (`D:\5202nlp课程project\benchmarks\dev`)
- Git commit: `f4e8c4965e01279a621b37ea9045e8f93d1806c4`
- Mode: `single_agent`
- Provider: `mock` (fixture)
- Repeats: 1
- Runs: 6

- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.12.10`
- Executable: `D:\5202nlp课程project\.venv\Scripts\python.exe`

> **MOCK FIXTURE RESULT.** LLM actions are deterministic fixtures used only to validate the benchmark harness, tools, state machine and verifiers. These scores are NOT real-LLM capability and must not be reported as model performance.

## 1. Aggregate results

| Metric | Value |
|---|---:|
| Goal completion rate (verified) | 100.0% |
| Passed / failed | 6 / 0 |
| Needs review / skipped | 0 / 0 |
| Timeout / runtime error | 0 / 0 |
| Average E2E seconds | 1.104 |
| Median E2E seconds | 1.101 |
| p95 E2E seconds | 2.110 |
| Average active seconds | 1.044 |
| Total simulated human wait | 0.000s |
| Total LLM calls | 36 |
| Total / average tokens | 94915 / 15819.2 |
| Total tool calls | 11 |
| Tool failure rate | 0.0% |
| Retries (repeated identical calls) | 0 |
| Delegations / success rate | 0 / 0.0% |
| Memory retrievals / used | 0 / 0 |
| Approvals / clarifications | 0 / 0 |
| Recovery success rate | 0.0% |
| Crash/timeout rate | 0.0% |

## 2. Results by category

| Category | Total | Passed | Success | Avg E2E | Avg active | Avg tools | Avg LLM calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| code_execution | 1 | 1 | 100.0% | 2.109s | 2.047s | 2.00 | 6.00 |
| code_generation | 1 | 1 | 100.0% | 2.110s | 2.047s | 1.00 | 5.00 |
| debugging | 1 | 1 | 100.0% | 2.062s | 2.000s | 2.00 | 6.00 |
| file_editing | 1 | 1 | 100.0% | 0.094s | 0.047s | 1.00 | 5.00 |
| planning | 1 | 1 | 100.0% | 0.140s | 0.078s | 3.00 | 8.00 |
| research | 1 | 1 | 100.0% | 0.109s | 0.047s | 2.00 | 6.00 |

## 3. Per-task runs

| Task | Category | Diff | Status | Verified | E2E | Active | Tokens | Tools | Failure |
|---|---|---|---|---|---:|---:|---:|---:|---|
| `research_basic_facts` | research | basic | passed | passed | 0.109s | 0.047s | 15886 | 2 | - |
| `codegen_add_function` | code_generation | basic | passed | passed | 2.110s | 2.047s | 12297 | 1 | - |
| `debug_off_by_one` | debugging | basic | passed | passed | 2.062s | 2.000s | 15855 | 2 | - |
| `file_exact_create` | file_editing | basic | passed | passed | 0.094s | 0.047s | 12111 | 1 | - |
| `plan_research_to_coding` | planning | hard | passed | passed | 0.140s | 0.078s | 22898 | 3 | - |
| `execute_compute_sum` | code_execution | basic | passed | passed | 2.109s | 2.047s | 15868 | 2 | - |

## 4. Failure modes

No classified failures.

## 5. Metric definitions

- **Goal completion** is decided only by the task's objective verifier, not by `workflow.status == completed`.
- **E2E seconds** are monotonic wall-clock from task start to final observation; timestamps remain in the JSON record for audit.
- **Active seconds** exclude scripted human waiting. **Human wait** is simulated and explicitly labeled.
- **Token usage** for fixture runs is an estimate from prompt/response length; live runs use provider usage.
- **Retries** count repeated identical `(tool, arguments)` invocations beyond the first.

## 6. Notes and limitations

- Fixture-provider runs validate the harness only; they are not evidence of real-LLM task performance.
- Human responses in benchmark tasks are scripted simulations.
