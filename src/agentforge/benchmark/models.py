"""Typed benchmark contracts for AgentForge Phase 5."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from agentforge.models.common import new_id, utc_now
from agentforge.models.task import SubtaskSpec
from agentforge.models.tool import (
    ApprovalStatus,
    PermissionDecision,
    ToolResultStatus,
)
from agentforge.models.workflow import WorkflowStatus


class TaskCategory(str, Enum):
    RESEARCH = "research"
    CODE_GENERATION = "code_generation"
    DEBUGGING = "debugging"
    FILE_EDITING = "file_editing"
    PLANNING = "planning"
    DELEGATION = "delegation"
    CODE_EXECUTION = "code_execution"
    APPROVAL = "approval"
    CLARIFICATION = "clarification"
    FAILURE_RECOVERY = "failure_recovery"


class Difficulty(str, Enum):
    BASIC = "basic"
    HARD = "hard"


class VerificationStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"


class RunStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    ERROR = "error"


class BenchmarkMode(str, Enum):
    MULTI_AGENT = "multi_agent"
    SINGLE_AGENT = "single_agent"


class ProviderKind(str, Enum):
    FIXTURE = "fixture"
    LIVE = "live"


class WorkspaceSeed(BaseModel):
    agent: str = "coding"
    path: str
    content: str


class FileCheck(BaseModel):
    agent: str = "coding"
    path: str
    must_exist: bool = True
    must_not_exist: bool = False
    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    min_bytes: int = Field(default=0, ge=0)
    exact_content: str | None = None
    case_sensitive: bool = True


class ToolCallCheck(BaseModel):
    tool_name: str | None = None
    status: ToolResultStatus | None = None
    permission_decision: PermissionDecision | None = None
    approval_status: ApprovalStatus | None = None
    min_count: int = Field(default=0, ge=0)
    max_count: int | None = Field(default=None, ge=0)
    error_contains: str | None = None
    output_contains: str | None = None
    metadata_reason: str | None = None


class WorkflowCheck(BaseModel):
    status: WorkflowStatus | None = None
    final_contains: list[str] = Field(default_factory=list)
    final_not_contains: list[str] = Field(default_factory=list)
    error_contains: str | None = None
    min_pauses: int = 0
    min_resumes: int = 0
    min_cancellations: int = 0


class ApprovalCheck(BaseModel):
    tool_name: str | None = None
    status: ApprovalStatus | None = None
    min_count: int = Field(default=0, ge=0)
    max_count: int | None = Field(default=None, ge=0)
    consumed: bool | None = None


class ClarificationCheck(BaseModel):
    status: str | None = None
    min_count: int = Field(default=0, ge=0)
    answer_contains: str | None = None


class TaskGraphCheck(BaseModel):
    min_tasks: int = 0
    min_completed: int = 0
    min_failed: int = 0
    min_delegated_tasks: int = 0
    min_max_depth: int = 0
    min_reassignments: int = 0
    all_completed: bool = False


class PythonCheck(BaseModel):
    agent: str = "coding"
    source_path: str
    test_code: str
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    description: str = ""


class DuplicateEffectCheck(BaseModel):
    """Reject repeated successful effects for the same tool + arguments."""

    max_per_signature: int = Field(default=1, ge=1)
    tools: list[str] = Field(default_factory=list)


class VerificationSpec(BaseModel):
    files: list[FileCheck] = Field(default_factory=list)
    tool_calls: list[ToolCallCheck] = Field(default_factory=list)
    workflow: WorkflowCheck | None = None
    approvals: list[ApprovalCheck] = Field(default_factory=list)
    clarifications: list[ClarificationCheck] = Field(default_factory=list)
    task_graph: TaskGraphCheck | None = None
    python: list[PythonCheck] = Field(default_factory=list)
    duplicate_effects: DuplicateEffectCheck | None = None
    manual_review_reason: str | None = None

    def has_objective_checks(self) -> bool:
        return any(
            (
                self.files,
                self.tool_calls,
                self.workflow,
                self.approvals,
                self.clarifications,
                self.task_graph,
                self.python,
                self.duplicate_effects,
            )
        )


class MockAction(BaseModel):
    """Deterministic fixture action for harness validation only.

    This is never used for live-provider acceptance. ``kind`` matches the task
    kind in ``execute:<kind>:<role>``. Conditions are evaluated against the
    rendered prompt, so the same fixture also works in single-agent mode where
    the role differs.
    """

    kind: str = "coding"
    after_tool_results: int | None = None
    clarification_answered: bool | None = None
    delegated_results: bool | None = None
    prompt_contains: str | None = None
    action: dict[str, Any]


class ProviderBehavior(BaseModel):
    pause_on_kind: str | None = None
    cancel_on_kind: str | None = None


class HumanScript(BaseModel):
    approval_decision: Literal[
        "approve", "reject", "wait_expire", "leave_pending"
    ] = "approve"
    approval_comment: str = "simulated human decision"
    clarification_answer: str | None = None
    max_interactions: int = Field(default=6, ge=1, le=20)


class BenchmarkTask(BaseModel):
    task_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    title: str
    category: TaskCategory
    difficulty: Difficulty
    goal: str = Field(min_length=1)
    description: str = ""
    initial_workspace: list[WorkspaceSeed] = Field(default_factory=list)
    available_agents: list[str] = Field(
        default_factory=lambda: ["coordinator", "research", "coding", "reviewer"]
    )
    allowed_tools: list[str] | None = None
    plan: list[SubtaskSpec] = Field(default_factory=list)
    time_budget_seconds: float = Field(default=120.0, gt=0)
    token_budget: int = Field(default=200_000, ge=1)
    tool_budget: int = Field(default=20, ge=1)
    max_delegation_depth: int = Field(default=2, ge=0, le=10)
    max_execution_steps: int = Field(default=30, ge=1, le=200)
    expected_artifacts: list[str] = Field(default_factory=list)
    success_criteria: str = ""
    verification_method: str = "objective_checks"
    ground_truth: dict[str, Any] = Field(default_factory=dict)
    reference_checks: list[str] = Field(default_factory=list)
    verification: VerificationSpec
    human_script: HumanScript | None = None
    mock_actions: list[MockAction] = Field(default_factory=list)
    behavior: ProviderBehavior | None = None
    tags: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_verification(self) -> "BenchmarkTask":
        if not self.verification.has_objective_checks() and not (
            self.verification.manual_review_reason
        ):
            raise ValueError(
                f"task {self.task_id} has no objective checks or manual review reason"
            )
        return self


class BenchmarkDataset(BaseModel):
    dataset_id: str
    version: str = "1.0"
    description: str = ""
    tasks: list[BenchmarkTask] = Field(min_length=1)
    source_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_ids(self) -> "BenchmarkDataset":
        ids = [task.task_id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark task ids must be unique")
        return self


class BenchmarkConfig(BaseModel):
    dataset_path: str
    output_dir: str = "benchmarks/runs"
    mode: BenchmarkMode = BenchmarkMode.MULTI_AGENT
    provider_kind: ProviderKind = ProviderKind.FIXTURE
    provider_name: str = "mock"
    model: str | None = None
    task_ids: list[str] = Field(default_factory=list)
    categories: list[TaskCategory] = Field(default_factory=list)
    repeats: int = Field(default=1, ge=1, le=20)
    max_tasks: int | None = Field(default=None, ge=1)
    resume: bool = False
    batch_dir: str | None = None
    live_api_allowed: bool = False
    memory_evolution: bool = False
    memory_recall: bool = False
    shared_memory_file: str | None = None
    learning_task_ids: list[str] = Field(default_factory=list)
    fail_fast: bool = False
    verbose: bool = False


class CheckResult(BaseModel):
    name: str
    passed: bool | None = None
    detail: str = ""


class VerificationResult(BaseModel):
    status: VerificationStatus
    checks: list[CheckResult] = Field(default_factory=list)
    failure_reason: str | None = None
    manual_review_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class HumanInteractionRecord(BaseModel):
    kind: Literal["approval", "clarification", "resume"]
    target_id: str
    decision: str
    simulated: bool = True
    wait_seconds: float = 0.0
    created_at: datetime = Field(default_factory=utc_now)


class RunMetrics(BaseModel):
    """Per-task metrics with explicit units and definitions.

    ``end_to_end_seconds`` is monotonic wall-clock. ``active_seconds`` excludes
    scripted human wait. ``human_wait_seconds`` is simulated human latency only.
    """

    goal_success: bool = False
    workflow_completed: bool = False
    subtask_completion_rate: float = 0.0
    end_to_end_seconds: float = 0.0
    active_seconds: float = 0.0
    human_wait_seconds: float = 0.0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    tool_failure_rate: float = 0.0
    retry_count: int = 0
    delegation_count: int = 0
    delegation_success_rate: float = 0.0
    memory_retrieval_count: int = 0
    memory_usage_count: int = 0
    approval_count: int = 0
    clarification_count: int = 0
    recovery_success: bool = False
    workflow_crash_or_timeout: bool = False
    tool_budget_exceeded: bool = False
    token_budget_exceeded: bool = False
    time_budget_exceeded: bool = False
    invalid_json_rate: float = 0.0
    tool_schema_error_rate: float = 0.0
    plan_validation_failure_rate: float = 0.0
    tool_calling_success_rate: float = 0.0
    agent_completion_rate: float = 0.0


class FailureAnalysis(BaseModel):
    category: str = "none"
    reason: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    retryable: bool = False


class BenchmarkRun(BaseModel):
    run_id: str = Field(default_factory=lambda: new_id("run"))
    batch_id: str
    task_id: str
    title: str
    category: TaskCategory
    difficulty: Difficulty
    repeat_index: int
    mode: BenchmarkMode
    provider_kind: ProviderKind
    provider_name: str
    model: str
    git_commit: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    run_status: RunStatus
    verification: VerificationResult | None = None
    workflow_id: str | None = None
    workflow_status: WorkflowStatus | None = None
    workflow_error: str | None = None
    final_result_preview: str = ""
    metrics: RunMetrics = Field(default_factory=RunMetrics)
    failure: FailureAnalysis | None = None
    human_interactions: list[HumanInteractionRecord] = Field(default_factory=list)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class BenchmarkBatch(BaseModel):
    batch_id: str = Field(default_factory=lambda: new_id("batch"))
    dataset_id: str
    dataset_path: str
    git_commit: str | None = None
    config: BenchmarkConfig
    task_ids: list[str] = Field(default_factory=list)
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    status: Literal["running", "completed", "failed", "interrupted"] = "running"
    run_count: int = 0
    notes: str = ""


class CategoryAggregate(BaseModel):
    category: TaskCategory
    total: int = 0
    passed: int = 0
    failed: int = 0
    needs_review: int = 0
    skipped: int = 0
    success_rate: float = 0.0
    average_end_to_end_seconds: float = 0.0
    average_active_seconds: float = 0.0
    average_tool_calls: float = 0.0
    average_llm_calls: float = 0.0


class AggregateMetrics(BaseModel):
    total_runs: int = 0
    passed: int = 0
    failed: int = 0
    needs_review: int = 0
    skipped: int = 0
    timeout: int = 0
    errors: int = 0
    goal_completion_rate: float = 0.0
    average_end_to_end_seconds: float = 0.0
    median_end_to_end_seconds: float = 0.0
    p95_end_to_end_seconds: float = 0.0
    average_active_seconds: float = 0.0
    total_human_wait_seconds: float = 0.0
    total_llm_calls: int = 0
    total_tokens: int = 0
    average_tokens: float = 0.0
    total_tool_calls: int = 0
    tool_failure_rate: float = 0.0
    total_retries: int = 0
    total_delegations: int = 0
    delegation_success_rate: float = 0.0
    total_memory_retrievals: int = 0
    total_memory_usage: int = 0
    total_approvals: int = 0
    total_clarifications: int = 0
    recovery_success_rate: float = 0.0
    crash_or_timeout_rate: float = 0.0
    by_category: list[CategoryAggregate] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AblationConfig(BaseModel):
    name: str
    mode: BenchmarkMode
    memory: bool
    description: str = ""


class AblationResult(BaseModel):
    ablation_id: str = Field(default_factory=lambda: new_id("ablation"))
    dataset_id: str
    task_ids: list[str]
    learning_task_ids: list[str] = Field(default_factory=list)
    repeats: int = 1
    created_at: datetime = Field(default_factory=utc_now)
    configs: list[AblationConfig] = Field(default_factory=list)
    aggregates: dict[str, AggregateMetrics] = Field(default_factory=dict)
    runs: dict[str, list[BenchmarkRun]] = Field(default_factory=dict)
    initial_memory_entries: int = 0
    notes: list[str] = Field(default_factory=list)


class LiveCheck(BaseModel):
    check_id: str
    description: str
    status: Literal["NOT_VERIFIED", "PASSED", "FAILED", "SKIPPED"] = "NOT_VERIFIED"
    detail: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)


class LiveAcceptanceReport(BaseModel):
    provider: str
    model: str
    api_key_configured: bool = False
    live_api_allowed: bool = False
    checked_at: datetime = Field(default_factory=utc_now)
    status: Literal["NOT_VERIFIED", "PASSED", "FAILED", "PARTIAL"] = "NOT_VERIFIED"
    checks: list[LiveCheck] = Field(default_factory=list)
    harness_validation: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
