"""Capability-based assignment tests."""

from __future__ import annotations

import pytest

from agentforge.agents.factory import DEFAULT_PROFILES
from agentforge.models.agent import AgentCard, AgentRole
from agentforge.orchestration.assignment import CapabilityAssigner
from agentforge.orchestration.errors import TaskAssignmentError


def default_assigner() -> CapabilityAssigner:
    return CapabilityAssigner([AgentCard.from_profile(p) for p in DEFAULT_PROFILES])


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        ("research", "research"),
        ("coding", "coding"),
        ("synthesis", "coordinator"),
        ("review", "reviewer"),
        ("RESEARCH", "research"),
        ("software-architecture", "coding"),
    ],
)
def test_capability_routing(capability, expected):
    decision = default_assigner().assign([capability])
    assert decision.card.agent_id == expected
    assert decision.score == 1.0


def test_no_single_agent_covers_multiple_capabilities():
    with pytest.raises(TaskAssignmentError, match="no registered agent covers"):
        default_assigner().assign(["research", "coding"])


def test_empty_capabilities_rejected():
    with pytest.raises(TaskAssignmentError):
        default_assigner().assign([])


def test_deterministic_tie_break():
    cards = [
        AgentCard(
            agent_id="zeta",
            name="Zeta",
            role=AgentRole.RESEARCH,
            capabilities=["x"],
        ),
        AgentCard(
            agent_id="alpha",
            name="Alpha",
            role=AgentRole.RESEARCH,
            capabilities=["x"],
        ),
    ]
    decision = CapabilityAssigner(cards).assign(["x"])
    assert decision.card.agent_id == "alpha"


def test_candidates_expose_partial_scores():
    candidates = default_assigner().candidates(["research", "coding"])
    assert candidates[0].score == 0.5
    assert len(candidates) == 4
