"""Agent identity and capability models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class AgentRole(str, Enum):
    COORDINATOR = "coordinator"
    RESEARCH = "research"
    CODING = "coding"
    REVIEWER = "reviewer"


class AgentProfile(BaseModel):
    """Static configuration of an agent."""

    agent_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    name: str = Field(min_length=1)
    role: AgentRole
    capabilities: list[str] = Field(min_length=1)
    description: str = ""
    system_instructions: str = ""

    @field_validator("capabilities")
    @classmethod
    def _normalize_capabilities(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for item in value:
            token = item.strip().lower().replace(" ", "_").replace("-", "_")
            if token and token not in seen:
                seen.add(token)
                cleaned.append(token)
        if not cleaned:
            raise ValueError("an agent needs at least one capability")
        return cleaned


class AgentCard(BaseModel):
    """Public, prompt-safe view of an agent used for capability matching."""

    agent_id: str
    name: str
    role: AgentRole
    capabilities: list[str]
    description: str = ""

    @classmethod
    def from_profile(cls, profile: AgentProfile) -> "AgentCard":
        return cls(
            agent_id=profile.agent_id,
            name=profile.name,
            role=profile.role,
            capabilities=list(profile.capabilities),
            description=profile.description,
        )
