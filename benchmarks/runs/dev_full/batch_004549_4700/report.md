# AgentForge Benchmark Report

- Batch: `batch_cd99f7b4932f4c02b7338d4707fb158b`
- Dataset: `dev` (`D:\5202nlp课程project\benchmarks\dev`)
- Git commit: `f4e8c4965e01279a621b37ea9045e8f93d1806c4`
- Mode: `multi_agent`
- Provider: `mock` (fixture)
- Repeats: 1
- Runs: 34

- OS: `Windows-11-10.0.26200-SP0`
- Python: `3.12.10`
- Executable: `D:\5202nlp课程project\.venv\Scripts\python.exe`

> **MOCK FIXTURE RESULT.** LLM actions are deterministic fixtures used only to validate the benchmark harness, tools, state machine and verifiers. These scores are NOT real-LLM capability and must not be reported as model performance.

## 1. Aggregate results

| Metric | Value |
|---|---:|
| Goal completion rate (verified) | 97.1% |
| Passed / failed | 33 / 0 |
| Needs review / skipped | 1 / 0 |
| Timeout / runtime error | 0 / 0 |
| Average E2E seconds | 0.722 |
| Median E2E seconds | 0.125 |
| p95 E2E seconds | 2.332 |
| Average active seconds | 0.661 |
| Total simulated human wait | 0.000s |
| Total LLM calls | 218 |
| Total / average tokens | 557270 / 16390.3 |
| Total tool calls | 54 |
| Tool failure rate | 22.2% |
| Retries (repeated identical calls) | 3 |
| Delegations / success rate | 3 / 100.0% |
| Memory retrievals / used | 0 / 0 |
| Approvals / clarifications | 3 / 3 |
| Recovery success rate | 100.0% |
| Crash/timeout rate | 0.0% |

## 2. Results by category

| Category | Total | Passed | Success | Avg E2E | Avg active | Avg tools | Avg LLM calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| approval | 3 | 3 | 100.0% | 0.114s | 0.052s | 2.00 | 7.00 |
| clarification | 3 | 3 | 100.0% | 0.099s | 0.042s | 0.67 | 5.33 |
| code_execution | 4 | 4 | 100.0% | 2.269s | 2.211s | 2.00 | 7.00 |
| code_generation | 3 | 3 | 100.0% | 2.161s | 2.099s | 1.00 | 6.00 |
| debugging | 3 | 3 | 100.0% | 2.104s | 2.042s | 2.00 | 7.00 |
| delegation | 3 | 3 | 100.0% | 0.120s | 0.057s | 1.67 | 7.67 |
| failure_recovery | 5 | 5 | 100.0% | 0.100s | 0.041s | 1.20 | 5.20 |
| file_editing | 4 | 4 | 100.0% | 0.118s | 0.055s | 1.75 | 6.50 |
| planning | 3 | 3 | 100.0% | 0.109s | 0.052s | 1.67 | 6.33 |
| research | 3 | 2 | 66.7% | 0.125s | 0.057s | 2.00 | 6.67 |

## 3. Per-task runs

| Task | Category | Diff | Status | Verified | E2E | Active | Tokens | Tools | Failure |
|---|---|---|---|---|---:|---:|---:|---:|---|
| `research_basic_facts` | research | basic | passed | passed | 0.125s | 0.063s | 19204 | 2 | - |
| `research_conflicting_sources` | research | hard | passed | passed | 0.141s | 0.063s | 20326 | 3 | - |
| `research_manual_review` | research | hard | needs_review | needs_review | 0.109s | 0.046s | 15417 | 1 | manual_review_required |
| `codegen_add_function` | code_generation | basic | passed | passed | 2.234s | 2.172s | 15411 | 1 | - |
| `codegen_stats_edge_cases` | code_generation | hard | passed | passed | 2.125s | 2.078s | 15548 | 1 | - |
| `codegen_word_count` | code_generation | hard | passed | passed | 2.125s | 2.047s | 15526 | 1 | - |
| `debug_off_by_one` | debugging | basic | passed | passed | 2.141s | 2.078s | 19181 | 2 | - |
| `debug_two_bugs` | debugging | hard | passed | passed | 2.094s | 2.031s | 19435 | 2 | - |
| `debug_syntax_error` | debugging | hard | passed | passed | 2.078s | 2.016s | 19253 | 2 | - |
| `file_exact_create` | file_editing | basic | passed | passed | 0.110s | 0.047s | 15158 | 1 | - |
| `file_multi_artifact` | file_editing | hard | passed | passed | 0.125s | 0.062s | 16005 | 2 | - |
| `file_revision_copy` | file_editing | hard | passed | passed | 0.125s | 0.062s | 18990 | 2 | - |
| `file_invalid_path_recovery` | file_editing | hard | passed | passed | 0.110s | 0.047s | 18757 | 2 | - |
| `plan_two_dependent_files` | planning | basic | passed | passed | 0.125s | 0.063s | 21935 | 2 | - |
| `plan_research_to_coding` | planning | hard | passed | passed | 0.140s | 0.078s | 26372 | 3 | - |
| `plan_dependency_failure` | planning | hard | passed | passed | 0.063s | 0.016s | 3336 | 0 | - |
| `delegation_single_level` | delegation | basic | passed | passed | 0.141s | 0.078s | 24579 | 2 | - |
| `delegation_two_levels` | delegation | hard | passed | passed | 0.140s | 0.078s | 34181 | 3 | - |
| `delegation_depth_limit` | delegation | hard | passed | passed | 0.079s | 0.016s | 3313 | 0 | - |
| `execute_compute_sum` | code_execution | basic | passed | passed | 2.140s | 2.093s | 19185 | 2 | - |
| `execute_failing_test` | code_execution | hard | passed | passed | 2.515s | 2.453s | 19950 | 2 | - |
| `execute_timeout` | code_execution | hard | passed | passed | 4.312s | 4.250s | 19154 | 2 | - |
| `execute_invalid_arguments` | code_execution | hard | passed | passed | 0.109s | 0.047s | 18617 | 2 | - |
| `approval_delete_rejected` | approval | basic | passed | passed | 0.109s | 0.047s | 15522 | 2 | - |
| `approval_overwrite_approved` | approval | hard | passed | passed | 0.125s | 0.063s | 15784 | 2 | - |
| `approval_update_rejected` | approval | hard | passed | passed | 0.109s | 0.047s | 15548 | 2 | - |
| `clarification_retention` | clarification | basic | passed | passed | 0.110s | 0.047s | 15159 | 1 | - |
| `clarification_output_format` | clarification | hard | passed | passed | 0.125s | 0.062s | 15183 | 1 | - |
| `clarification_unanswered` | clarification | hard | passed | passed | 0.063s | 0.016s | 654 | 0 | - |
| `recovery_pause_resume` | failure_recovery | basic | passed | passed | 0.125s | 0.062s | 15123 | 1 | - |
| `recovery_tool_error_retry` | failure_recovery | hard | passed | passed | 0.125s | 0.063s | 22686 | 3 | - |
| `recovery_unachievable` | failure_recovery | hard | passed | passed | 0.078s | 0.016s | 3305 | 0 | - |
| `recovery_cancel` | failure_recovery | hard | passed | passed | 0.063s | 0.016s | 646 | 0 | - |
| `recovery_permission_denied` | failure_recovery | hard | passed | passed | 0.109s | 0.046s | 18827 | 2 | - |

## 4. Failure modes

| Failure category | Count |
|---|---:|
| manual_review_required | 1 |

## 5. Metric definitions

- **Goal completion** is decided only by the task's objective verifier, not by `workflow.status == completed`.
- **E2E seconds** are monotonic wall-clock from task start to final observation; timestamps remain in the JSON record for audit.
- **Active seconds** exclude scripted human waiting. **Human wait** is simulated and explicitly labeled.
- **Token usage** for fixture runs is an estimate from prompt/response length; live runs use provider usage.
- **Retries** count repeated identical `(tool, arguments)` invocations beyond the first.

## 6. Notes and limitations

- Fixture-provider runs validate the harness only; they are not evidence of real-LLM task performance.
- Human responses in benchmark tasks are scripted simulations.
