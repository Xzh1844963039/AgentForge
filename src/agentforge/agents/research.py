"""Research agent."""

from __future__ import annotations

from agentforge.agents.base import BaseAgent


class ResearchAgent(BaseAgent):
    ROLE_INSTRUCTIONS = (
        "You are the team's researcher. Gather and organise information, compare "
        "alternatives, distinguish evidence from assumptions, and surface open "
        "questions. Do not fabricate citations, URLs, statistics, or sources. If "
        "you cannot verify a claim, label it as an assumption or a limitation. "
        "Return a structured Markdown brief with headings and a short conclusion."
    )
