# Ablation Study

## Ablation study

All configurations use the same task set, provider kind/model, temperature, tool framework and budgets.

| Config | Mode | Memory | Runs | Verified success | Avg E2E | Avg active | Avg tokens | Tool failures |
|---|---|---|---:|---:|---:|---:|---:|---:|
| A_single_agent | single_agent | False | 6 | 100.0% | 1.104s | 1.044s | 15819.2 | 0.0% |
| B_multi_agent | multi_agent | False | 6 | 100.0% | 1.112s | 1.045s | 19085.2 | 0.0% |
| C_multi_agent_memory | multi_agent | True | 6 | 100.0% | 1.135s | 1.078s | 20098.3 | 0.0% |

Initial shared memory entries before evaluation: 39. Learning tasks are excluded from the evaluation aggregates.

With the deterministic fixture policy, memory is not used to choose answers, so any completion difference is plumbing/noise rather than evidence of real-LLM learning. Statistical significance is not claimed.
