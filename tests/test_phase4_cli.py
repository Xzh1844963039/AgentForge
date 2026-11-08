"""Phase 4 CLI lifecycle and human-interaction command tests."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

from agentforge.config.settings import Settings
from agentforge.interaction.approval_service import ApprovalService
from agentforge.interaction.clarification_service import ClarificationService
from agentforge.messaging.conversation import ConversationManager
from agentforge.models.common import utc_now
from agentforge.models.task import Task, TaskStatus
from agentforge.models.tool import ApprovalRequest
from agentforge.models.workflow import Workflow, WorkflowStatus
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger
from agentforge.tools.approval import arguments_digest

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


def seed_cli_state(tmp_path):
    settings = Settings(
        _env_file=None,
        llm_provider="mock",
        data_dir=tmp_path / "cli-data",
        log_level="WARNING",
        mock_latency_ms=0,
    )
    settings.ensure_directories()
    store = SQLiteStore(settings.resolved_sqlite_file)
    events = EventLogger(settings.resolved_events_file)
    conversations = ConversationManager(store)
    conversation = conversations.create_group(
        "Phase 4 CLI test",
        agent_ids=["coordinator", "coding", "reviewer"],
    )
    now = utc_now()

    interaction_workflow = Workflow(
        goal="Delete a stale note with human approval",
        conversation_id=conversation.conversation_id,
        status=WorkflowStatus.PAUSED,
        created_at=now,
        metadata={"phase": 4},
    )
    store.save_workflow(interaction_workflow)
    root = Task(
        workflow_id=interaction_workflow.workflow_id,
        description=interaction_workflow.goal,
        required_capabilities=[],
        assigned_agent="coordinator",
        status=TaskStatus.RUNNING,
        metadata={"role": "root"},
    )
    coding_task = Task(
        workflow_id=interaction_workflow.workflow_id,
        description="Delete stale.md after approval",
        required_capabilities=["coding"],
        assigned_agent="coding",
        status=TaskStatus.PENDING,
        metadata={
            "kind": "coding",
            "pending_approval_id": "approval-placeholder",
        },
    )
    store.save_task(root)
    store.save_task(coding_task)

    approval_service = ApprovalService(store, events)
    approval = ApprovalRequest(
        agent_id="coding",
        workflow_id=interaction_workflow.workflow_id,
        task_id=coding_task.task_id,
        tool_name="delete_file",
        action="delete_file",
        summary="Delete stale.md in the coding workspace.",
        arguments={"path": "stale.md"},
        argument_digest=arguments_digest({"path": "stale.md"}),
    )
    asyncio.run(approval_service.request_approval(approval))
    coding_task.metadata["pending_approval_id"] = approval.approval_id
    store.save_task(coding_task)

    clarification_service = ClarificationService(store, events)
    clarification = clarification_service.create(
        workflow_id=interaction_workflow.workflow_id,
        task_id=coding_task.task_id,
        agent_id="coding",
        question="Should the stale note be archived or deleted?",
        context="The goal says clean up but not how.",
        possible_interpretations=["archive", "delete"],
    )

    resume_workflow = Workflow(
        goal="Resume a retained workflow",
        conversation_id=conversation.conversation_id,
        status=WorkflowStatus.PAUSED,
        created_at=now,
        metadata={"phase": 4},
    )
    final_task = Task(
        workflow_id=resume_workflow.workflow_id,
        description="Produce the final seeded result",
        required_capabilities=["finalization"],
        assigned_agent="coordinator",
        status=TaskStatus.COMPLETED,
        result="Seeded final result: recovered from persistence.",
        metadata={"kind": "finalize"},
    )
    resume_root = Task(
        workflow_id=resume_workflow.workflow_id,
        description=resume_workflow.goal,
        required_capabilities=[],
        assigned_agent="coordinator",
        status=TaskStatus.RUNNING,
        metadata={"role": "root"},
    )
    resume_workflow.metadata["final_task_id"] = final_task.task_id
    store.save_workflow(resume_workflow)
    store.save_task(resume_root)
    store.save_task(final_task)

    cancel_workflow = Workflow(
        goal="Cancel a paused workflow",
        conversation_id=conversation.conversation_id,
        status=WorkflowStatus.PAUSED,
        created_at=now,
        metadata={"phase": 4},
    )
    cancel_root = Task(
        workflow_id=cancel_workflow.workflow_id,
        description=cancel_workflow.goal,
        required_capabilities=[],
        assigned_agent="coordinator",
        status=TaskStatus.RUNNING,
        metadata={"role": "root"},
    )
    cancel_work = Task(
        workflow_id=cancel_workflow.workflow_id,
        description="Never execute this work",
        required_capabilities=["coding"],
        assigned_agent="coding",
        status=TaskStatus.PENDING,
        metadata={"kind": "coding"},
    )
    store.save_workflow(cancel_workflow)
    store.save_task(cancel_root)
    store.save_task(cancel_work)

    events.close()
    store.close()
    return {
        "approval_id": approval.approval_id,
        "clarification_id": clarification.clarification_id,
        "interaction_workflow": interaction_workflow.workflow_id,
        "resume_workflow": resume_workflow.workflow_id,
        "cancel_workflow": cancel_workflow.workflow_id,
    }


def test_cli_approvals_clarification_and_workflow_inspection(tmp_path):
    ids = seed_cli_state(tmp_path)

    listed = run_cli(tmp_path, "approvals", "list")
    assert listed.returncode == 0, listed.stderr
    assert ids["approval_id"] in listed.stdout
    assert "delete_file" in listed.stdout
    assert "pending" in listed.stdout

    shown = run_cli(tmp_path, "approvals", "show", ids["approval_id"])
    assert shown.returncode == 0, shown.stderr
    assert "Argument digest" in shown.stdout
    assert "stale.md" in shown.stdout
    assert "Risk" in shown.stdout
    assert "Expires" in shown.stdout

    approved = run_cli(
        tmp_path, "approvals", "approve", ids["approval_id"], "--comment", "ok"
    )
    assert approved.returncode == 0, approved.stderr
    assert "Approved" in approved.stdout
    assert "workflows resume" in approved.stdout

    clarifications = run_cli(tmp_path, "clarification", "list")
    assert clarifications.returncode == 0, clarifications.stderr
    assert ids["clarification_id"] in clarifications.stdout
    assert "archived or deleted" in clarifications.stdout.lower()

    clarification_show = run_cli(
        tmp_path, "clarification", "show", ids["clarification_id"]
    )
    assert clarification_show.returncode == 0, clarification_show.stderr
    assert "archive" in clarification_show.stdout
    assert "delete" in clarification_show.stdout

    answered = run_cli(
        tmp_path,
        "clarification",
        "answer",
        ids["clarification_id"],
        "archive",
    )
    assert answered.returncode == 0, answered.stderr
    assert "Answered" in answered.stdout

    workflows = run_cli(tmp_path, "workflows", "list")
    assert workflows.returncode == 0, workflows.stderr
    assert ids["interaction_workflow"] in workflows.stdout
    assert "paused" in workflows.stdout

    detail = run_cli(
        tmp_path, "workflows", "show", ids["interaction_workflow"]
    )
    assert detail.returncode == 0, detail.stderr
    assert "Approvals" in detail.stdout
    assert "Clarifications" in detail.stdout
    assert "Lifecycle events" in detail.stdout


def test_cli_workflow_pause_resume_cancel(tmp_path):
    ids = seed_cli_state(tmp_path)

    paused = run_cli(tmp_path, "workflows", "pause", ids["interaction_workflow"])
    assert paused.returncode == 0, paused.stderr
    assert "is paused" in paused.stdout

    resumed = run_cli(tmp_path, "workflows", "resume", ids["resume_workflow"])
    assert resumed.returncode == 0, resumed.stderr
    assert "completed" in resumed.stdout
    assert "Seeded final result" in resumed.stdout

    cancelled = run_cli(
        tmp_path,
        "workflows",
        "cancel",
        ids["cancel_workflow"],
        "--reason",
        "operator stop",
    )
    assert cancelled.returncode == 0, cancelled.stderr
    assert "cancelled" in cancelled.stdout
    assert "cleaned up" in cancelled.stdout

    after = run_cli(tmp_path, "workflows", "show", ids["cancel_workflow"])
    assert after.returncode == 0, after.stderr
    assert "cancelled" in after.stdout
