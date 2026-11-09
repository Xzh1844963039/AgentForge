"""Two minimal HKGAI control experiments for the Stage 2 disconnect.

A: minimal structured JSON output.
B: the real Coordinator PLANNER_PROTOCOL with a minimal one-task goal.

Both use the same provider/model/base URL, non-streaming mode, no retries, no
tools, and no side effects. The suite runs at most two requests and stops after
A fails.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agentforge.agents.base import extract_json_payload
from agentforge.agents.coordinator import PLANNER_PROTOCOL
from agentforge.agents.factory import DEFAULT_PROFILES
from agentforge.benchmark.provider import LiveBudgetProvider
from agentforge.config.settings import Settings
from agentforge.llm.base import ChatMessage, LLMProviderError
from agentforge.llm.factory import build_llm_provider
from agentforge.models.agent import AgentCard
from agentforge.models.common import utc_now
from agentforge.models.task import ExecutionPlan
from agentforge.orchestration.assignment import CapabilityAssigner
from agentforge.orchestration.planner import validate_plan


class ExperimentResult(BaseModel):
    experiment: str
    question: str
    status: str = "NOT_VERIFIED"
    http_status: int | None = None
    response_model: str | None = None
    elapsed_seconds: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    finish_reason: str | None = None
    content_chars: int = 0
    content_preview: str = ""
    valid_json: bool = False
    schema_valid: bool = False
    plan_valid: bool = False
    plan_subtasks: int = 0
    error_type: str | None = None
    error_detail: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)
    checked_at: datetime = Field(default_factory=utc_now)


class ExperimentSuiteResult(BaseModel):
    status: str = "NOT_VERIFIED"
    configured_model: str
    stream: bool = False
    experiments: list[ExperimentResult] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    output_dir: str = ""


class ExperimentState(BaseModel):
    a_status: str = "not_run"
    b_status: str = "not_run"
    last_run_dir: str | None = None
    updated_at: datetime = Field(default_factory=utc_now)


def _catalog() -> str:
    cards = [AgentCard.from_profile(profile) for profile in DEFAULT_PROFILES]
    return "\n".join(
        f"- {card.agent_id} ({card.name}, role={card.role.value}): "
        f"capabilities={', '.join(card.capabilities)}. {card.description}"
        for card in cards
    )


def experiment_a_messages() -> list[ChatMessage]:
    return [
        ChatMessage.system(
            "You are a JSON-only endpoint. Return exactly one JSON object and "
            "nothing else. No Markdown fences, no commentary."
        ),
        ChatMessage.user(
            'Return the JSON object {"status": "ok", "value": 1} exactly. '
            "Do not add any other keys."
        ),
    ]


def experiment_b_messages() -> list[ChatMessage]:
    goal = 'Create notes/simple.md containing "hello".'
    user = (
        f"[GOAL]\n{goal}\n\n"
        f"[AVAILABLE_AGENTS]\n{_catalog()}\n\n"
        "[MAX_SUBTASKS]\n3\n\n"
        "Return the JSON execution plan now."
    )
    return [ChatMessage.system(PLANNER_PROTOCOL), ChatMessage.user(user)]


def validate_experiment_a(content: str) -> tuple[bool, bool]:
    try:
        payload = extract_json_payload(content)
    except Exception:
        return False, False
    valid_json = isinstance(payload, dict)
    schema_valid = payload == {"status": "ok", "value": 1}
    return valid_json, schema_valid


def validate_experiment_b(content: str) -> tuple[bool, bool, int]:
    try:
        payload = extract_json_payload(content)
        if not isinstance(payload, dict):
            return False, False, 0
        plan = ExecutionPlan.model_validate(payload)
        ordered = validate_plan(plan, max_subtasks=3)
    except Exception:
        return True, False, 0
    if not plan.goal_summary.strip() or len(ordered) != 1:
        return True, False, len(ordered)
    try:
        assigner = CapabilityAssigner(
            [AgentCard.from_profile(profile) for profile in DEFAULT_PROFILES]
        )
        assigner.assign(ordered[0].required_capabilities)
    except Exception:
        return True, False, len(ordered)
    return True, True, len(ordered)


class HKGAIExperimentRunner:
    """Sequential A→B control suite with explicit budget and state gates."""

    def __init__(
        self,
        settings: Settings,
        *,
        allow_live_api: bool,
        output_dir: str | Path = "benchmarks/runs",
        timeout: float = 120.0,
    ) -> None:
        self.settings = settings
        self.allow_live_api = allow_live_api
        self.output_root = Path(output_dir)
        self.timeout = timeout
        self.state_path = self.output_root / "hkgai_experiments_state.json"

    async def run(self, experiment: str = "AB") -> ExperimentSuiteResult:
        experiment = experiment.upper()
        if experiment not in {"A", "B", "AB"}:
            raise ValueError("experiment must be A, B or AB")
        if not self.allow_live_api:
            raise PermissionError(
                "HKGAI experiments require explicit --allow-live-api authorization"
            )
        if (
            self.settings.hkgai_api_key is None
            or not self.settings.hkgai_api_key.get_secret_value().strip()
        ):
            raise PermissionError("HKGAI API key is not configured")
        if not self.settings.hkgai_base_url or not self.settings.hkgai_model:
            raise PermissionError("HKGAI base URL/model are not configured")

        state = self._load_state()
        if experiment in {"B", "AB"} and state.a_status != "passed":
            if experiment == "B":
                raise PermissionError(
                    "experiment B is gated on a passed experiment A; "
                    "run A first"
                )
        run_dir = self.output_root / f"hkgai_experiments_{time.strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        result = ExperimentSuiteResult(
            configured_model=self.settings.hkgai_model or "unknown",
            output_dir=str(run_dir),
        )
        provider_settings = self.settings.model_copy(
            update={
                "llm_provider": "hkgai",
                "hkgai_stream": False,
                "max_retries": 0,
                "request_timeout": self.timeout,
                "max_tokens": 512,
            }
        )
        base = build_llm_provider(provider_settings)
        budget = LiveBudgetProvider(base, max_requests=2, max_tokens=20_000)
        try:
            if experiment in {"A", "AB"}:
                a = await self._run_one(
                    "A",
                    "Can the model return a minimal JSON object without planning?",
                    experiment_a_messages(),
                    budget,
                    max_tokens=256,
                    validator=validate_experiment_a,
                )
                result.experiments.append(a)
                state.a_status = a.status
                if a.status != "passed":
                    if experiment == "AB":
                        b = ExperimentResult(
                            experiment="B",
                            question="Minimal Coordinator DAG",
                            status="SKIPPED",
                            error_type="GatedByExperimentA",
                            error_detail="experiment A failed; B was not run",
                        )
                        result.experiments.append(b)
                        state.b_status = "skipped"
                    state.last_run_dir = str(run_dir)
                    state.updated_at = utc_now()
                    self._save_state(state)
                    result.status = "FAILED"
                    result.notes.append(
                        "Experiment B was not executed because A failed."
                    )
                    self._write(run_dir, result)
                    return result
            if experiment in {"B", "AB"}:
                if state.a_status != "passed":
                    raise PermissionError("experiment A has not passed")
                b = await self._run_one(
                    "B",
                    "Does the real PLANNER_PROTOCOL work with a minimal one-task goal?",
                    experiment_b_messages(),
                    budget,
                    max_tokens=512,
                    validator=validate_experiment_b,
                )
                result.experiments.append(b)
                state.b_status = b.status
                if b.status != "passed":
                    result.status = "FAILED"
            else:
                result.status = (
                    "PASSED" if result.experiments[0].status == "passed" else "FAILED"
                )
            state.last_run_dir = str(run_dir)
            state.updated_at = utc_now()
            self._save_state(state)
            if result.status == "NOT_VERIFIED":
                result.status = (
                    "PASSED"
                    if all(item.status == "passed" for item in result.experiments)
                    else "FAILED"
                )
            self._write(run_dir, result)
            return result
        finally:
            await budget.close_underlying()

    async def _run_one(
        self,
        label: str,
        question: str,
        messages: list[ChatMessage],
        budget: LiveBudgetProvider,
        *,
        max_tokens: int,
        validator,
    ) -> ExperimentResult:
        result = ExperimentResult(experiment=label, question=question)
        started = time.monotonic()
        try:
            response = await budget.complete(
                messages,
                purpose=f"hkgai_control_{label.lower()}",
                temperature=0.2,
                max_tokens=max_tokens,
            )
            result.http_status = response.raw.get("http_status")
            result.response_model = response.model
            result.finish_reason = response.finish_reason
            result.prompt_tokens = response.usage.prompt_tokens
            result.completion_tokens = response.usage.completion_tokens
            result.total_tokens = response.usage.total_tokens
            result.content_chars = len(response.content)
            result.content_preview = response.content[:800]
            valid_json, schema_valid, *rest = validator(response.content)
            result.valid_json = valid_json
            result.schema_valid = schema_valid
            if label == "B" and rest:
                result.plan_subtasks = int(rest[0])
                result.plan_valid = schema_valid
            if label == "A":
                result.plan_valid = schema_valid
            result.status = (
                "passed" if valid_json and schema_valid else "failed"
            )
        except Exception as exc:
            result.status = "failed"
            result.error_type = type(exc).__name__
            secret = (
                self.settings.hkgai_api_key.get_secret_value()
                if self.settings.hkgai_api_key is not None
                else ""
            )
            detail = str(exc)[:1000]
            result.error_detail = (
                detail.replace(secret, "[REDACTED]") if secret else detail
            )
            if isinstance(exc, LLMProviderError):
                result.http_status = exc.status_code
        finally:
            result.elapsed_seconds = round(time.monotonic() - started, 3)
            result.budget = budget.snapshot()
        return result

    def _load_state(self) -> ExperimentState:
        if not self.state_path.exists():
            return ExperimentState()
        try:
            return ExperimentState.model_validate_json(
                self.state_path.read_text(encoding="utf-8")
            )
        except Exception:
            return ExperimentState()

    def _save_state(self, state: ExperimentState) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            state.model_dump_json(indent=2), encoding="utf-8"
        )

    def _write(self, root: Path, result: ExperimentSuiteResult) -> None:
        (root / "experiments.json").write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        lines = [
            "# HKGAI Control Experiments",
            "",
            f"- Configured model: `{result.configured_model}`",
            f"- Stream: {result.stream}",
            f"- Status: **{result.status}**",
            "",
            "| Experiment | Status | HTTP | Elapsed | Tokens | finish_reason | JSON | Schema | Plan |",
            "|---|---|---:|---:|---:|---|---|---|---|",
        ]
        for item in result.experiments:
            lines.append(
                f"| {item.experiment} | {item.status} | {item.http_status} | "
                f"{item.elapsed_seconds}s | {item.total_tokens} | "
                f"{item.finish_reason} | {item.valid_json} | "
                f"{item.schema_valid} | {item.plan_valid} |"
            )
        (root / "EXPERIMENTS_REPORT.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )


async def run_hkgai_experiments(
    settings: Settings,
    *,
    allow_live_api: bool,
    experiment: str = "AB",
    output_dir: str | Path = "benchmarks/runs",
    timeout: float = 120.0,
) -> ExperimentSuiteResult:
    return await HKGAIExperimentRunner(
        settings,
        allow_live_api=allow_live_api,
        output_dir=output_dir,
        timeout=timeout,
    ).run(experiment)
