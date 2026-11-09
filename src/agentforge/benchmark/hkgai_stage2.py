"""Bounded real Stage 2 acceptance: Coordinator → Agent → Tool → Evidence.

This runner executes one full AgentForge workflow through the real runtime with
the configured HKGAI provider. It enforces a hard request/token budget, disables
provider retries, never switches models, and stops immediately on a
pre-response disconnect. It only marks ``PASSED`` when every live check is
backed by persisted runtime state.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agentforge.agents.base import COMPACT_ACTION_PROTOCOL
from agentforge.agents.coordinator import COMPACT_PLANNER_PROTOCOL
from agentforge.config.settings import Settings
from agentforge.llm.base import (
    BaseLLMProvider,
    LLMProviderError,
    LLMResponse,
)
from agentforge.llm.factory import build_llm_provider
from agentforge.llm.hkgai import HKGAIProvider
from agentforge.models.common import utc_now
from agentforge.runtime import AgentForgeRuntime

MAX_REQUESTS = 12
MAX_TOKENS_PER_REQUEST = 1024
MAX_TOTAL_TOKENS = 20_000
MAX_TIMEOUT_SECONDS = 120.0

STAGE2_GOAL = (
    "Create and sandbox-execute a Python script named stage2_sum.py that prints "
    'exactly "STAGE2_SUM=5" by computing 2 + 3, then have the final report quote '
    "the recorded stdout and exit code from the tool audit."
)
EVIDENCE_MARKER = "STAGE2_SUM=5"


class Stage2BudgetExceeded(RuntimeError):
    def __init__(self, budget_type: str, limit: int, used: int) -> None:
        super().__init__(
            f"stage2 {budget_type} budget exceeded (limit={limit}, used={used})"
        )
        self.budget_type = budget_type


class Stage2Stopped(RuntimeError):
    """Raised after a pre-response disconnect; no further online call is made."""


class Stage2Call(BaseModel):
    index: int
    purpose: str
    started_at: datetime = Field(default_factory=utc_now)
    prompt_chars: int = 0
    requested_max_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    model: str | None = None
    finish_reason: str | None = None
    http_status: int | None = None
    content_preview: str = ""
    compact_planner_prompt: bool = False
    compact_action_prompt: bool = False
    tool_evidence_in_prompt: bool = False
    marker_in_prompt: bool = False
    status: str = "success"
    error_type: str | None = None
    error_detail: str | None = None


class Stage2Check(BaseModel):
    passed: bool
    detail: str


class Stage2Result(BaseModel):
    status: str = "NOT_VERIFIED"
    mode: str = "LIVE"
    configured_model: str = "unknown"
    workflow_id: str | None = None
    workflow_status: str | None = None
    goal: str = STAGE2_GOAL
    checks: dict[str, Stage2Check] = Field(default_factory=dict)
    plan_subtasks: int = 0
    plan_attempts: int = 0
    plan_summary: str | None = None
    max_plan_subtasks: int = 2
    review_enabled: bool = False
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    final_result_excerpt: str = ""
    calls: list[Stage2Call] = Field(default_factory=list)
    total_requests: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0
    hard_stopped: bool = False
    hard_stop_reason: str | None = None
    budget_exhausted: bool = False
    budget_max_requests: int = MAX_REQUESTS
    budget_max_tokens: int = MAX_TOTAL_TOKENS
    error_type: str | None = None
    error_detail: str | None = None
    output_dir: str = ""
    checked_at: datetime = Field(default_factory=utc_now)


def _is_no_response_disconnect(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)):
        return True
    if isinstance(exc, LLMProviderError) and exc.status_code is None:
        return True
    return False


class Stage2BudgetProvider(BaseLLMProvider):
    """Hard request/token budget with per-request max_tokens cap and stop-on-disconnect."""

    def __init__(
        self,
        provider: BaseLLMProvider,
        *,
        max_requests: int = MAX_REQUESTS,
        max_tokens: int = MAX_TOTAL_TOKENS,
        per_request_max_tokens: int = MAX_TOKENS_PER_REQUEST,
        expected_marker: str = EVIDENCE_MARKER,
    ) -> None:
        super().__init__(
            default_temperature=provider.default_temperature,
            default_max_tokens=min(
                provider.default_max_tokens, per_request_max_tokens
            ),
        )
        self.provider = provider
        self.name = provider.name
        self.model = provider.model
        self.max_requests = max(1, max_requests)
        self.max_tokens = max(1, max_tokens)
        self.per_request_max_tokens = max(1, per_request_max_tokens)
        self.expected_marker = expected_marker
        self.requests = 0
        self.tokens = 0
        self.calls: list[Stage2Call] = []
        self.hard_stopped = False
        self.hard_stop_reason: str | None = None
        self.budget_exhausted = False

    async def complete(
        self,
        messages,
        *,
        purpose: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        if self.hard_stopped:
            raise Stage2Stopped(self.hard_stop_reason or "stage2 stopped")
        if self.requests >= self.max_requests:
            self.budget_exhausted = True
            raise Stage2BudgetExceeded(
                "request", self.max_requests, self.requests
            )
        if self.tokens >= self.max_tokens:
            self.budget_exhausted = True
            raise Stage2BudgetExceeded("token", self.max_tokens, self.tokens)

        capped = min(
            max_tokens if max_tokens is not None else self.per_request_max_tokens,
            self.per_request_max_tokens,
        )
        prompt = "\n\n".join(message.content for message in messages)
        system = "\n".join(
            message.content for message in messages if message.role == "system"
        )
        call = Stage2Call(
            index=self.requests + 1,
            purpose=purpose or "generic",
            prompt_chars=len(prompt),
            requested_max_tokens=capped,
            compact_planner_prompt=COMPACT_PLANNER_PROTOCOL in prompt,
            compact_action_prompt=COMPACT_ACTION_PROTOCOL in system,
            tool_evidence_in_prompt="[TOOL_EVIDENCE]" in prompt,
            marker_in_prompt=self.expected_marker in prompt,
        )
        self.requests += 1
        started = time.monotonic()
        try:
            response = await self.provider.complete(
                messages,
                purpose=purpose,
                temperature=temperature,
                max_tokens=capped,
            )
        except BaseException as exc:
            call.elapsed_seconds = round(time.monotonic() - started, 3)
            call.status = "failed"
            call.error_type = type(exc).__name__
            call.error_detail = str(exc)[:1000]
            self.calls.append(call)
            if _is_no_response_disconnect(exc):
                self.hard_stopped = True
                self.hard_stop_reason = (
                    f"{type(exc).__name__} after {call.elapsed_seconds}s: "
                    f"{str(exc)[:300]}"
                )
            raise
        call.elapsed_seconds = round(time.monotonic() - started, 3)
        call.model = response.model
        call.finish_reason = response.finish_reason
        call.http_status = response.raw.get("http_status")
        call.content_preview = response.content[:300]
        usage = response.usage
        call.prompt_tokens = usage.prompt_tokens
        call.completion_tokens = usage.completion_tokens
        call.total_tokens = usage.total_tokens or (
            usage.prompt_tokens + usage.completion_tokens
        )
        self.tokens += call.total_tokens
        self.calls.append(call)
        return response

    async def aclose(self) -> None:
        return None

    async def close_underlying(self) -> None:
        await self.provider.aclose()

    def snapshot(self) -> dict[str, int | bool | str | None]:
        return {
            "requests": self.requests,
            "tokens": self.tokens,
            "max_requests": self.max_requests,
            "max_tokens": self.max_tokens,
            "per_request_max_tokens": self.per_request_max_tokens,
            "hard_stopped": self.hard_stopped,
            "hard_stop_reason": self.hard_stop_reason,
            "budget_exhausted": self.budget_exhausted,
        }


def _check(result: Stage2Result, key: str, passed: bool, detail: str) -> None:
    result.checks[key] = Stage2Check(passed=passed, detail=detail)


def evaluate_stage2(
    runtime: AgentForgeRuntime,
    workflow,
    provider: Stage2BudgetProvider,
    *,
    mode: str = "LIVE",
    provider_class_ok: bool = True,
    require_sandbox: bool = True,
    required_tool: str = "python_execute",
) -> Stage2Result:
    """Derive A–G checks from persisted runtime state, never from LLM claims."""
    result = Stage2Result(
        mode=mode,
        workflow_id=workflow.workflow_id,
        workflow_status=workflow.status.value,
        configured_model=provider.model,
        goal=workflow.goal,
    )
    store = runtime.store
    tasks = store.list_tasks(workflow.workflow_id)
    executions = store.list_executions(workflow.workflow_id)
    tool_calls = store.list_tool_calls(workflow_id=workflow.workflow_id)

    plan_tasks = [task for task in tasks if task.metadata.get("origin") == "plan"]
    plan_executions = [item for item in executions if item.purpose == "plan"]
    action_executions = [
        item for item in executions if item.purpose.startswith("execute:")
    ]

    # A — real provider and compact protocols actually used by the runtime.
    coordinator = runtime.get_agent("coordinator")
    providers_used = {call.model for call in provider.calls if call.model}
    plan_call = next(
        (call for call in provider.calls if call.purpose == "plan"), None
    )
    action_calls = [
        call for call in provider.calls if call.purpose.startswith("execute:")
    ]
    provider_ok = (
        provider.name == "hkgai"
        and provider_class_ok
        and coordinator.planner_protocol == "compact"
        and coordinator.agent_protocol == "compact"
        and plan_call is not None
        and plan_call.compact_planner_prompt
        and all(call.compact_action_prompt for call in action_calls)
    )
    _check(
        result,
        "A_real_hkgai_compact_prompts",
        provider_ok,
        f"provider={provider.name}, provider_class_ok={provider_class_ok}, "
        f"models={sorted(providers_used)}, planner={coordinator.planner_protocol}, "
        f"agent={coordinator.agent_protocol}, "
        f"plan_compact_prompt={bool(plan_call and plan_call.compact_planner_prompt)}, "
        f"action_calls={len(action_calls)}",
    )

    # B — Coordinator returned a plan that passed ExecutionPlan + validate_plan.
    plan_ok = (
        bool(plan_tasks)
        and bool(workflow.metadata.get("plan_summary"))
        and bool(workflow.metadata.get("final_task_id"))
        and all(item.status.value == "completed" for item in plan_executions)
    )
    result.plan_subtasks = len(plan_tasks)
    result.plan_attempts = len(plan_executions)
    result.plan_summary = workflow.metadata.get("plan_summary")
    _check(
        result,
        "B_plan_validated",
        plan_ok,
        f"plan_subtasks={len(plan_tasks)}, plan_attempts={len(plan_executions)}, "
        f"plan_errors={sum(1 for item in plan_executions if item.error)}",
    )

    # C — capability dispatcher assigned the plan tasks to matching agents.
    assignments: list[dict[str, Any]] = []
    dispatch_ok = bool(plan_tasks)
    for task in plan_tasks:
        score = float(task.metadata.get("assignment_score", 0.0))
        assignments.append(
            {
                "task_id": task.task_id,
                "plan_key": task.metadata.get("plan_key"),
                "kind": task.metadata.get("kind"),
                "required_capabilities": task.required_capabilities,
                "assigned_agent": task.assigned_agent,
                "assignment_score": score,
                "matched_capabilities": task.metadata.get("matched_capabilities"),
                "status": task.status.value,
            }
        )
        dispatch_ok = dispatch_ok and bool(task.assigned_agent) and score >= 1.0
    result.assignments = assignments
    _check(
        result,
        "C_capability_dispatch",
        dispatch_ok,
        f"assigned={[item['assigned_agent'] for item in assignments]}",
    )

    # D — a real executing agent produced a completed structured action.
    completed_action_executions = [
        item
        for item in action_executions
        if item.status.value == "completed" and not item.purpose.startswith("execute:synthesis")
        and not item.purpose.startswith("execute:review")
        and not item.purpose.startswith("execute:finalize")
    ]
    completed_plan_tasks = [
        task
        for task in plan_tasks
        if task.status.value == "completed" and (task.result or "").strip()
    ]
    action_ok = bool(completed_action_executions) and bool(completed_plan_tasks)
    _check(
        result,
        "D_agent_structured_action",
        action_ok,
        f"executing_agent_calls={len(completed_action_executions)}, "
        f"completed_plan_tasks={len(completed_plan_tasks)}",
    )

    # E — dispatcher schema/permission validation: a successful call proves both.
    successful_calls = [record for record in tool_calls if record.status.value == "success"]
    result.tool_calls = [
        {
            "tool_name": record.tool_name,
            "agent_id": record.agent_id,
            "status": record.status.value,
            "error": record.error,
            "duration_seconds": record.duration_seconds,
            "output_keys": sorted((record.output or {}).keys()),
            "container": (record.metadata or {}).get("container"),
        }
        for record in tool_calls
    ]
    _check(
        result,
        "E_action_schema_permission_validation",
        bool(successful_calls),
        f"successful_tool_calls={len(successful_calls)}/{len(tool_calls)}",
    )

    # F — authorized tool executed with recorded output; sandbox in live mode.
    tool_records = [
        record
        for record in tool_calls
        if record.tool_name == required_tool and record.status.value == "success"
    ]
    tool_ok = False
    if require_sandbox:
        tool_detail = f"no successful {required_tool} record"
        if tool_records:
            record = tool_records[-1]
            output = record.output or {}
            exit_code = output.get("exit_code")
            stdout = str(output.get("stdout") or "")
            container = (record.metadata or {}).get("container")
            tool_ok = (
                exit_code == 0
                and EVIDENCE_MARKER in stdout
                and bool(container)
            )
            tool_detail = (
                f"exit_code={exit_code}, marker_in_stdout={EVIDENCE_MARKER in stdout}, "
                f"container={container}, duration={record.duration_seconds}s"
            )
    else:
        tool_detail = f"no successful {required_tool} record"
        if tool_records:
            record = tool_records[-1]
            output_text = json.dumps(record.output or {}, ensure_ascii=False)
            tool_ok = EVIDENCE_MARKER in output_text
            tool_detail = (
                f"output_contains_marker={EVIDENCE_MARKER in output_text}, "
                f"duration={record.duration_seconds}s"
            )
    _check(
        result,
        "F_sandbox_tool_execution" if require_sandbox else "F_tool_execution",
        tool_ok,
        tool_detail,
    )

    # G — recorded tool evidence reached synthesis/finalize and the final report.
    policy_calls = [
        call
        for call in provider.calls
        if call.purpose.startswith("execute:synthesis")
        or call.purpose.startswith("execute:finalize")
    ]
    evidence_in_prompt = any(
        call.tool_evidence_in_prompt and call.marker_in_prompt for call in policy_calls
    )
    final_task = next(
        (
            task
            for task in tasks
            if task.task_id == workflow.metadata.get("final_task_id")
        ),
        None,
    )
    final_result = (final_task.result if final_task else "") or ""
    marker_in_final = EVIDENCE_MARKER in final_result
    result.final_result_excerpt = final_result[:1200]
    _check(
        result,
        "G_tool_evidence_in_final_report",
        evidence_in_prompt and marker_in_final,
        f"evidence_in_prompt={evidence_in_prompt}, "
        f"marker_in_final_report={marker_in_final}, "
        f"final_chars={len(final_result)}",
    )

    result.total_requests = provider.requests
    result.total_tokens = provider.tokens
    result.hard_stopped = provider.hard_stopped
    result.hard_stop_reason = provider.hard_stop_reason
    result.budget_exhausted = provider.budget_exhausted
    result.calls = provider.calls

    all_passed = all(check.passed for check in result.checks.values())
    if provider.hard_stopped:
        result.status = "FAILED"
    elif all_passed and workflow.status.value == "completed":
        result.status = "PASSED" if mode == "LIVE" else "MOCK_VALIDATED"
    elif any(check.passed for check in result.checks.values()):
        result.status = "PARTIALLY_VERIFIED"
    else:
        result.status = "FAILED"
    return result


async def run_hkgai_stage2(
    settings: Settings,
    *,
    allow_live_api: bool,
    output_dir: str | Path = "benchmarks/runs",
    timeout: float = 120.0,
    provider_override: BaseLLMProvider | None = None,
    require_sandbox: bool = True,
    required_tool: str = "python_execute",
    enable_review: bool = False,
    max_plan_subtasks: int = 2,
    max_requests: int = MAX_REQUESTS,
    max_tokens: int = MAX_TOTAL_TOKENS,
    structured_output_repairs: int = 2,
) -> Stage2Result:
    """Run the bounded real Stage 2 workflow and persist its evidence."""
    live = provider_override is None
    if live:
        if not allow_live_api:
            raise PermissionError(
                "Stage 2 requires explicit --allow-live-api"
            )
        if (
            settings.hkgai_api_key is None
            or not settings.hkgai_api_key.get_secret_value().strip()
        ):
            raise PermissionError("HKGAI API key is not configured")
        if not settings.hkgai_base_url or not settings.hkgai_model:
            raise PermissionError("HKGAI base URL/model are not configured")

    stamp = time.strftime("%Y%m%d_%H%M%S")
    root = Path(output_dir) / f"hkgai_stage2_{stamp}"
    root.mkdir(parents=True, exist_ok=True)
    data_dir = root / "data"

    effective_timeout = min(timeout, MAX_TIMEOUT_SECONDS)
    run_settings = settings.model_copy(
        update={
            "llm_provider": "hkgai",
            "hkgai_stream": False,
            "max_retries": 0,
            "request_timeout": effective_timeout,
            "max_tokens": MAX_TOKENS_PER_REQUEST,
            "planner_protocol": "auto",
            "agent_protocol": "auto",
            "enable_final_review": enable_review,
            "max_plan_subtasks": max_plan_subtasks,
            "structured_output_repairs": structured_output_repairs,
            "data_dir": data_dir,
        }
    )
    base = provider_override or build_llm_provider(run_settings)
    budget = Stage2BudgetProvider(
        base, max_requests=max_requests, max_tokens=max_tokens
    )
    result = Stage2Result(
        mode="LIVE" if live else "OFFLINE_TEST",
        configured_model=run_settings.hkgai_model or "unknown",
        output_dir=str(root),
        max_plan_subtasks=max_plan_subtasks,
        review_enabled=enable_review,
        budget_max_requests=budget.max_requests,
        budget_max_tokens=budget.max_tokens,
    )
    started = time.monotonic()
    workflow = None
    try:
        async with AgentForgeRuntime(run_settings, provider=budget) as runtime:
            coordinator = runtime.get_agent("coordinator")
            if coordinator.planner_protocol != "compact":
                raise RuntimeError(
                    "runtime did not select the compact planner for HKGAI: "
                    f"{coordinator.planner_protocol}"
                )
            workflow = await runtime.submit_goal(STAGE2_GOAL)
            result = evaluate_stage2(
                runtime,
                workflow,
                budget,
                mode="LIVE" if live else "OFFLINE_TEST",
                provider_class_ok=(
                    isinstance(base, HKGAIProvider) if live else True
                ),
                require_sandbox=require_sandbox,
                required_tool=required_tool,
            )
            result.output_dir = str(root)
            result.max_plan_subtasks = max_plan_subtasks
            result.review_enabled = enable_review
            result.budget_max_requests = budget.max_requests
            result.budget_max_tokens = budget.max_tokens
    except PermissionError:
        raise
    except Exception as exc:
        result.status = "FAILED"
        result.error_type = type(exc).__name__
        secret = (
            settings.hkgai_api_key.get_secret_value()
            if settings.hkgai_api_key is not None
            else ""
        )
        detail = str(exc)[:1000]
        result.error_detail = detail.replace(secret, "[REDACTED]") if secret else detail
        result.hard_stopped = budget.hard_stopped
        result.hard_stop_reason = budget.hard_stop_reason
        result.budget_exhausted = budget.budget_exhausted
        result.workflow_id = workflow.workflow_id if workflow is not None else None
        result.workflow_status = (
            workflow.status.value if workflow is not None else None
        )
    finally:
        result.elapsed_seconds = round(time.monotonic() - started, 3)
        result.total_requests = budget.requests
        result.total_tokens = budget.tokens
        result.calls = budget.calls
        result.hard_stopped = budget.hard_stopped
        result.hard_stop_reason = budget.hard_stop_reason
        result.budget_exhausted = budget.budget_exhausted
        await budget.close_underlying()

    (root / "stage2.json").write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_report(root, result)
    return result


def _write_report(root: Path, result: Stage2Result) -> None:
    lines = [
        "# HKGAI Real Stage 2 Report",
        "",
        f"- Mode: **{result.mode}**",
        f"- Status: **{result.status}**",
        f"- Configured model: `{result.configured_model}`",
        f"- Workflow: `{result.workflow_id or 'n/a'}` ({result.workflow_status})",
        f"- Goal: {result.goal}",
        f"- Plan subtask cap: {result.max_plan_subtasks}; review phase: "
        f"{result.review_enabled}",
        f"- Budget: {result.budget_max_requests} requests / "
        f"{result.budget_max_tokens} tokens (per-request cap "
        f"{MAX_TOKENS_PER_REQUEST})",
        f"- Requests: {result.total_requests}",
        f"- Tokens: {result.total_tokens}",
        f"- Elapsed: {result.elapsed_seconds}s",
        f"- Hard-stopped: {result.hard_stopped} "
        f"({result.hard_stop_reason or 'n/a'})",
        "",
        "## Checks A–G",
        "",
        "| Check | Passed | Detail |",
        "|---|---|---|",
    ]
    for key in sorted(result.checks):
        check = result.checks[key]
        lines.append(f"| `{key}` | {check.passed} | {check.detail} |")
    lines.extend(["", "## Model calls", "", "| # | Purpose | Model | HTTP | Tokens | s |", "|---:|---|---|---:|---:|---:|"])
    for call in result.calls:
        lines.append(
            f"| {call.index} | `{call.purpose}` | `{call.model or 'n/a'}` | "
            f"{call.http_status} | {call.total_tokens} | {call.elapsed_seconds} |"
        )
    if result.tool_calls:
        lines.extend(["", "## Tool calls", ""])
        for item in result.tool_calls:
            lines.append(
                f"- `{item['tool_name']}` by `{item['agent_id']}`: "
                f"status={item['status']}, error={item['error']}, "
                f"container={item['container']}"
            )
    if result.final_result_excerpt:
        lines.extend(
            ["", "## Final report excerpt", "", "```text", result.final_result_excerpt, "```"]
        )
    if result.error_type:
        lines.extend(
            [
                "",
                "## Error",
                "",
                f"- Type: `{result.error_type}`",
                f"- Detail: {result.error_detail}",
            ]
        )
    (root / "STAGE2_REPORT.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
