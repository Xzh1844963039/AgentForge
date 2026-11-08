"""Memory CLI smoke tests (real subprocesses)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.models import (
    EvolvedMemory,
    MemoryType,
    PromotionState,
    VerificationLevel,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(data_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["COLUMNS"] = "200"
    env["AGENTFORGE_LLM_PROVIDER"] = "mock"
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agentforge",
            "--data-dir",
            str(data_dir),
            "--log-level",
            "WARNING",
            *args,
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def seed_memories(tmp_path: Path) -> Path:
    data_dir = tmp_path / "cli-memory"
    data_dir.mkdir(parents=True)
    store = EvolutionMemoryStore(data_dir / "memory_evolution.db")
    try:
        store.upsert_memory(
            EvolvedMemory(
                agent_id="coding",
                memory_type=MemoryType.PROCEDURAL,
                verification_level=VerificationLevel.REAL_EXECUTION,
                content=(
                    "Create and verify a note using write_file then read_file "
                    "with path notes/verified.md"
                ),
                importance=0.9,
                confidence=0.9,
                support_count=2,
                promotion_state=PromotionState.PROMOTED,
                dedupe_key="cli-real",
            )
        )
        store.upsert_memory(
            EvolvedMemory(
                agent_id="coding",
                memory_type=MemoryType.EPISODIC,
                verification_level=VerificationLevel.MOCK_SIMULATION,
                content="Mock note procedure",
                importance=0.3,
                confidence=0.3,
                dedupe_key="cli-mock",
            )
        )
        store.upsert_memory(
            EvolvedMemory(
                agent_id="coding",
                memory_type=MemoryType.EPISODIC,
                verification_level=VerificationLevel.UNVERIFIED,
                content="Unverified chat note",
                importance=0.2,
                confidence=0.15,
                dedupe_key="cli-unverified",
            )
        )
    finally:
        store.close()
    return data_dir


def test_memory_list_search_stats_clear(tmp_path):
    data_dir = seed_memories(tmp_path)

    listing = run_cli(data_dir, "memory", "list", "--agent", "coding")
    assert listing.returncode == 0, listing.stderr
    assert "notes/verified.md" in listing.stdout
    assert "REAL_EXECUTION" in listing.stdout
    assert "MOCK_SIMULATION" in listing.stdout

    search = run_cli(
        data_dir, "memory", "search", "create verify note", "--agent", "coding"
    )
    assert search.returncode == 0, search.stderr
    assert "notes/verified.md" in search.stdout
    assert "MOCK_SIMULATION" not in search.stdout
    assert "Unverified chat note" not in search.stdout

    stats = run_cli(data_dir, "memory", "stats", "--agent", "coding")
    assert stats.returncode == 0, stats.stderr
    assert "REAL_EXECUTION=1" in stats.stdout
    assert "MOCK_SIMULATION=1" in stats.stdout
    assert "UNVERIFIED=1" in stats.stdout

    refused = run_cli(data_dir, "memory", "clear", "--agent", "coding")
    assert refused.returncode == 2
    assert "Refusing to clear memory" in refused.stdout

    cleared = run_cli(
        data_dir, "memory", "clear", "--agent", "coding", "--confirm"
    )
    assert cleared.returncode == 0, cleared.stderr
    assert "evolved_memories=3" in cleared.stdout

    after = run_cli(data_dir, "memory", "stats", "--agent", "coding")
    assert "Total / active          │ 0 / 0" in after.stdout


def test_memory_experiment_cli(tmp_path):
    data_dir = tmp_path / "cli-experiment"
    result = run_cli(data_dir, "memory", "experiment")
    assert result.returncode == 0, result.stderr
    assert "Phase 3 Memory Experiment" in result.stdout
    assert "Action changed" in result.stdout
    assert "YES" in result.stdout
    assert "notes/verified.md" in result.stdout
    assert "notes/default.md" in result.stdout
    assert "REAL_EXECUTION" in result.stdout
    assert "Pipeline verified" in result.stdout
    assert "Real LLM learning effect" in result.stdout
    assert "NOT VERIFIED" in result.stdout
