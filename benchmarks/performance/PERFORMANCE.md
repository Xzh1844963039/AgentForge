# Performance Optimization Evidence

Generated: 2026-11-08T16:33:23.450085+00:00

## SQLite composite index

Query: `list_tool_calls(workflow_id, task_id)` over 20000 rows.

| Variant | Median | Mean | p95 |
|---|---:|---:|---:|
| Before index | 0.310ms | 0.338ms | 0.439ms |
| After index | 0.241ms | 0.247ms | 0.257ms |

Median speedup: **1.29x**.

Task-only query (`list_tool_calls(task_id)`): before 15.353ms, after 0.241ms, speedup **63.82x**.

## Tool catalog cache

2000 calls over 13 tools (6669 catalog chars).

| Variant | Per call | Total |
|---|---:|---:|
| Rebuild | 0.129774ms | 259.548ms |
| Cached | 0.000116ms | 0.231ms |

Speedup: **1122.13x**.

The cached and uncached catalog strings are identical; registration invalidates the cache.
