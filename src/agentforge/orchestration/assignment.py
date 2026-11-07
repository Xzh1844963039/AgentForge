"""Capability-based agent assignment."""

from __future__ import annotations

from dataclasses import dataclass

from agentforge.models.agent import AgentCard
from agentforge.orchestration.errors import TaskAssignmentError


def normalize_capability(value: str) -> str:
    return value.strip().lower().replace(" ", "_").replace("-", "_")


@dataclass(frozen=True)
class AssignmentCandidate:
    card: AgentCard
    matched: tuple[str, ...]
    score: float


class CapabilityAssigner:
    """Assign a task to an agent that covers all required capabilities.

    Requiring full coverage avoids silently sending research work to an agent
    that only knows how to review. If no single agent covers the task, the
    planner must split the task or the workflow fails with a clear error.
    """

    def __init__(self, agents: list[AgentCard]) -> None:
        if not agents:
            raise ValueError("at least one agent card is required")
        self._agents = list(agents)

    def candidates(self, required_capabilities: list[str]) -> list[AssignmentCandidate]:
        required = {
            normalize_capability(capability)
            for capability in required_capabilities
            if capability.strip()
        }
        if not required:
            raise TaskAssignmentError("task has no required capabilities")

        candidates: list[AssignmentCandidate] = []
        for card in self._agents:
            available = {normalize_capability(cap) for cap in card.capabilities}
            matched = sorted(required & available)
            score = len(matched) / len(required)
            candidates.append(
                AssignmentCandidate(card=card, matched=tuple(matched), score=score)
            )

        candidates.sort(
            key=lambda item: (
                -item.score,
                len(item.card.capabilities),
                item.card.agent_id,
            )
        )
        return candidates

    def assign(self, required_capabilities: list[str]) -> AssignmentCandidate:
        candidates = self.candidates(required_capabilities)
        best = candidates[0]
        if best.score < 1.0:
            missing = sorted(
                {
                    normalize_capability(cap)
                    for cap in required_capabilities
                    if cap.strip()
                }
                - set(best.matched)
            )
            catalog = "; ".join(
                f"{card.agent_id}={card.capabilities}" for card in self._agents
            )
            raise TaskAssignmentError(
                "no registered agent covers all required capabilities "
                f"{sorted(required_capabilities)}; best match {best.card.agent_id} "
                f"is missing {missing}. Registered agents: {catalog}"
            )
        return best
