"""Report evidence propagation tests: audit facts vs. Mock prose."""

from __future__ import annotations

import json
from datetime import timedelta


from agentforge.llm.base import ChatMessage
from agentforge.llm.mock import MockProvider
from agentforge.models.common import utc_now

STDOUT = "Primes up to 20: [2, 3, 5, 7, 11, 13, 17, 19]"


def evidence(
    *,
    tool_name: str = "python_execute",
    status: str = "success",
    success: bool = True,
    stdout: str = STDOUT,
    stderr: str = "",
    exit_code: int | None = 0,
    error: str | None = None,
    duration: float = 0.123,
    metadata: dict | None = None,
    tool_call_id: str = "tc_exec",
    task_id: str = "task_coding",
) -> dict:
    now = utc_now()
    return {
        "tool_call_id": tool_call_id,
        "workflow_id": "wf_test",
        "task_id": task_id,
        "agent_id": "coding",
        "tool_name": tool_name,
        "status": status,
        "success": success,
        "output": {
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "timed_out": status == "timeout",
            "duration_seconds": duration,
            "script_path": "scripts/primes.py",
            "output_truncated": False,
        },
        "error": error,
        "duration_seconds": duration + 0.01,
        "approval_status": "not_required",
        "permissions": ["python_execute"],
        "started_at": now.isoformat(),
        "finished_at": (now + timedelta(seconds=duration)).isoformat(),
        "metadata": metadata
        or {
            "container": "agentforge-sandbox-coding-deadbeef",
            "script": "scripts/primes.py",
        },
    }


def prompt_for(evidence_items: list[dict], *, goal: str = "Calculate prime numbers") -> str:
    return (
        f"[GOAL]\n{goal}\n\n"
        f"[TOOL_EVIDENCE]\n{json.dumps(evidence_items, ensure_ascii=False)}\n\n"
        "[CONSTRAINTS]\nUse the recorded facts."
    )


async def call(provider: MockProvider, purpose: str, prompt: str) -> str:
    response = await provider.complete([ChatMessage.user(prompt)], purpose=purpose)
    payload = json.loads(response.content)
    assert payload["action"] == "complete"
    return payload["content"]


async def test_normal_execution_report_uses_real_stdout():
    provider = MockProvider()
    evidence_items = [
        evidence(
            tool_name="python_syntax_check",
            stdout="",
            exit_code=None,
            tool_call_id="tc_syntax",
        ),
        evidence(),
    ]
    prompt = prompt_for(evidence_items)
    synthesis = await call(provider, "execute:synthesis:coordinator", prompt)
    review = await call(provider, "execute:review:reviewer", prompt)
    final = await call(provider, "execute:finalize:coordinator", prompt)

    for text in (synthesis, review, final):
        assert STDOUT in text
        assert "Exit code: `0`" in text
        assert "(not available)" not in text
        assert "Exit code: unknown" not in text

    assert "agentforge-sandbox-coding-deadbeef" in final
    assert "scripts/primes.py" in final
    assert "0.123s" in final
    assert "APPROVED_WITH_ISSUES" in review
    assert "requires a working Docker daemon" not in review
    assert "Docker sandbox" in review
    assert "Tool calls recorded: 2" in final


async def test_empty_stdout_is_reported_as_empty_not_missing():
    provider = MockProvider()
    prompt = prompt_for([evidence(stdout="")])
    review = await call(provider, "execute:review:reviewer", prompt)
    final = await call(provider, "execute:finalize:coordinator", prompt)
    assert "stdout:\n```text\n(empty)" in final
    assert "Exit code: `0`" in final
    assert "(not available)" not in final
    assert "Empty stdout" in review


async def test_nonzero_exit_is_rejected_and_reported():
    provider = MockProvider()
    item = evidence(
        status="error",
        success=False,
        stdout="",
        stderr="Traceback: boom",
        exit_code=1,
        error="python exited with code 1",
    )
    prompt = prompt_for([item])
    review = await call(provider, "execute:review:reviewer", prompt)
    final = await call(provider, "execute:finalize:coordinator", prompt)
    assert review.startswith("Verdict: REJECTED")
    assert "Exit code: `1`" in review
    assert "Traceback: boom" in review
    assert "Verdict: `REJECTED`" in final
    assert "Exit code: `1`" in final
    assert "Traceback: boom" in final
    assert "APPROVED" not in final


async def test_exception_evidence_does_not_fabricate_stdout():
    provider = MockProvider()
    item = evidence(
        status="error",
        success=False,
        stdout="",
        stderr="",
        exit_code=None,
        error="Docker sandbox unavailable: daemon offline",
    )
    prompt = prompt_for([item])
    review = await call(provider, "execute:review:reviewer", prompt)
    final = await call(provider, "execute:finalize:coordinator", prompt)
    assert "Verdict: REJECTED" in review
    assert "Docker sandbox unavailable" in review
    assert STDOUT not in final
    assert "Docker sandbox unavailable" in final
    assert "Exit code: `missing`" in final
    assert "(not available)" not in final


async def test_missing_exit_code_is_not_treated_as_success():
    provider = MockProvider()
    item = evidence(status="success", success=True, stdout=STDOUT, exit_code=None)
    prompt = prompt_for([item])
    review = await call(provider, "execute:review:reviewer", prompt)
    final = await call(provider, "execute:finalize:coordinator", prompt)
    assert "Verdict: REJECTED" in review
    assert "Exit code: `missing`" in review
    assert "Verdict: `REJECTED`" in final
    assert "Exit code: `missing`" in final


async def test_timeout_evidence_is_reported():
    provider = MockProvider()
    item = evidence(
        status="timeout",
        success=False,
        stdout="",
        stderr="",
        exit_code=124,
        error="tool timed out after 30s",
        duration=30.0,
    )
    prompt = prompt_for([item])
    review = await call(provider, "execute:review:reviewer", prompt)
    final = await call(provider, "execute:finalize:coordinator", prompt)
    assert "Verdict: REJECTED" in review
    assert "timeout" in review.lower()
    assert "124" in final
    assert "30.000s" in final


async def test_multiple_tool_calls_are_all_visible():
    provider = MockProvider()
    syntax = evidence(
        tool_name="python_syntax_check",
        stdout="",
        exit_code=None,
        tool_call_id="tc_syntax",
    )
    execution = evidence(tool_call_id="tc_exec")
    final = await call(
        provider,
        "execute:finalize:coordinator",
        prompt_for([syntax, execution]),
    )
    assert "Tool calls recorded: 2" in final
    assert "`python_syntax_check`" in final
    assert "`python_execute`" in final
    assert STDOUT in final


async def test_report_does_not_use_task_prose_when_evidence_disagrees():
    provider = MockProvider()
    item = evidence(stdout=STDOUT)
    prompt = prompt_for([item]) + (
        "\n\n[DEPENDENCY_RESULTS]\nThe task prose falsely claims stdout was "
        "'FAKE PROSE OUTPUT' and exit code 99."
    )
    final = await call(provider, "execute:finalize:coordinator", prompt)
    assert STDOUT in final
    assert "Exit code: `0`" in final
    assert "FAKE PROSE OUTPUT" not in final
    assert "exit code 99" not in final.lower()
