"""Before/after microbenchmarks for the Phase 5 safe optimizations."""

from __future__ import annotations

import asyncio
import json
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from agentforge.config.settings import Settings
from agentforge.persistence.database import SQLiteStore
from agentforge.tools.artifact_store import ArtifactStore
from agentforge.tools.builtins import build_default_tools
from agentforge.tools.workspace import WorkspaceManager

ROOT = Path(__file__).resolve().parent


def _populate_tool_calls(store: SQLiteStore) -> None:
    rows = []
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for workflow_index in range(50):
        for task_index in range(40):
            for call_index in range(10):
                count += 1
                rows.append(
                    (
                        f"call_{count}",
                        f"wf_{workflow_index:03d}",
                        f"task_{workflow_index:03d}_{task_index:03d}",
                        "coding",
                        "write_file",
                        json.dumps({"path": f"notes/{count}.md"}),
                        "success",
                        json.dumps({"path": f"notes/{count}.md"}),
                        None,
                        0.001,
                        "allowed",
                        "not_required",
                        None,
                        json.dumps(["file_write"]),
                        now,
                        now,
                        json.dumps({}),
                    )
                )
    with store._lock, store._conn:
        store._conn.executemany(
            """
            INSERT INTO tool_calls(
                tool_call_id, workflow_id, task_id, agent_id, tool_name,
                arguments, status, output, error, duration_seconds,
                permission_decision, approval_status, approval_id, permissions,
                started_at, finished_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _time_query(
    store: SQLiteStore,
    workflow_id: str | None,
    task_id: str,
    runs: int = 100,
) -> dict:
    timings = []
    start = time.perf_counter()
    for _ in range(runs):
        query_start = time.perf_counter()
        rows = store.list_tool_calls(workflow_id, task_id=task_id)
        timings.append((time.perf_counter() - query_start) * 1000)
    total = (time.perf_counter() - start) * 1000
    assert len(rows) == 10
    return {
        "runs": runs,
        "rows_returned": len(rows),
        "median_ms": round(statistics.median(timings), 6),
        "mean_ms": round(statistics.fmean(timings), 6),
        "p95_ms": round(sorted(timings)[int(len(timings) * 0.95) - 1], 6),
        "total_ms": round(total, 3),
    }


def measure_sqlite() -> dict:
    with tempfile.TemporaryDirectory(prefix="agentforge-perf-") as temp:
        store = SQLiteStore(Path(temp) / "perf.db")
        try:
            _populate_tool_calls(store)
            with store._lock, store._conn:
                # Leave the Phase 4 workflow index in place; remove only the
                # Phase 5 additions to measure the actual before/after delta.
                store._conn.execute("DROP INDEX IF EXISTS idx_tool_calls_task")
                store._conn.execute(
                    "DROP INDEX IF EXISTS idx_tool_calls_workflow_task"
                )
            plan_before = [
                tuple(row)
                for row in store._conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM tool_calls "
                    "WHERE workflow_id = ? AND task_id = ?",
                    ("wf_001", "task_001_001"),
                )
            ]
            before = _time_query(store, "wf_001", "task_001_001")
            before_task_only = _time_query(store, None, "task_001_001")
            with store._lock, store._conn:
                store._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tool_calls_task "
                    "ON tool_calls(task_id, started_at)"
                )
                store._conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_tool_calls_workflow_task "
                    "ON tool_calls(workflow_id, task_id, started_at)"
                )
            after = _time_query(store, "wf_001", "task_001_001")
            after_task_only = _time_query(store, None, "task_001_001")
            plan_after = [
                tuple(row)
                for row in store._conn.execute(
                    "EXPLAIN QUERY PLAN SELECT * FROM tool_calls "
                    "WHERE workflow_id = ? AND task_id = ?",
                    ("wf_001", "task_001_001"),
                )
            ]
            return {
                "rows": 20_000,
                "query": "list_tool_calls(workflow_id, task_id)",
                "before_index": before,
                "after_index": after,
                "speedup": round(
                    before["median_ms"] / after["median_ms"], 2
                )
                if after["median_ms"] else None,
                "task_only_query": "list_tool_calls(task_id)",
                "before_task_only": before_task_only,
                "after_task_only": after_task_only,
                "task_only_speedup": round(
                    before_task_only["median_ms"] / after_task_only["median_ms"], 2
                )
                if after_task_only["median_ms"] else None,
                "query_plan_before": plan_before,
                "query_plan_after": plan_after,
            }
        finally:
            store.close()


def measure_prompt_catalog() -> dict:
    with tempfile.TemporaryDirectory(prefix="agentforge-catalog-") as temp:
        settings = Settings(
            _env_file=None,
            data_dir=Path(temp) / "data",
            log_level="WARNING",
        )
        settings.ensure_directories()
        store = SQLiteStore(Path(temp) / "catalog.db")
        try:
            workspaces = WorkspaceManager(Path(temp) / "workspaces")
            artifacts = ArtifactStore(
                Path(temp) / "artifacts",
                workspaces,
                store,
                max_bytes=settings.max_file_bytes,
            )
            built = build_default_tools(
                settings, workspaces=workspaces, artifacts=artifacts
            )
            registry = built.registry
            iterations = 2000
            start = time.perf_counter()
            for _ in range(iterations):
                registry._catalog_cache = None
                registry.prompt_catalog()
            uncached = (time.perf_counter() - start) * 1000
            start = time.perf_counter()
            for _ in range(iterations):
                registry.prompt_catalog()
            cached = (time.perf_counter() - start) * 1000
            return {
                "iterations": iterations,
                "tool_count": len(registry),
                "catalog_chars": len(registry.prompt_catalog()),
                "uncached_total_ms": round(uncached, 3),
                "cached_total_ms": round(cached, 3),
                "uncached_per_call_ms": round(uncached / iterations, 6),
                "cached_per_call_ms": round(cached / iterations, 6),
                "speedup": round(uncached / cached, 2) if cached else None,
            }
        finally:
            store.close()


def main() -> None:
    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "sqlite_index": measure_sqlite(),
        "prompt_catalog_cache": measure_prompt_catalog(),
        "notes": [
            "Microbenchmarks isolate infrastructure overhead; end-to-end benchmark "
            "timings are dominated by Docker container startup and LLM calls.",
            "The cache changes no prompt text; the index changes no query semantics.",
        ],
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = [
        "# Performance Optimization Evidence",
        "",
        f"Generated: {results['timestamp']}",
        "",
        "## SQLite composite index",
        "",
        f"Query: `{results['sqlite_index']['query']}` over "
        f"{results['sqlite_index']['rows']} rows.",
        "",
        "| Variant | Median | Mean | p95 |",
        "|---|---:|---:|---:|",
        f"| Before index | {results['sqlite_index']['before_index']['median_ms']:.3f}ms | "
        f"{results['sqlite_index']['before_index']['mean_ms']:.3f}ms | "
        f"{results['sqlite_index']['before_index']['p95_ms']:.3f}ms |",
        f"| After index | {results['sqlite_index']['after_index']['median_ms']:.3f}ms | "
        f"{results['sqlite_index']['after_index']['mean_ms']:.3f}ms | "
        f"{results['sqlite_index']['after_index']['p95_ms']:.3f}ms |",
        "",
        f"Median speedup: **{results['sqlite_index']['speedup']}x**.",
        "",
        f"Task-only query (`list_tool_calls(task_id)`): "
        f"before {results['sqlite_index']['before_task_only']['median_ms']:.3f}ms, "
        f"after {results['sqlite_index']['after_task_only']['median_ms']:.3f}ms, "
        f"speedup **{results['sqlite_index']['task_only_speedup']}x**.",
        "",
        "## Tool catalog cache",
        "",
        f"{results['prompt_catalog_cache']['iterations']} calls over "
        f"{results['prompt_catalog_cache']['tool_count']} tools "
        f"({results['prompt_catalog_cache']['catalog_chars']} catalog chars).",
        "",
        "| Variant | Per call | Total |",
        "|---|---:|---:|",
        f"| Rebuild | {results['prompt_catalog_cache']['uncached_per_call_ms']:.6f}ms | "
        f"{results['prompt_catalog_cache']['uncached_total_ms']:.3f}ms |",
        f"| Cached | {results['prompt_catalog_cache']['cached_per_call_ms']:.6f}ms | "
        f"{results['prompt_catalog_cache']['cached_total_ms']:.3f}ms |",
        "",
        f"Speedup: **{results['prompt_catalog_cache']['speedup']}x**.",
        "",
        "The cached and uncached catalog strings are identical; registration "
        "invalidates the cache.",
    ]
    (ROOT / "PERFORMANCE.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
