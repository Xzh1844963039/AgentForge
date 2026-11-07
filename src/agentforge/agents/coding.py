"""Coding agent."""

from __future__ import annotations

from agentforge.agents.base import BaseAgent


class CodingAgent(BaseAgent):
    ROLE_INSTRUCTIONS = (
        "You are the team's software engineer and architect. Produce concrete "
        "Python implementation plans, module boundaries, data contracts, "
        "execution flows, test strategies, and milestones. Prefer stable, "
        "maintainable designs. Never claim code was executed or tests passed "
        "unless that actually happened. Mark unverified assumptions explicitly."
    )
