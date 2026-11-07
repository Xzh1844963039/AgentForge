"""Deterministic mock LLM provider.

This provider makes **no network calls** and is used for automated tests and the
offline demo. Its responses are fixed, role-specific fixtures; they must never
be presented as real LLM output in formal evaluation.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Sequence

from agentforge.llm.base import (
    BaseLLMProvider,
    ChatMessage,
    LLMResponse,
    TokenUsage,
)

_PLAN = {
    "goal_summary": (
        "Research the benefits of multi-agent systems and propose a concrete, "
        "reviewed Python implementation plan."
    ),
    "subtasks": [
        {
            "key": "benefits_research",
            "description": (
                "Research the concrete benefits and trade-offs of multi-agent LLM "
                "systems, covering specialisation, parallelism, robustness, cost and "
                "latency overhead, coordination risk, and auditability."
            ),
            "required_capabilities": ["research"],
            "depends_on": [],
            "kind": "research",
        },
        {
            "key": "python_implementation_plan",
            "description": (
                "Propose a concrete Python implementation plan for a multi-agent "
                "collaborative platform: architecture, core modules, orchestration "
                "strategy, persistence, testing, milestones, and risks."
            ),
            "required_capabilities": ["coding"],
            "depends_on": ["benefits_research"],
            "kind": "coding",
        },
    ],
}

_RESEARCH = """## Research: Benefits of Multi-Agent Systems

Multi-agent systems (MAS) assign a complex goal to several specialised agents that exchange messages and coordinate through an orchestrator. In current LLM-agent practice, the recurring benefits are:

1. **Specialisation and answer quality.** A narrow role prompt, a small tool set, and role-specific memory reduce context dilution. A research agent can focus on evidence gathering while a coding agent concentrates on architecture and implementation detail.

2. **Decomposition of complexity.** An explicit task graph turns an open-ended goal into smaller units with clear inputs, dependencies, and acceptance criteria. This makes progress observable and failures local rather than global.

3. **Parallelism.** Independent subtasks can execute concurrently. Even when a naive implementation is sequential, the DAG representation preserves opportunities for wall-clock speedups and for scaling out separate agents.

4. **Independent verification.** A reviewer agent that did not produce the work can catch omissions, contradictions, and unsafe assumptions. This separation of generation and critique is one of the strongest practical arguments for multi-agent designs.

5. **Robustness and graceful degradation.** If one agent fails, the orchestrator can retry, re-route, or ask a human, while preserving completed results. A single monolithic prompt has no comparable recovery point.

6. **Modular extensibility.** New capabilities such as web search, code execution, or repository management can be introduced as agents or tools without rewriting the coordination layer.

7. **Auditability.** Durable messages, task records, and execution traces make it possible to reconstruct who did what, why a branch failed, and how much time and money the workflow consumed.

**Trade-offs are real.** Coordination adds token overhead and latency; free-form agent conversation can drift or loop; errors can propagate through dependent tasks; shared state needs explicit ownership; and evaluation becomes harder because quality is distributed across agents. These risks justify hard limits on delegation depth and execution steps, mandatory review gates, per-agent workspaces, and complete observability.

**Conclusion.** Multi-agent architectures pay off for decomposable, open-ended tasks where specialisation and independent review matter more than the additional coordination cost. They are a poor fit for small, latency-sensitive requests that a single model can answer directly."""

_CODING = """## Python Implementation Plan for a Multi-Agent Collaborative Platform

**Target stack.** Python 3.11+, asyncio for concurrent I/O, Pydantic v2 for contracts, httpx for provider calls, SQLite for durable state, and pytest for automated verification.

### 1. Core modules

- `models/`: immutable data contracts for messages, conversations, tasks, agent actions, executions, and workflows.
- `llm/`: a single async provider interface with DeepSeek, HKGAI-compatible, and deterministic mock implementations.
- `messaging/`: conversation management and a message bus that persists, broadcasts, and delivers messages to agent inboxes.
- `agents/`: a `BaseAgent` plus Coordinator, Research, Coding, and Reviewer roles. Agents return structured actions: complete, delegate, or fail.
- `orchestration/`: plan validation, capability-based assignment, a DAG executor, bounded delegation, and workflow-level metrics.
- `memory/`: per-agent namespaces with episodic task outcomes; the interface leaves room for reflection, importance scoring, and semantic retrieval.
- `tools/` and `sandbox/`: stable tool and sandbox interfaces; real tool execution and Docker isolation arrive in later phases.
- `persistence/`: SQLite repositories plus a JSONL event stream for audit and debugging.
- `evaluation/`: completion rate, execution time, token usage, delegation depth, and failure accounting.
- `cli.py` and `runtime.py`: a thin interactive shell over the runtime.

### 2. Execution model

1. A human publishes a goal into a group conversation.
2. The Coordinator asks the LLM for a structured JSON plan using the live capability catalogue; the plan is validated as a DAG.
3. A capability matcher assigns every subtask to an agent that covers all required capabilities.
4. Task requests travel through the message bus. Each agent runs one LLM call with dependency results and its own memory context, and returns a structured action.
5. Delegation creates bounded child tasks. The parent is blocked until children finish, then resumed with their results.
6. The Coordinator synthesises the results; the Reviewer audits the draft; the Coordinator produces the final deliverable.
7. Every message, task transition, and LLM invocation is persisted. Metrics are computed from the durable records.

### 3. Persistence and safety

SQLite tables cover conversations, messages, tasks, executions, workflows, and memory entries. A JSONL event log records lifecycle events. Hard limits on plan size, delegation depth, delegation rounds, total tasks, and execution steps prevent runaway loops. API keys come from environment variables and are never logged.

### 4. Testing and evaluation

Unit tests cover models, state transitions, capability matching, providers, and persistence. Integration tests exercise private chat, group chat, agent-to-agent messages, delegation, failure propagation, and the full mock demo. Provider tests use `httpx.MockTransport`, so no network access is required. The mock provider is explicitly labelled and excluded from formal LLM evaluation.

### 5. Milestones

- **M1:** runtime, models, provider abstraction, mock demo (one week).
- **M2:** durable memory, reflection, experience reuse (one week).
- **M3:** tools, Docker sandboxes, private filesystems (two weeks).
- **M4:** human-in-the-loop approvals, pause/resume, permissions (one week).
- **M5:** search and GitHub automation, end-to-end evaluation (two weeks).

### 6. Key risks

Token cost and latency require limits and caching; coordination quality depends on plan validation; isolated sandboxes need resource quotas; and evaluation needs both automated metrics and human rubric review."""

_SYNTHESIS = """# Draft Answer: Multi-Agent Systems and a Python Implementation Plan

## Executive summary

Multi-agent systems are valuable when a goal is large enough to decompose and when independent review materially improves quality. They trade coordination cost for specialisation, parallelism, robustness, and auditability. A disciplined implementation should therefore begin with a small, observable runtime: explicit message contracts, a validated task graph, capability-based assignment, and hard limits on delegation.

## Benefits that justify the architecture

The strongest benefits are specialisation, decomposition of complexity, independent verification, graceful degradation, modular extensibility, and auditability. Parallelism is a bonus rather than the primary motivation: many useful workflows are mostly sequential but still benefit from role separation. The main costs are token overhead, added latency, coordination drift, error propagation through dependencies, and harder evaluation. These costs are manageable when the platform enforces bounded task graphs, explicit review gates, and complete execution traces.

## Python implementation plan

The implementation should be layered. Pydantic models define messages, tasks, and structured agent actions. A provider interface isolates DeepSeek and HKGAI-compatible endpoints behind one async API. A message bus persists and routes direct, group, and agent-to-agent messages. Four agents cover coordination, research, coding, and review, each with an independent memory namespace. The orchestrator validates an LLM-generated DAG, assigns tasks by capability coverage, executes ready tasks, and permits only bounded delegation. SQLite stores durable state; JSONL records lifecycle events; metrics are derived from the persisted records. The CLI exposes agent inspection, private chat, group goals, task status, message replay, and workflow history.

## Delivery sequence

Start with the runtime and mock provider so the entire flow is testable without network access. Then add memory evolution, sandboxed tools, human approvals, and external integrations. Keep the mock provider strictly for tests and demos, and run real-API acceptance tests separately with recorded configuration.

## Open questions for review

The draft assumes that capability tokens are sufficient for assignment, that sequential execution is acceptable for the first milestone, and that a single reviewer is enough. It does not yet quantify token cost or define a human approval policy."""

_REVIEW = """Verdict: APPROVED_WITH_ISSUES

## Summary

The draft is coherent, technically feasible, and correctly frames multi-agent systems as a trade-off rather than a universal improvement. The proposed Python architecture is modular, testable offline, and appropriately scoped. However, several risks need explicit treatment before the plan is considered production-ready.

## Issues

1. **Cost and latency are under-specified.** The draft mentions token overhead but gives no budget, caching strategy, or per-workflow cap. Multi-agent flows can multiply calls by four to eight times compared with a single prompt.
2. **Error propagation through the task graph.** A failed research task currently blocks every dependent task and the final synthesis. The plan needs a partial-result policy and a clear retry/fallback boundary.
3. **Shared-state ownership.** Multiple agents may write to memory and files. The plan should define per-agent namespaces and immutable task inputs to avoid conflicting writes.
4. **Delegation loop risk.** The draft mentions limits but not how they are measured. Depth, rounds, total tasks, and per-workflow LLM calls must all be enforced by the orchestrator, not by prompt instructions.
5. **Evaluation is still qualitative.** Completion rate and execution time are necessary but not sufficient. The plan should add task-level success criteria, review verdicts, and a human rubric.
6. **Security of future tools.** Web access, Python execution, and GitHub credentials need sandboxing, allow-lists, and human approval. These are future phases but the interfaces should not assume trusted execution.
7. **Deterministic mock fidelity.** The mock provider validates mechanics, not model quality. Reports must label mock runs clearly and keep real-API acceptance tests separate.

## Suggestions

- Add a configurable per-workflow token and wall-clock budget, and fail fast when exceeded.
- Persist partial results and let the Coordinator synthesise with explicit gaps.
- Record assignment rationale and every delegation edge for audit.
- Define acceptance criteria per subtask and have the Reviewer check them explicitly.
- Introduce a human approval gate before any future write-capable tool runs.
- Keep deterministic mock fixtures in tests and mark them as non-evaluable output.

## Decision

Approved for implementation planning with the issues above tracked as Phase 1/2 hardening items. The core architecture does not need to change."""

_FINAL = """# Final Report: Multi-Agent Systems and a Reviewed Python Implementation Plan

## Executive summary

Multi-agent systems are a good fit for large, decomposable goals where specialised roles and independent review outweigh coordination overhead. The recommended design is a small, observable runtime built around explicit message and task contracts, capability-based assignment, a validated DAG executor, bounded delegation, and durable observability. Multi-agent use should be selective: a single model remains the better choice for short, latency-sensitive requests.

## 1. Benefits of multi-agent systems

- **Specialisation.** Narrow role prompts and tool sets reduce context dilution and improve depth on research, coding, or review tasks.
- **Decomposition.** A task graph makes an open-ended goal actionable, observable, and recoverable at subtask granularity.
- **Independent verification.** A separate reviewer catches omissions and contradictions that the producing agent is biased to miss.
- **Robustness.** Failures are local: completed work is preserved, and the orchestrator can retry, reassign, or escalate.
- **Extensibility and auditability.** New capabilities can be added as agents or tools, and every step leaves a durable trace.
- **Parallelism.** Independent branches can run concurrently, although many useful workflows are primarily sequential.

The costs are equally important: additional token spend, higher latency, coordination drift, error propagation through dependencies, and harder evaluation. Hard limits, explicit review gates, and complete traces are the mitigations.

## 2. Python implementation plan

**Stack:** Python 3.11+, asyncio, Pydantic v2, httpx, SQLite, pytest.

**Layers:**

1. **Domain models** for messages, conversations, tasks, agent actions, executions, and workflows.
2. **LLM providers** behind one async interface: DeepSeek, an HKGAI-compatible endpoint, and a deterministic mock for tests and demos.
3. **Messaging** with persisted direct, group, task-request, task-result, delegation, and system messages.
4. **Agents** for coordination, research, coding, and review. Each returns a structured action: complete, delegate, or fail.
5. **Orchestration** with JSON plan validation, cycle detection, capability matching, dependency-ordered execution, bounded delegation, and failure propagation.
6. **Memory** isolated per agent, recording task outcomes and supporting keyword/recency recall; reflection and semantic retrieval are future work.
7. **Persistence and evaluation** using SQLite for state, JSONL for events, and computed metrics for completion rate, execution time, token usage, and delegation depth.
8. **CLI** for agent inspection, private chat, group goals, task status, message replay, and workflow history.

**Delivery sequence:** runtime and mock demo first; then memory evolution; then tools and Docker sandboxes; then human approvals; then search and GitHub automation. The design keeps the provider and tool interfaces stable so later phases add capability rather than rewrites.

## 3. Review findings and mitigations

The review approved the plan with issues. Each issue has a concrete response:

- **Cost/latency:** add per-workflow token and wall-clock budgets, fail fast when exceeded, and keep the mock path offline.
- **Error propagation:** persist partial results and allow synthesis with explicit gaps rather than treating every dependency failure as fatal in future phases.
- **Shared state:** enforce per-agent memory namespaces and immutable task inputs; filesystem isolation arrives with sandboxes.
- **Delegation loops:** enforce depth, rounds, task count, and execution-step limits inside the orchestrator rather than trusting prompts.
- **Evaluation:** track completion rate, execution time, token usage, review verdicts, and a human rubric; label mock results as non-evaluable.
- **Security:** require sandboxing, allow-lists, and human approval before any write-capable tool is enabled.

## 4. Recommendation

Proceed with the layered Python plan. Keep Phase 1 deliberately small and fully testable offline. Add memory evolution and sandboxed tools only after the runtime's message, task, and observability contracts are stable. Treat real-API validation as a separate acceptance gate and never substitute mock output for measured LLM quality.

## 5. Next steps

1. Finalise the Phase 1 runtime and its automated tests.
2. Add reflection-based memory and cross-task experience reuse.
3. Implement Docker-isolated tools and private per-agent filesystems.
4. Introduce human approval and pause/resume semantics.
5. Add web search, GitHub workflows, and end-to-end evaluation dashboards."""

_CHAT_RESPONSES = {
    "coordinator": (
        "CoordinatorAgent here. I can decompose a complex goal, assign subtasks to "
        "agents by capability, track dependencies, and consolidate the final report. "
        "For a full workflow, use the group task entry point. What goal should I plan?"
    ),
    "research": (
        "ResearchAgent here. I focus on evidence gathering, comparison, and structured "
        "summaries. Share a research question and the scope or constraints you care "
        "about, and I will return a concise, source-oriented analysis."
    ),
    "coding": (
        "CodingAgent here. I can propose Python architectures, module boundaries, data "
        "models, test plans, and implementation milestones. Tell me the target runtime, "
        "constraints, and expected scale so the plan is concrete."
    ),
    "reviewer": (
        "ReviewerAgent here. I audit drafts for correctness, completeness, consistency, "
        "feasibility, and risk, then return a verdict with actionable issues. Send me "
        "the draft or the plan you want reviewed."
    ),
}


class MockProvider(BaseLLMProvider):
    """Deterministic provider for tests and the offline demo."""

    name = "mock"
    model = "mock-deterministic-v1"

    def __init__(
        self,
        *,
        latency_ms: int = 0,
        default_temperature: float = 0.2,
        default_max_tokens: int = 2048,
        scripted_responses: Sequence[str | LLMResponse] | None = None,
    ) -> None:
        super().__init__(
            default_temperature=default_temperature,
            default_max_tokens=default_max_tokens,
        )
        self.latency_ms = max(0, latency_ms)
        self._scripted = list(scripted_responses or [])
        self.calls: list[dict[str, Any]] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def remaining_scripted(self) -> int:
        return len(self._scripted)

    async def complete(
        self,
        messages: Sequence[ChatMessage],
        *,
        purpose: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        if self.latency_ms:
            await asyncio.sleep(self.latency_ms / 1000.0)

        prompt = "\n\n".join(message.content for message in messages)
        self.calls.append(
            {
                "purpose": purpose or "generic",
                "messages": [message.model_dump(mode="json") for message in messages],
            }
        )

        if self._scripted:
            scripted = self._scripted.pop(0)
            if isinstance(scripted, LLMResponse):
                response = scripted.model_copy(deep=True)
            else:
                response = LLMResponse(
                    content=scripted,
                    provider=self.name,
                    model=self.model,
                    usage=self._usage(prompt, scripted),
                )
            response.latency_ms = (time.perf_counter() - started) * 1000.0
            return response

        content = self._select_content(purpose or "generic", prompt)
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=self._usage(prompt, content),
            latency_ms=(time.perf_counter() - started) * 1000.0,
            finish_reason="stop",
            raw={"mock": True},
        )

    def _select_content(self, purpose: str, prompt: str) -> str:
        if purpose == "plan":
            return self._plan_json(prompt)
        if purpose.startswith("execute:"):
            parts = purpose.split(":")
            kind = parts[1] if len(parts) > 1 else "generic"
            role = parts[2] if len(parts) > 2 else "coordinator"
            return self._execute_content(kind, role, prompt)
        if purpose.startswith("chat:"):
            role = purpose.split(":", 1)[1]
            return _CHAT_RESPONSES.get(role, _CHAT_RESPONSES["coordinator"])
        return f"[MOCK] Deterministic response for purpose={purpose!r}."

    def _plan_json(self, prompt: str) -> str:
        goal = _extract_section(prompt, "GOAL") or _PLAN["goal_summary"]
        plan = dict(_PLAN)
        plan["goal_summary"] = goal[:500]
        plan["subtasks"] = [dict(item) for item in _PLAN["subtasks"]]
        return json.dumps(plan, indent=2, ensure_ascii=False)

    def _execute_content(self, kind: str, role: str, prompt: str) -> str:
        content = {
            "research": _RESEARCH,
            "coding": _CODING,
            "synthesis": _SYNTHESIS,
            "review": _REVIEW,
            "finalize": self._finalize_content(prompt),
        }.get(kind)

        if content is None:
            if kind == "delegated":
                content = {
                    "research": _RESEARCH,
                    "coding": _CODING,
                    "reviewer": _REVIEW,
                    "coordinator": _SYNTHESIS,
                }.get(role, _generic_completion(role, prompt))
            else:
                content = _generic_completion(role, prompt)

        return json.dumps(
            {
                "action": "complete",
                "content": content,
                "reasoning": (
                    "Deterministic mock response selected by task kind and agent role."
                ),
            },
            ensure_ascii=False,
        )

    def _finalize_content(self, prompt: str) -> str:
        goal = _extract_section(prompt, "GOAL")
        if not goal:
            return _FINAL
        return f"{_FINAL}\n\n---\n\n**Goal addressed:** {goal.strip()[:1000]}"

    @staticmethod
    def _usage(prompt: str, completion: str) -> TokenUsage:
        prompt_tokens = max(1, len(prompt) // 4)
        completion_tokens = max(1, len(completion) // 4)
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )


def _generic_completion(role: str, prompt: str) -> str:
    task = _extract_section(prompt, "TASK") or "the requested task"
    excerpt = task.strip().splitlines()[0][:300]
    return (
        f"## Mock completion from {role}\n\n"
        f"The deterministic mock provider cannot perform real reasoning. It "
        f"acknowledged the task:\n\n> {excerpt}\n\n"
        "Configure a DeepSeek or HKGAI provider for real model output."
    )


def _extract_section(text: str, header: str) -> str:
    marker = f"[{header}]"
    if marker not in text:
        return ""
    tail = text.split(marker, 1)[1].lstrip("\n")
    match = re.search(r"\n\[[A-Z_]+\]", tail)
    if match:
        tail = tail[: match.start()]
    return tail.strip()
