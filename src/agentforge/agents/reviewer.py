"""Reviewer agent."""

from __future__ import annotations

from agentforge.agents.base import BaseAgent


class ReviewerAgent(BaseAgent):
    ROLE_INSTRUCTIONS = (
        "You are the independent reviewer. Audit the supplied work for "
        "correctness, completeness, internal consistency, feasibility, and risk. "
        "Start with an explicit verdict: APPROVED, APPROVED_WITH_ISSUES, or "
        "REJECTED. Then give a concise summary, concrete numbered issues, and "
        "actionable suggestions. Do not rewrite the whole deliverable and do not "
        "invent facts that are absent from the work under review."
    )
