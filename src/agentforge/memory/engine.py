"""Evidence-first memory evolution, reflection, promotion and feedback."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta
from typing import Any

from agentforge.config.settings import Settings
from agentforge.memory.base import tokenize
from agentforge.memory.evolution_store import EvolutionMemoryStore
from agentforge.memory.models import (
    EvolvedMemory,
    MemoryCandidate,
    MemoryEvolutionResult,
    MemoryFeedback,
    MemorySource,
    MemoryType,
    PromotionState,
    VerificationLevel,
)
from agentforge.models.common import utc_now
from agentforge.models.task import Task
from agentforge.models.tool import ToolCallRecord, ToolEvidence, ToolResultStatus
from agentforge.models.workflow import Workflow, WorkflowStatus
from agentforge.persistence.database import SQLiteStore

logger = logging.getLogger("agentforge.memory.engine")

_ARGUMENT_HINT_KEYS = {"path", "filename", "url", "query", "remote", "branch"}


class MemoryEvolutionEngine:
    """Turn persisted tool audit into verified memories.

    Reflection is deterministic and evidence-first: it summarizes ToolCallRecord
    rows and ToolEvidence. It does not infer facts from chat text or LLM prose.
    """

    def __init__(
        self,
        *,
        audit: SQLiteStore,
        memory: EvolutionMemoryStore,
        settings: Settings,
    ) -> None:
        self.audit = audit
        self.memory = memory
        self.settings = settings

    def process_workflow(
        self, workflow_id: str, *, learn: bool = True
    ) -> MemoryEvolutionResult:
        workflow = self.audit.get_workflow(workflow_id)
        if workflow is None:
            return MemoryEvolutionResult(
                workflow_id=workflow_id, skipped_reason="workflow_not_found"
            )
        tasks = self.audit.list_tasks(workflow_id)
        records = self.audit.list_tool_calls(workflow_id)
        if not records:
            return MemoryEvolutionResult(
                workflow_id=workflow_id, skipped_reason="no_tool_audit"
            )

        result = MemoryEvolutionResult(workflow_id=workflow_id)
        by_task: dict[str, list[ToolCallRecord]] = {}
        for record in records:
            if record.task_id:
                by_task.setdefault(record.task_id, []).append(record)

        agents: set[str] = set()
        for task in tasks:
            task_records = sorted(
                by_task.get(task.task_id, []), key=lambda item: item.started_at
            )
            if not task_records:
                continue
            agents.add(task.assigned_agent or task_records[0].agent_id)
            verification = self._verification(task_records)
            result.verification_counts[verification.value] = (
                result.verification_counts.get(verification.value, 0) + 1
            )
            if learn:
                self._reflect_task(
                    workflow, task, task_records, verification, result
                )
            self._apply_feedback(workflow, task, task_records, result)

        if learn:
            for agent_id in sorted(agents):
                self.memory.prune(agent_id=agent_id)
        return result

    # ------------------------------------------------------------------ reflection
    def _reflect_task(
        self,
        workflow: Workflow,
        task: Task,
        records: list[ToolCallRecord],
        verification: VerificationLevel,
        result: MemoryEvolutionResult,
    ) -> None:
        agent_id = task.assigned_agent or records[0].agent_id
        namespace = "experience"
        evidence = [ToolEvidence.from_record(record) for record in records]
        source = self._source(
            workflow, task, agent_id, records, evidence, verification
        )
        now = utc_now()
        workflow_ids = [workflow.workflow_id]

        episodic = EvolvedMemory(
            agent_id=agent_id,
            namespace=namespace,
            memory_type=MemoryType.EPISODIC,
            verification_level=verification,
            content=self._episodic_content(workflow, task, records, verification),
            importance=_importance_for(verification, procedural=False),
            confidence=_confidence_for(verification),
            source=source,
            metadata={
                "reflection": "evidence_first_task_summary",
                "workflow_ids": workflow_ids,
                "task_kind": task.metadata.get("kind", "generic"),
                "tool_call_ids": [record.tool_call_id for record in records],
                "evidence_tier": verification.value,
            },
            dedupe_key=f"episodic:{workflow.workflow_id}:{task.task_id}",
        )
        self._store_and_record(episodic, result)

        semantic = self._semantic_memory(workflow, task, records, source, verification)
        if semantic is not None:
            self._store_and_record(semantic, result)

        working = EvolvedMemory(
            agent_id=agent_id,
            namespace=namespace,
            memory_type=MemoryType.WORKING,
            verification_level=verification,
            content=(
                f"Recent workflow context: '{workflow.goal}' / task "
                f"'{task.description}'. Tools: "
                + ", ".join(record.tool_name for record in records)
                + "."
            ),
            importance=0.2,
            confidence=_confidence_for(verification),
            source=source,
            metadata={"workflow_ids": workflow_ids},
            expires_at=now
            + timedelta(seconds=self.settings.memory_working_ttl_seconds),
            dedupe_key=f"working:{workflow.workflow_id}:{task.task_id}",
        )
        self._store_and_record(working, result, count_created=False)

        if verification == VerificationLevel.UNVERIFIED:
            return
        if workflow.status != WorkflowStatus.COMPLETED:
            return
        if not all(record.status == ToolResultStatus.SUCCESS for record in records):
            return
        self._update_procedural_candidate(
            workflow, task, records, source, verification, result
        )

    def _semantic_memory(
        self,
        workflow: Workflow,
        task: Task,
        records: list[ToolCallRecord],
        source: MemorySource,
        verification: VerificationLevel,
    ) -> EvolvedMemory | None:
        record = next(
            (
                item
                for item in records
                if (item.output or {}).get("stdout")
                or (item.output or {}).get("content")
                or (item.output or {}).get("path")
            ),
            None,
        )
        if record is None:
            return None
        output = record.output or {}
        detail = (
            output.get("stdout")
            or output.get("content")
            or output.get("path")
            or ""
        )
        detail = str(detail).strip()
        if len(detail) > 1200:
            detail = detail[:1200] + "...[truncated]"
        exit_code = output.get("exit_code")
        exit_text = "" if exit_code is None else f", exit_code={exit_code}"
        content = (
            f"Execution fact from task '{task.description}': "
            f"{record.tool_name} returned status={record.status.value}"
            f"{exit_text}. Observed: {detail or '(no textual output)'}"
        )
        return EvolvedMemory(
            agent_id=task.assigned_agent or record.agent_id,
            namespace="experience",
            memory_type=MemoryType.SEMANTIC,
            verification_level=verification,
            content=content,
            importance=_importance_for(verification, procedural=False) - 0.05,
            confidence=_confidence_for(verification),
            source=source,
            metadata={
                "workflow_ids": [workflow.workflow_id],
                "tool_call_id": record.tool_call_id,
            },
            dedupe_key=f"semantic:{record.tool_call_id}",
        )

    def _episodic_content(
        self,
        workflow: Workflow,
        task: Task,
        records: list[ToolCallRecord],
        verification: VerificationLevel,
    ) -> str:
        lines = [
            f"Reflection on workflow '{workflow.goal}' / task '{task.description}'.",
            "Tool sequence: "
            + " -> ".join(
                f"{record.tool_name}({record.status.value})" for record in records
            )
            + ".",
        ]
        highlights = []
        for record in records:
            output = record.output or {}
            if output.get("stdout"):
                highlights.append(
                    f"{record.tool_name} stdout={str(output['stdout'])[:500]!r}"
                )
            elif output.get("content"):
                highlights.append(
                    f"{record.tool_name} content={str(output['content'])[:300]!r}"
                )
            elif output.get("path"):
                highlights.append(f"{record.tool_name} path={output['path']!r}")
            elif record.error:
                highlights.append(f"{record.tool_name} error={record.error[:300]!r}")
        if highlights:
            lines.append("Key results: " + "; ".join(highlights))
        lines.append(
            f"Verification: {verification.value}; evidence tool calls: "
            + ", ".join(record.tool_call_id for record in records)
            + "."
        )
        return "\n".join(lines)

    # -------------------------------------------------------------- procedural
    def _update_procedural_candidate(
        self,
        workflow: Workflow,
        task: Task,
        records: list[ToolCallRecord],
        source: MemorySource,
        verification: VerificationLevel,
        result: MemoryEvolutionResult,
    ) -> None:
        agent_id = task.assigned_agent or records[0].agent_id
        procedure = self._procedure(workflow, task, records, verification)
        signature = hashlib.sha256(
            json.dumps(
                {
                    "agent": agent_id,
                    "capabilities": procedure["required_capabilities"],
                    "kind": procedure["kind"],
                    "steps": procedure["steps"],
                    "terms": procedure["task_terms"],
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        candidate = MemoryCandidate(
            agent_id=agent_id,
            namespace="experience",
            signature=signature,
            verification_level=verification,
            content=_procedure_text(
                agent_id, procedure, verification, support_count=1
            ),
            procedure=procedure,
            support_count=1,
            evidence_workflow_ids=[workflow.workflow_id],
            evidence_tool_call_ids=[record.tool_call_id for record in records],
            evidence_task_ids=[task.task_id],
            status=PromotionState.CANDIDATE,
        )
        merged, created, new_workflow = self.memory.upsert_candidate(candidate)
        if created or new_workflow:
            result.candidate_ids.append(merged.candidate_id)

        if (
            merged.verification_level == VerificationLevel.REAL_EXECUTION
            and merged.support_count >= self.settings.memory_promotion_threshold
        ):
            confidence = min(
                0.95, 0.75 + 0.05 * merged.support_count
            )
            if confidence >= self.settings.memory_promotion_confidence:
                merged.content = _procedure_text(
                    agent_id,
                    merged.procedure,
                    merged.verification_level,
                    support_count=merged.support_count,
                )
                self.memory.upsert_candidate(merged)
                promoted = EvolvedMemory(
                    agent_id=agent_id,
                    namespace="experience",
                    memory_type=MemoryType.PROCEDURAL,
                    verification_level=VerificationLevel.REAL_EXECUTION,
                    content=merged.content,
                    importance=0.9,
                    confidence=confidence,
                    support_count=merged.support_count,
                    source=source.model_copy(
                        update={
                            "origin": "evidence_first_procedural_reflection",
                            "tool_call_ids": merged.evidence_tool_call_ids,
                            "metadata": {
                                "candidate_id": merged.candidate_id,
                                "workflow_ids": merged.evidence_workflow_ids,
                            },
                        }
                    ),
                    metadata={
                        "procedure": merged.procedure,
                        "candidate_id": merged.candidate_id,
                        "workflow_ids": merged.evidence_workflow_ids,
                        "evidence_tool_call_ids": merged.evidence_tool_call_ids,
                    },
                    promotion_state=PromotionState.PROMOTED,
                    dedupe_key=f"procedural:{merged.signature}",
                )
                stored, created_memory, _updated = self.memory.upsert_memory(promoted)
                if created_memory:
                    result.created_memory_ids.append(stored.memory_id)
                else:
                    result.updated_memory_ids.append(stored.memory_id)
                result.promoted_memory_ids.append(stored.memory_id)
                self.memory.mark_candidate_promoted(merged.candidate_id, stored.memory_id)
        elif (
            merged.verification_level == VerificationLevel.MOCK_SIMULATION
            and self.settings.memory_store_mock_procedures
        ):
            merged.content = _procedure_text(
                agent_id,
                merged.procedure,
                merged.verification_level,
                support_count=merged.support_count,
            )
            self.memory.upsert_candidate(merged)
            provisional = EvolvedMemory(
                agent_id=agent_id,
                namespace="experience",
                memory_type=MemoryType.PROCEDURAL,
                verification_level=VerificationLevel.MOCK_SIMULATION,
                content="[MOCK SIMULATION - NOT REAL-WORLD VERIFIED] "
                + merged.content,
                importance=0.35,
                confidence=0.3,
                support_count=merged.support_count,
                source=source.model_copy(
                    update={
                        "origin": "evidence_first_procedural_reflection",
                        "tool_call_ids": merged.evidence_tool_call_ids,
                    }
                ),
                metadata={
                    "procedure": merged.procedure,
                    "candidate_id": merged.candidate_id,
                    "workflow_ids": merged.evidence_workflow_ids,
                },
                promotion_state=PromotionState.PROVISIONAL,
                dedupe_key=f"mock_procedural:{merged.signature}",
            )
            stored, created_memory, _updated = self.memory.upsert_memory(provisional)
            if created_memory:
                result.created_memory_ids.append(stored.memory_id)
            else:
                result.updated_memory_ids.append(stored.memory_id)
            merged.status = PromotionState.PROVISIONAL
            merged.promoted_memory_id = stored.memory_id
            self.memory.upsert_candidate(merged)

    def _procedure(
        self,
        workflow: Workflow,
        task: Task,
        records: list[ToolCallRecord],
        verification: VerificationLevel,
    ) -> dict[str, Any]:
        terms = sorted(tokenize(task.description))[:12]
        return {
            "required_capabilities": sorted(task.required_capabilities),
            "kind": str(task.metadata.get("kind", "generic")),
            "task_terms": terms,
            "task_example": task.description[:500],
            "steps": [
                {
                    "tool": record.tool_name,
                    "arguments": _argument_hints(record.arguments),
                }
                for record in records
            ],
            "tool_sequence": [record.tool_name for record in records],
            "verification": verification.value,
        }

    # ------------------------------------------------------------------ feedback
    def _apply_feedback(
        self,
        workflow: Workflow,
        task: Task,
        records: list[ToolCallRecord],
        result: MemoryEvolutionResult,
    ) -> None:
        agent_id = task.assigned_agent or records[0].agent_id
        if task.started_at is None or task.finished_at is None:
            return
        events = self.memory.list_retrieval_events(
            agent_id=agent_id,
            since=task.started_at,
            until=task.finished_at,
        )
        all_success = all(
            record.status == ToolResultStatus.SUCCESS for record in records
        )
        for event in events:
            for memory_id in event.memory_ids:
                memory = self.memory.get_memory(memory_id)
                if memory is None:
                    continue
                if (
                    memory.memory_type != MemoryType.PROCEDURAL
                    or memory.promotion_state != PromotionState.PROMOTED
                    or memory.verification_level != VerificationLevel.REAL_EXECUTION
                ):
                    continue
                procedure = memory.metadata.get("procedure") or {}
                used = _procedure_matches(procedure, records)
                delta = 0.0
                if used:
                    delta = 0.03 if all_success else -0.10
                    self.memory.update_confidence(memory_id, delta)
                feedback = MemoryFeedback(
                    retrieval_id=event.retrieval_id,
                    memory_id=memory_id,
                    workflow_id=workflow.workflow_id,
                    task_id=task.task_id,
                    agent_id=agent_id,
                    used=used,
                    success=all_success if used else False,
                    confidence_delta=delta,
                    evidence={
                        "tool_call_ids": [record.tool_call_id for record in records],
                        "tool_sequence": [record.tool_name for record in records],
                    },
                )
                if self.memory.record_feedback(feedback):
                    result.feedback_ids.append(feedback.feedback_id)

    # ------------------------------------------------------------------- helpers
    def _source(
        self,
        workflow: Workflow,
        task: Task,
        agent_id: str,
        records: list[ToolCallRecord],
        evidence: list[ToolEvidence],
        verification: VerificationLevel,
    ) -> MemorySource:
        provider = workflow.metadata.get("provider")
        digest_payload = [
            {
                "tool_call_id": item.tool_call_id,
                "tool_name": item.tool_name,
                "status": item.status.value,
                "success": item.success,
                "error": item.error,
                "duration_seconds": item.duration_seconds,
            }
            for item in evidence
        ]
        digest = hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return MemorySource(
            origin="tool_audit",
            workflow_id=workflow.workflow_id,
            task_id=task.task_id,
            agent_id=agent_id,
            tool_call_ids=[record.tool_call_id for record in records],
            tool_names=[record.tool_name for record in records],
            evidence_digest=digest,
            llm_provider=str(provider) if provider else None,
            llm_is_mock=str(provider).lower() == "mock",
            tool_execution=(
                "mock"
                if verification == VerificationLevel.MOCK_SIMULATION
                else "real"
                if verification == VerificationLevel.REAL_EXECUTION
                else "none"
            ),
            metadata={
                "evidence_statuses": [item.status.value for item in evidence],
                "evidence_success_count": sum(
                    1 for item in evidence if item.success
                ),
            },
        )

    def _verification(
        self, records: list[ToolCallRecord]
    ) -> VerificationLevel:
        if any(record.status != ToolResultStatus.SUCCESS for record in records):
            return VerificationLevel.UNVERIFIED
        if any(bool(record.metadata.get("mock")) for record in records):
            return VerificationLevel.MOCK_SIMULATION
        return VerificationLevel.REAL_EXECUTION

    def _store_and_record(
        self,
        memory: EvolvedMemory,
        result: MemoryEvolutionResult,
        *,
        count_created: bool = True,
    ) -> None:
        stored, created, updated = self.memory.upsert_memory(memory)
        if count_created and created:
            result.created_memory_ids.append(stored.memory_id)
        if updated:
            result.updated_memory_ids.append(stored.memory_id)


def _importance_for(
    verification: VerificationLevel, *, procedural: bool
) -> float:
    if procedural:
        return {
            VerificationLevel.REAL_EXECUTION: 0.9,
            VerificationLevel.MOCK_SIMULATION: 0.35,
            VerificationLevel.UNVERIFIED: 0.2,
        }[verification]
    return {
        VerificationLevel.REAL_EXECUTION: 0.55,
        VerificationLevel.MOCK_SIMULATION: 0.35,
        VerificationLevel.UNVERIFIED: 0.2,
    }[verification]


def _confidence_for(verification: VerificationLevel) -> float:
    return {
        VerificationLevel.REAL_EXECUTION: 0.75,
        VerificationLevel.MOCK_SIMULATION: 0.3,
        VerificationLevel.UNVERIFIED: 0.15,
    }[verification]


def _argument_hints(arguments: dict[str, Any]) -> dict[str, Any]:
    hints: dict[str, Any] = {}
    for key, value in arguments.items():
        if key not in _ARGUMENT_HINT_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            text = str(value)
            hints[key] = text[:200]
    return hints


def _procedure_text(
    agent_id: str,
    procedure: dict[str, Any],
    verification: VerificationLevel,
    *,
    support_count: int,
) -> str:
    prefix = (
        "Verified procedure"
        if verification == VerificationLevel.REAL_EXECUTION
        else "MOCK-SIMULATION procedure (NOT REAL-WORLD VERIFIED)"
    )
    capabilities = ", ".join(procedure.get("required_capabilities", [])) or "none"
    terms = ", ".join(procedure.get("task_terms", [])[:8])
    lines = [
        f"{prefix} for agent '{agent_id}' (kind="
        f"{procedure.get('kind', 'generic')}; capabilities: {capabilities}).",
        f"Task terms: {terms}.",
        "Successful tool sequence:",
    ]
    for index, step in enumerate(procedure.get("steps", []), start=1):
        args = ", ".join(
            f"{key}={value!r}" for key, value in step.get("arguments", {}).items()
        )
        lines.append(f"  {index}. {step.get('tool')}({args})")
    lines.append(
        f"Evidence: {support_count} distinct workflow(s), verification="
        f"{verification.value}. Prefer this exact sequence and arguments when a "
        f"task matches the terms/capabilities above. Example task: "
        f"{procedure.get('task_example', '')[:300]}"
    )
    return "\n".join(lines)


def _procedure_matches(
    procedure: dict[str, Any], records: list[ToolCallRecord]
) -> bool:
    expected_sequence = procedure.get("tool_sequence") or []
    actual_sequence = [record.tool_name for record in records]
    if expected_sequence != actual_sequence:
        return False
    expected_steps = procedure.get("steps") or []
    if len(expected_steps) != len(records):
        return False
    for step, record in zip(expected_steps, records):
        for key, value in (step.get("arguments") or {}).items():
            actual = record.arguments.get(key)
            if str(actual) != str(value):
                return False
    return True
