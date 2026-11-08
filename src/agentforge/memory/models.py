"""Phase 3 memory taxonomy, provenance and verification models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from agentforge.models.common import new_id, utc_now


class MemoryType(str, Enum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    WORKING = "working"


class VerificationLevel(str, Enum):
    REAL_EXECUTION = "REAL_EXECUTION"
    MOCK_SIMULATION = "MOCK_SIMULATION"
    UNVERIFIED = "UNVERIFIED"


class PromotionState(str, Enum):
    NONE = "none"
    CANDIDATE = "candidate"
    PROVISIONAL = "provisional"
    PROMOTED = "promoted"


VERIFICATION_RANK = {
    VerificationLevel.UNVERIFIED: 0,
    VerificationLevel.MOCK_SIMULATION: 1,
    VerificationLevel.REAL_EXECUTION: 2,
}


class MemorySource(BaseModel):
    """Where a memory came from, with traceable evidence references."""

    origin: str = "tool_audit"
    workflow_id: str | None = None
    task_id: str | None = None
    agent_id: str | None = None
    tool_call_ids: list[str] = Field(default_factory=list)
    tool_names: list[str] = Field(default_factory=list)
    evidence_digest: str = ""
    llm_provider: str | None = None
    llm_is_mock: bool = False
    tool_execution: str = "none"  # real | mock | none
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvolvedMemory(BaseModel):
    memory_id: str = Field(default_factory=lambda: new_id("mem"))
    agent_id: str
    namespace: str = "experience"
    memory_type: MemoryType = MemoryType.EPISODIC
    verification_level: VerificationLevel = VerificationLevel.UNVERIFIED
    content: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.2, ge=0.0, le=1.0)
    support_count: int = Field(default=1, ge=1)
    source: MemorySource | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: list[float] = Field(default_factory=list)
    access_count: int = 0
    last_accessed_at: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime | None = None
    promotion_state: PromotionState = PromotionState.NONE
    dedupe_key: str | None = None


class MemoryRetrievalConfig(BaseModel):
    enabled: bool = True
    max_items: int = Field(default=6, ge=1, le=50)
    max_chars: int = Field(default=3000, ge=100, le=50_000)
    include_mock: bool = False
    include_unverified: bool = False


class MemoryCandidate(BaseModel):
    candidate_id: str = Field(default_factory=lambda: new_id("cand"))
    agent_id: str
    namespace: str = "experience"
    signature: str
    verification_level: VerificationLevel
    content: str
    procedure: dict[str, Any] = Field(default_factory=dict)
    support_count: int = Field(default=1, ge=1)
    evidence_workflow_ids: list[str] = Field(default_factory=list)
    evidence_tool_call_ids: list[str] = Field(default_factory=list)
    evidence_task_ids: list[str] = Field(default_factory=list)
    first_seen: datetime = Field(default_factory=utc_now)
    last_seen: datetime = Field(default_factory=utc_now)
    promoted_memory_id: str | None = None
    status: PromotionState = PromotionState.CANDIDATE


class RetrievalEvent(BaseModel):
    retrieval_id: str = Field(default_factory=lambda: new_id("retrieval"))
    agent_id: str
    namespace: str | None = None
    query: str
    memory_ids: list[str] = Field(default_factory=list)
    workflow_id: str | None = None
    task_id: str | None = None
    execution_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)


class MemoryFeedback(BaseModel):
    feedback_id: str = Field(default_factory=lambda: new_id("feedback"))
    retrieval_id: str
    memory_id: str
    workflow_id: str | None = None
    task_id: str | None = None
    execution_id: str | None = None
    agent_id: str
    used: bool = False
    success: bool = False
    confidence_delta: float = 0.0
    evidence: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)


class MemoryEvolutionResult(BaseModel):
    workflow_id: str
    skipped_reason: str | None = None
    created_memory_ids: list[str] = Field(default_factory=list)
    updated_memory_ids: list[str] = Field(default_factory=list)
    candidate_ids: list[str] = Field(default_factory=list)
    promoted_memory_ids: list[str] = Field(default_factory=list)
    feedback_ids: list[str] = Field(default_factory=list)
    verification_counts: dict[str, int] = Field(default_factory=dict)


class MemoryStats(BaseModel):
    total: int = 0
    active: int = 0
    candidates: int = 0
    promoted: int = 0
    feedback_used: int = 0
    feedback_success: int = 0
    by_agent: dict[str, int] = Field(default_factory=dict)
    by_type: dict[str, int] = Field(default_factory=dict)
    by_verification: dict[str, int] = Field(default_factory=dict)
