"""CLI smoke tests executed as real subprocesses."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_cli(tmp_path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT / "src") + os.pathsep + env.get(
        "PYTHONPATH", ""
    )
    env["PYTHONIOENCODING"] = "utf-8"
    env["COLUMNS"] = "200"
    env["LINES"] = "60"
    env["AGENTFORGE_LLM_PROVIDER"] = "mock"
    env.pop("AGENTFORGE_DEEPSEEK_API_KEY", None)
    env.pop("DEEPSEEK_API_KEY", None)
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "agentforge",
            "--data-dir",
            str(tmp_path / "cli-data"),
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


def test_cli_demo_history_and_chat(tmp_path):
    demo = run_cli(tmp_path, "demo")
    assert demo.returncode == 0, demo.stderr
    assert "AgentForge Phase 1 Demo" in demo.stdout
    assert "MOCK provider" in demo.stdout
    assert "Task completion rate" in demo.stdout
    assert "100.0%" in demo.stdout
    assert "Final Report" in demo.stdout
    assert "Persistence and observability" in demo.stdout

    history = run_cli(tmp_path, "history")
    assert history.returncode == 0, history.stderr
    assert "completed" in history.stdout
    assert "Workflow history" in history.stdout

    chat = run_cli(tmp_path, "chat", "research", "-m", "What are you good at?")
    assert chat.returncode == 0, chat.stderr
    assert "ResearchAgent here" in chat.stdout

    tasks = run_cli(tmp_path, "tasks")
    assert tasks.returncode == 0, tasks.stderr
    assert "finalize" in tasks.stdout

    messages = run_cli(tmp_path, "messages", "--all")
    assert messages.returncode == 0, messages.stderr
    assert "plan" in messages.stdout


def test_cli_agents_needs_no_database(tmp_path):
    result = run_cli(tmp_path, "agents")
    assert result.returncode == 0, result.stderr
    assert "CoordinatorAgent" in result.stdout
    assert "ResearchAgent" in result.stdout
    assert "ReviewerAgent" in result.stdout
