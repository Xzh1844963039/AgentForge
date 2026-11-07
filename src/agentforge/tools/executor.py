"""Unified tool dispatcher with validation, approvals, timeouts and audit."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Any

from jsonschema import Draft202012Validator

from agentforge.models.common import utc_now
from agentforge.models.tool import (
    ApprovalRequest,
    ApprovalStatus,
    PermissionDecision,
    ToolCall,
    ToolCallRecord,
    ToolResultStatus,
)
from agentforge.persistence.database import SQLiteStore
from agentforge.persistence.events import EventLogger
from agentforge.tools.approval import ApprovalGate
from agentforge.tools.base import (
    ToolExecutionContext,
    ToolRegistry,
    ToolResult,
)
from agentforge.tools.policy import ToolPermissionPolicy

logger = logging.getLogger("agentforge.tools.executor")


class ToolExecutor:
    """The only path through which agents can execute tools."""

    def __init__(
        self,
        registry: ToolRegistry,
        store: SQLiteStore,
        events: EventLogger,
        policy: ToolPermissionPolicy,
        approval_gate: ApprovalGate,
        *,
        max_output_bytes: int = 65_536,
    ) -> None:
        self.registry = registry
        self.store = store
        self.events = events
        self.policy = policy
        self.approval_gate = approval_gate
        self.max_output_bytes = max(1024, max_output_bytes)

    async def execute(
        self, tool_call: ToolCall, context: ToolExecutionContext
    ) -> ToolResult:
        started_at = utc_now()
        started_perf = time.perf_counter()
        permission_decision = PermissionDecision.DENIED
        approval_status = ApprovalStatus.NOT_REQUIRED
        approval_id: str | None = None
        tool_spec = None

        try:
            tool = self.registry.get(tool_call.name)
        except KeyError as exc:
            result = ToolResult.fail(str(exc))
            self._record(
                tool_call,
                context,
                result,
                started_at,
                started_perf,
                permission_decision=permission_decision,
                approval_status=approval_status,
                approval_id=None,
                permissions=[],
                metadata={"reason": "unknown_tool"},
            )
            return result

        tool_spec = tool.spec
        evaluation = self.policy.evaluate(context.agent_id, tool_spec)
        permission_decision = evaluation.decision
        if evaluation.decision == PermissionDecision.DENIED:
            result = ToolResult.fail(
                f"tool permission denied: {evaluation.reason}",
                status=ToolResultStatus.DENIED,
                metadata={
                    "missing_permissions": list(evaluation.missing),
                    "tool_permissions": list(evaluation.permissions),
                },
            )
            self._record(
                tool_call,
                context,
                result,
                started_at,
                started_perf,
                permission_decision=permission_decision,
                approval_status=approval_status,
                approval_id=None,
                permissions=list(evaluation.permissions),
                metadata={"reason": "permission_denied"},
            )
            return result

        argument_error = self._validate_schema(
            tool_spec.input_schema, tool_call.arguments, label="arguments"
        )
        if argument_error is not None:
            result = ToolResult.fail(argument_error)
            self._record(
                tool_call,
                context,
                result,
                started_at,
                started_perf,
                permission_decision=permission_decision,
                approval_status=approval_status,
                approval_id=None,
                permissions=list(evaluation.permissions),
                metadata={"reason": "schema_validation"},
            )
            return result

        if evaluation.requires_approval:
            request = ApprovalRequest(
                agent_id=context.agent_id,
                action=tool_spec.name,
                summary=(
                    f"Agent {context.agent_id} requests approval to run "
                    f"{tool_spec.name}"
                ),
                workflow_id=context.workflow_id,
                task_id=context.task_id,
                tool_name=tool_spec.name,
                details={"arguments": tool_call.arguments},
            )
            approval_id = request.approval_id
            decision = await self.approval_gate.request_approval(request)
            approval_status = decision.status
            self.events.event(
                "tool_approval",
                tool_call_id=tool_call.call_id,
                workflow_id=context.workflow_id,
                task_id=context.task_id,
                agent_id=context.agent_id,
                tool_name=tool_spec.name,
                approval_id=request.approval_id,
                status=decision.status.value,
                reason=decision.reason,
            )

            if decision.status == ApprovalStatus.PENDING:
                result = ToolResult.fail(
                    "tool execution is pending human approval",
                    status=ToolResultStatus.PENDING_APPROVAL,
                    metadata={
                        "approval_id": request.approval_id,
                        "approval_status": decision.status.value,
                    },
                )
                self._record(
                    tool_call,
                    context,
                    result,
                    started_at,
                    started_perf,
                    permission_decision=permission_decision,
                    approval_status=approval_status,
                    approval_id=approval_id,
                    permissions=list(evaluation.permissions),
                    metadata={"reason": "approval_pending"},
                )
                return result

            if decision.status != ApprovalStatus.APPROVED:
                result = ToolResult.fail(
                    f"tool execution was not approved: {decision.reason}",
                    status=ToolResultStatus.DENIED,
                    metadata={
                        "approval_id": request.approval_id,
                        "approval_status": decision.status.value,
                    },
                )
                self._record(
                    tool_call,
                    context,
                    result,
                    started_at,
                    started_perf,
                    permission_decision=permission_decision,
                    approval_status=approval_status,
                    approval_id=approval_id,
                    permissions=list(evaluation.permissions),
                    metadata={"reason": "approval_denied"},
                )
                return result

        try:
            result = await asyncio.wait_for(
                tool.execute(tool_call.arguments, context),
                timeout=tool_spec.timeout_seconds,
            )
            if not isinstance(result, ToolResult):
                raise TypeError(
                    f"tool {tool_spec.name!r} did not return a ToolResult"
                )
        except asyncio.TimeoutError:
            result = ToolResult.fail(
                f"tool timed out after {tool_spec.timeout_seconds:g}s",
                status=ToolResultStatus.TIMEOUT,
                metadata={"timeout_seconds": tool_spec.timeout_seconds},
            )
        except Exception as exc:
            logger.exception("tool %s failed", tool_spec.name)
            result = ToolResult.fail(
                f"{type(exc).__name__}: {exc}",
                status=ToolResultStatus.ERROR,
                metadata={"exception_type": type(exc).__name__},
            )

        if result.status == ToolResultStatus.SUCCESS:
            output_error = self._validate_schema(
                tool_spec.output_schema, result.output, label="output"
            )
            if output_error is not None:
                result = ToolResult.fail(
                    f"tool output failed schema validation: {output_error}",
                    metadata={"invalid_output": result.output},
                )

        result = self._truncate_output(result, tool_spec.max_output_bytes)
        result.metadata.setdefault("tool_name", tool_spec.name)
        result.metadata.setdefault("tool_call_id", tool_call.call_id)
        result.metadata.setdefault("untrusted_output", tool_spec.untrusted_output)
        result.metadata.setdefault("permissions", list(evaluation.permissions))
        result.metadata.setdefault("approval_status", approval_status.value)
        if approval_id:
            result.metadata.setdefault("approval_id", approval_id)
        self._record(
            tool_call,
            context,
            result,
            started_at,
            started_perf,
            permission_decision=permission_decision,
            approval_status=approval_status,
            approval_id=approval_id,
            permissions=list(evaluation.permissions),
            metadata={"reason": "executed"},
        )
        return result

    async def execute_many(
        self,
        tool_calls: list[ToolCall],
        context: ToolExecutionContext,
    ) -> list[ToolResult]:
        """Execute calls sequentially; stop is the agent's decision, not ours."""
        return [await self.execute(call, context) for call in tool_calls]

    def _record(
        self,
        tool_call: ToolCall,
        context: ToolExecutionContext,
        result: ToolResult,
        started_at: datetime,
        started_perf: float,
        *,
        permission_decision: PermissionDecision,
        approval_status: ApprovalStatus,
        approval_id: str | None,
        permissions: list[str],
        metadata: dict[str, Any],
    ) -> ToolCallRecord:
        duration = max(0.0, time.perf_counter() - started_perf)
        result.duration_seconds = duration
        merged_metadata = {**metadata, **result.metadata}
        record = ToolCallRecord(
            tool_call_id=tool_call.call_id,
            workflow_id=context.workflow_id,
            task_id=context.task_id,
            agent_id=context.agent_id,
            tool_name=tool_call.name,
            arguments=tool_call.arguments,
            status=result.status,
            output=result.output,
            error=result.error,
            duration_seconds=duration,
            permission_decision=permission_decision,
            approval_status=approval_status,
            approval_id=approval_id,
            permissions=permissions,
            started_at=started_at,
            finished_at=utc_now(),
            metadata=merged_metadata,
        )
        try:
            self.store.save_tool_call(record)
        except Exception:
            logger.exception("failed to persist tool call %s", tool_call.call_id)
        self.events.event(
            "tool_call",
            tool_call_id=record.tool_call_id,
            workflow_id=record.workflow_id,
            task_id=record.task_id,
            agent_id=record.agent_id,
            tool_name=record.tool_name,
            status=record.status.value,
            duration_seconds=record.duration_seconds,
            approval_status=record.approval_status.value,
            error=record.error,
        )
        return record

    @staticmethod
    def _validate_schema(
        schema: dict[str, Any], value: Any, *, label: str
    ) -> str | None:
        if not schema:
            return None
        validator = Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(value), key=lambda error: list(error.path))
        if not errors:
            return None
        details = []
        for error in errors[:5]:
            path = ".".join(str(part) for part in error.absolute_path) or "<root>"
            details.append(f"{path}: {error.message}")
        return f"invalid tool {label}: " + "; ".join(details)

    def _truncate_output(
        self, result: ToolResult, tool_limit: int | None
    ) -> ToolResult:
        limit = min(self.max_output_bytes, tool_limit or self.max_output_bytes)
        serialized = json.dumps(result.output, ensure_ascii=False, default=str)
        encoded = serialized.encode("utf-8")
        if len(encoded) <= limit:
            return result
        preview = encoded[:limit].decode("utf-8", errors="replace")
        return ToolResult(
            status=result.status,
            output={"truncated": True, "preview": preview},
            error=result.error,
            duration_seconds=result.duration_seconds,
            metadata={**result.metadata, "output_truncated": True},
        )
