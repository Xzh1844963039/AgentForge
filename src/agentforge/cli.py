"""AgentForge command-line interface.

Commands:
  agentforge demo                  Deterministic offline demo (mock provider)
  agentforge demo-tools            Phase 2 tool + Docker sandbox demo
  agentforge run "<goal>"          Execute a goal with the configured provider
  agentforge chat <agent_id>       Private chat with one agent
  agentforge repl                  Interactive shell
  agentforge agents                List agents and capabilities
  agentforge tools                 List tool contracts and permissions
  agentforge tool-calls            Inspect tool invocation audit records
  agentforge tasks [--workflow ID] Task status
  agentforge messages              Conversations / transcript replay
  agentforge history               Workflow history
  agentforge result <workflow_id>  Print a final report
  agentforge metrics [workflow_id] Print workflow metrics
  agentforge approvals ...        List/show/approve/reject human approvals
  agentforge clarification ...    List/show/answer clarification requests
  agentforge workflows ...        List/show/pause/resume/cancel workflows
  agentforge benchmark ...        Benchmark, ablation, failure and live-acceptance tools
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from agentforge import __version__
from agentforge.agents.factory import DEFAULT_PROFILES
from agentforge.agents.registry import AgentNotFoundError
from agentforge.config.settings import Settings
from agentforge.llm.base import ProviderConfigurationError
from agentforge.memory.models import (
    MemoryType,
    PromotionState,
    VerificationLevel,
)
from agentforge.models.agent import AgentCard
from agentforge.models.clarification import ClarificationStatus
from agentforge.models.tool import ApprovalStatus, ToolCallRecord
from agentforge.models.workflow import Workflow, WorkflowStatus
from agentforge.persistence.database import SQLiteStore
from agentforge.runtime import AgentForgeRuntime
from agentforge.sandbox.docker import DockerSandbox
from agentforge.tools.artifact_store import ArtifactStore
from agentforge.tools.builtins import build_default_tools
from agentforge.tools.workspace import WorkspaceManager

console = Console()
logger = logging.getLogger("agentforge.cli")

DEMO_GOAL = (
    "Research the benefits of multi-agent systems, propose a Python "
    "implementation plan, and review the plan for potential issues."
)

PHASE2_GOAL = (
    "Create a Python script that calculates prime numbers, execute it, verify "
    "the output, and produce a Markdown report summarizing the implementation "
    "and test results."
)

STATUS_STYLES = {
    "completed": "bold green",
    "failed": "bold red",
    "running": "bold yellow",
    "pending": "cyan",
    "blocked": "magenta",
    "success": "bold green",
    "error": "bold red",
    "timeout": "bold red",
    "denied": "bold magenta",
    "pending_approval": "bold yellow",
    "paused": "bold yellow",
    "cancelled": "bold red",
}


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge",
        description="AgentForge: multi-agent collaborative platform (Phase 5)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    _add_common_options(parser)

    subparsers = parser.add_subparsers(dest="command")

    demo = subparsers.add_parser("demo", help="run the deterministic offline demo")
    _add_common_options(demo, suppress_defaults=True)

    demo_tools = subparsers.add_parser(
        "demo-tools", help="run the Phase 2 tool + Docker sandbox demo"
    )
    _add_common_options(demo_tools, suppress_defaults=True)

    tools = subparsers.add_parser("tools", help="list tool contracts")
    _add_common_options(tools, suppress_defaults=True)

    tool_calls = subparsers.add_parser(
        "tool-calls", help="show tool invocation audit records"
    )
    _add_common_options(tool_calls, suppress_defaults=True)
    tool_calls.add_argument("--workflow", help="filter by workflow id")
    tool_calls.add_argument("--task", help="filter by task id")
    tool_calls.add_argument("--agent", help="filter by agent id")
    tool_calls.add_argument("--limit", type=int, default=100)

    memory = subparsers.add_parser("memory", help="Phase 3 memory tools")
    _add_common_options(memory, suppress_defaults=True)
    memory_sub = memory.add_subparsers(dest="memory_command")
    memory_list = memory_sub.add_parser("list", help="list memories")
    memory_list.add_argument("--agent")
    memory_list.add_argument("--namespace")
    memory_list.add_argument("--type", choices=[item.value for item in MemoryType])
    memory_list.add_argument(
        "--verification", choices=[item.value for item in VerificationLevel]
    )
    memory_list.add_argument("--limit", type=int, default=50)
    memory_search = memory_sub.add_parser("search", help="semantic memory search")
    memory_search.add_argument("query")
    memory_search.add_argument("--agent")
    memory_search.add_argument("--namespace")
    memory_search.add_argument("--include-mock", action="store_true")
    memory_search.add_argument("--include-unverified", action="store_true")
    memory_search.add_argument("--limit", type=int, default=20)
    memory_stats = memory_sub.add_parser("stats", help="memory statistics")
    memory_stats.add_argument("--agent")
    memory_candidates = memory_sub.add_parser(
        "candidates", help="list procedural candidates"
    )
    memory_candidates.add_argument("--agent")
    memory_candidates.add_argument(
        "--status", choices=[item.value for item in PromotionState]
    )
    memory_candidates.add_argument("--limit", type=int, default=50)
    memory_feedback = memory_sub.add_parser(
        "feedback", help="show retrieval feedback"
    )
    memory_feedback.add_argument("--workflow")
    memory_feedback.add_argument("--agent")
    memory_feedback.add_argument("--limit", type=int, default=50)
    memory_clear = memory_sub.add_parser("clear", help="clear memories (destructive)")
    memory_clear.add_argument("--agent")
    memory_clear.add_argument("--namespace")
    memory_clear.add_argument("--confirm", action="store_true")
    memory_experiment = memory_sub.add_parser(
        "experiment", help="run the with/without-memory controlled experiment"
    )
    memory_experiment.add_argument("--learning-runs", type=int, default=2)
    memory_experiment.add_argument("--experiment-dir")

    run = subparsers.add_parser("run", help="execute a goal")
    _add_common_options(run, suppress_defaults=True)
    run.add_argument("goal", nargs="+", help="goal text")

    chat = subparsers.add_parser("chat", help="private chat with one agent")
    _add_common_options(chat, suppress_defaults=True)
    chat.add_argument("agent_id", help="agent id, e.g. research")
    chat.add_argument("--message", "-m", help="send one message and exit")

    repl = subparsers.add_parser("repl", help="interactive command shell")
    _add_common_options(repl, suppress_defaults=True)

    agents = subparsers.add_parser("agents", help="list agents and capabilities")
    _add_common_options(agents, suppress_defaults=True)

    tasks = subparsers.add_parser("tasks", help="show task status")
    _add_common_options(tasks, suppress_defaults=True)
    tasks.add_argument("--workflow", help="filter by workflow id")
    tasks.add_argument("--limit", type=int, default=100)

    messages = subparsers.add_parser("messages", help="conversations and transcripts")
    _add_common_options(messages, suppress_defaults=True)
    messages.add_argument("--conversation", help="conversation id")
    messages.add_argument("--all", action="store_true", help="show messages from all chats")
    messages.add_argument("--limit", type=int, default=100)

    conversations = subparsers.add_parser(
        "conversations", help="list conversations"
    )
    _add_common_options(conversations, suppress_defaults=True)
    conversations.add_argument("--limit", type=int, default=50)

    history = subparsers.add_parser("history", help="workflow history")
    _add_common_options(history, suppress_defaults=True)
    history.add_argument("--limit", type=int, default=20)

    result = subparsers.add_parser("result", help="print a workflow final report")
    _add_common_options(result, suppress_defaults=True)
    result.add_argument("workflow_id")

    metrics = subparsers.add_parser("metrics", help="print workflow metrics")
    _add_common_options(metrics, suppress_defaults=True)
    metrics.add_argument("workflow_id", nargs="?")

    approvals = subparsers.add_parser(
        "approvals", help="list, inspect and decide human approvals"
    )
    _add_common_options(approvals, suppress_defaults=True)
    approvals_sub = approvals.add_subparsers(dest="approvals_command")
    approvals_list = approvals_sub.add_parser("list", help="list approval requests")
    approvals_list.add_argument("--workflow")
    approvals_list.add_argument(
        "--status",
        choices=[item.value for item in ApprovalStatus],
    )
    approvals_show = approvals_sub.add_parser("show", help="show one approval")
    approvals_show.add_argument("approval_id")
    approvals_approve = approvals_sub.add_parser("approve", help="approve an action")
    approvals_approve.add_argument("approval_id")
    approvals_approve.add_argument("--comment", default="")
    approvals_reject = approvals_sub.add_parser("reject", help="reject an action")
    approvals_reject.add_argument("approval_id")
    approvals_reject.add_argument("--comment", default="")

    clarification = subparsers.add_parser(
        "clarification", help="list, inspect and answer clarification requests"
    )
    _add_common_options(clarification, suppress_defaults=True)
    clarification_sub = clarification.add_subparsers(dest="clarification_command")
    clarification_list = clarification_sub.add_parser(
        "list", help="list clarification requests"
    )
    clarification_list.add_argument("--workflow")
    clarification_list.add_argument(
        "--status",
        choices=[item.value for item in ClarificationStatus],
    )
    clarification_show = clarification_sub.add_parser(
        "show", help="show one clarification request"
    )
    clarification_show.add_argument("clarification_id")
    clarification_answer = clarification_sub.add_parser(
        "answer", help="answer an open clarification request"
    )
    clarification_answer.add_argument("clarification_id")
    clarification_answer.add_argument("answer", nargs="+")

    workflows = subparsers.add_parser(
        "workflows", help="inspect and control workflow lifecycle"
    )
    _add_common_options(workflows, suppress_defaults=True)
    workflows_sub = workflows.add_subparsers(dest="workflows_command")
    workflows_list = workflows_sub.add_parser("list", help="list workflows")
    workflows_list.add_argument("--limit", type=int, default=20)
    workflows_show = workflows_sub.add_parser("show", help="show one workflow")
    workflows_show.add_argument("workflow_id")
    workflows_pause = workflows_sub.add_parser("pause", help="pause a workflow")
    workflows_pause.add_argument("workflow_id")
    workflows_resume = workflows_sub.add_parser("resume", help="resume a workflow")
    workflows_resume.add_argument("workflow_id")
    workflows_cancel = workflows_sub.add_parser("cancel", help="cancel a workflow")
    workflows_cancel.add_argument("workflow_id")
    workflows_cancel.add_argument("--reason", default="human_cancel")

    benchmark = subparsers.add_parser(
        "benchmark", help="Phase 5 benchmark, ablation and live-acceptance harness"
    )
    _add_common_options(benchmark, suppress_defaults=True)
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_command")

    benchmark_list = benchmark_sub.add_parser("list", help="list benchmark tasks")
    _add_common_options(benchmark_list, suppress_defaults=True)
    benchmark_list.add_argument("--dataset", default="benchmarks/dev")
    benchmark_list.add_argument("--category")

    benchmark_run = benchmark_sub.add_parser("run", help="run benchmark tasks")
    _add_common_options(benchmark_run, suppress_defaults=True)
    benchmark_run.add_argument("--dataset", default="benchmarks/dev")
    benchmark_run.add_argument("--task-id", action="append", default=[])
    benchmark_run.add_argument("--category", action="append", default=[])
    benchmark_run.add_argument(
        "--mode",
        choices=["multi_agent", "single_agent"],
        default="multi_agent",
    )
    benchmark_run.add_argument("--repeats", type=int, default=1)
    benchmark_run.add_argument("--output-dir", default="benchmarks/runs")
    benchmark_run.add_argument("--batch-dir")
    benchmark_run.add_argument("--resume", action="store_true")
    benchmark_run.add_argument("--max-tasks", type=int)
    benchmark_run.add_argument("--allow-live-api", action="store_true")
    benchmark_run.add_argument("--memory", action="store_true")

    benchmark_compare = benchmark_sub.add_parser(
        "compare", help="run the A/B/C ablation"
    )
    _add_common_options(benchmark_compare, suppress_defaults=True)
    benchmark_compare.add_argument("--dataset", default="benchmarks/dev")
    benchmark_compare.add_argument("--task-id", action="append", default=[])
    benchmark_compare.add_argument("--repeats", type=int, default=1)
    benchmark_compare.add_argument("--output-dir", default="benchmarks/runs")

    benchmark_report = benchmark_sub.add_parser(
        "report", help="regenerate a Markdown report"
    )
    _add_common_options(benchmark_report, suppress_defaults=True)
    benchmark_report.add_argument("--batch-dir")
    benchmark_report.add_argument("--output-dir", default="benchmarks/runs")
    benchmark_report.add_argument("--output")

    benchmark_failures = benchmark_sub.add_parser(
        "failures", help="show classified benchmark failures"
    )
    _add_common_options(benchmark_failures, suppress_defaults=True)
    benchmark_failures.add_argument("--batch-dir")
    benchmark_failures.add_argument("--output-dir", default="benchmarks/runs")

    benchmark_live = benchmark_sub.add_parser(
        "live", help="real-LLM acceptance checks (requires explicit authorization)"
    )
    _add_common_options(benchmark_live, suppress_defaults=True)
    benchmark_live.add_argument("--output-dir", default="benchmarks/runs")
    benchmark_live.add_argument("--allow-live-api", action="store_true")
    benchmark_live.add_argument(
        "--stage",
        choices=["all", "1", "2", "3", "4", "5", "6"],
        help="run staged acceptance instead of the full acceptance report",
    )
    benchmark_live.add_argument("--max-requests", type=int, default=120)
    benchmark_live.add_argument("--max-tokens", type=int, default=250_000)
    benchmark_live.add_argument("--skip-memory", action="store_true")

    benchmark_probe = benchmark_sub.add_parser(
        "hkgai-probe",
        help="single-request streaming probe for the failed HKGAI planning prompt",
    )
    _add_common_options(benchmark_probe, suppress_defaults=True)
    benchmark_probe.add_argument("--output-dir", default="benchmarks/runs")
    benchmark_probe.add_argument("--allow-live-api", action="store_true")
    benchmark_probe.add_argument("--max-tokens", type=int, default=256)
    benchmark_probe.add_argument("--timeout", type=float, default=120.0)

    benchmark_experiments = benchmark_sub.add_parser(
        "hkgai-experiments",
        help="two minimal HKGAI control experiments (A then B, max 2 requests)",
    )
    _add_common_options(benchmark_experiments, suppress_defaults=True)
    benchmark_experiments.add_argument("--output-dir", default="benchmarks/runs")
    benchmark_experiments.add_argument("--allow-live-api", action="store_true")
    benchmark_experiments.add_argument(
        "--experiment", choices=["A", "B", "AB"], default="AB"
    )
    benchmark_experiments.add_argument("--timeout", type=float, default=120.0)

    return parser


def _add_common_options(
    parser: argparse.ArgumentParser, *, suppress_defaults: bool = False
) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument(
        "--data-dir",
        dest="data_dir",
        default=default,
        help="data directory (default: data)",
    )
    parser.add_argument(
        "--provider",
        dest="provider",
        choices=["mock", "deepseek", "hkgai"],
        default=default,
        help="override the configured LLM provider",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default=default,
        help="logging level",
    )


def _make_settings(args: argparse.Namespace) -> Settings:
    overrides: dict = {}
    data_dir = getattr(args, "data_dir", None)
    provider = getattr(args, "provider", None)
    log_level = getattr(args, "log_level", None)
    if data_dir:
        overrides["data_dir"] = Path(data_dir)
    if provider:
        overrides["llm_provider"] = provider
    if log_level:
        overrides["log_level"] = log_level
    return Settings(**overrides)


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 0
    try:
        return asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        console.print("\nInterrupted.")
        return 130
    except ProviderConfigurationError as exc:
        console.print(Panel(Text(str(exc)), title="Configuration error", style="red"))
        return 2
    except AgentNotFoundError as exc:
        console.print(Text(str(exc), style="red"))
        return 2
    except Exception as exc:
        logger.exception("command failed")
        console.print(Text(f"{type(exc).__name__}: {exc}", style="bold red"))
        return 1


async def _dispatch(args: argparse.Namespace) -> int:
    command = args.command
    if command == "demo":
        return await _cmd_demo(args)
    if command == "demo-tools":
        return await _cmd_demo_tools(args)
    if command == "run":
        return await _cmd_run(args)
    if command == "chat":
        return await _cmd_chat(args)
    if command == "repl":
        return await _cmd_repl(args)
    if command == "agents":
        return _cmd_agents(args)
    if command == "tools":
        return await _cmd_tools(args)
    if command == "tool-calls":
        return _cmd_tool_calls(args)
    if command == "memory":
        return await _cmd_memory(args)
    if command == "tasks":
        return _cmd_tasks(args)
    if command == "messages":
        return _cmd_messages(args)
    if command == "conversations":
        return _cmd_conversations(args)
    if command == "history":
        return _cmd_history(args)
    if command == "result":
        return _cmd_result(args)
    if command == "metrics":
        return _cmd_metrics(args)
    if command == "approvals":
        return await _cmd_approvals(args)
    if command == "clarification":
        return await _cmd_clarification(args)
    if command == "workflows":
        return await _cmd_workflows(args)
    if command == "benchmark":
        return await _cmd_benchmark(args)
    console.print(Text(f"unknown command {command!r}", style="red"))
    return 2


async def _cmd_benchmark(args: argparse.Namespace) -> int:
    from agentforge.cli_benchmark import run_benchmark_command

    return await run_benchmark_command(
        args, settings_factory=_make_settings, console=console
    )


async def _cmd_memory(args: argparse.Namespace) -> int:
    from agentforge.cli_memory import run_memory_command

    return await run_memory_command(
        args, settings_factory=_make_settings, console=console
    )


# ---------------------------------------------------------------------- runtime cmds
async def _cmd_demo(args: argparse.Namespace) -> int:
    settings = _make_settings(args)
    explicit_real = getattr(args, "provider", None) in {"deepseek", "hkgai"}
    if not explicit_real:
        settings = settings.mock_copy()

    async with AgentForgeRuntime(settings) as runtime:
        console.rule("[bold]AgentForge Phase 1 Demo[/bold]")
        mode = (
            "MOCK provider - deterministic fixtures, NO external API calls"
            if runtime.provider_name == "mock"
            else f"REAL provider: {runtime.provider_name} ({runtime.provider_model})"
        )
        console.print(Text(f"Provider: {mode}", style="bold yellow"))
        console.print(Text(f"Goal: {DEMO_GOAL}", style="bold"))
        console.print()

        workflow = await runtime.submit_goal(DEMO_GOAL)
        if workflow.status != WorkflowStatus.COMPLETED:
            _render_workflow(console, runtime.store, workflow)
            console.print(Text(f"Demo failed: {workflow.error}", style="bold red"))
            return 1

        _render_workflow(console, runtime.store, workflow)
        _print_persistence(console, runtime)
        if runtime.provider_name == "mock":
            console.print()
            console.print(
                Panel(
                    Text(
                        "This run used deterministic mock fixtures. The message "
                        "routing, task DAG, delegation, persistence and metrics are "
                        "real; the LLM content is not. Set AGENTFORGE_LLM_PROVIDER="
                        "deepseek (with an API key) for real model output.",
                        style="yellow",
                    ),
                    title="Mock mode notice",
                )
            )
        return 0


async def _cmd_demo_tools(args: argparse.Namespace) -> int:
    available, detail = await asyncio.to_thread(DockerSandbox.check_available)
    if not available:
        console.print(
            Panel(
                Text(
                    "The Phase 2 end-to-end demo requires Docker. No host-side "
                    "Python fallback is used.\n\n"
                    f"Detected: {detail}\n\n"
                    "Required:\n"
                    "  1. Install Docker Desktop with WSL2.\n"
                    "  2. Start Docker Desktop.\n"
                    "  3. Verify: docker info\n"
                    "  4. Run: agentforge demo-tools\n\n"
                    "Docker integration tests will be SKIPPED until this is fixed; "
                    "non-Docker unit tests still run.",
                    style="yellow",
                ),
                title="Docker prerequisite missing",
                style="red",
            )
        )
        return 2

    settings = _make_settings(args)
    explicit_real = getattr(args, "provider", None) in {"deepseek", "hkgai"}
    if not explicit_real:
        settings = settings.mock_copy()
    if not settings.tools_enabled:
        console.print(Text("tools are disabled in configuration", style="red"))
        return 2

    async with AgentForgeRuntime(settings) as runtime:
        console.rule("[bold]AgentForge Phase 2 Tool + Sandbox Demo[/bold]")
        console.print(Text(f"Docker: {detail}", style="green"))
        console.print(
            Text(
                f"Provider: {runtime.provider_name} ({runtime.provider_model})",
                style="bold yellow" if runtime.provider_name == "mock" else "bold",
            )
        )
        console.print(Text(f"Goal: {PHASE2_GOAL}", style="bold"))
        console.print()
        workflow = await runtime.submit_goal(PHASE2_GOAL)
        _render_workflow(console, runtime.store, workflow)
        _print_persistence(console, runtime)
        if runtime.provider_name == "mock":
            console.print(
                Panel(
                    Text(
                        "LLM decisions are deterministic mock fixtures. File writes, "
                        "Python execution, stdout/stderr, exit codes, permissions and "
                        "the tool audit are real. Docker sandbox isolation is real; "
                        "no host-side Python fallback exists.",
                        style="yellow",
                    ),
                    title="Mock provider / real tools",
                )
            )
        return 0 if workflow.status == WorkflowStatus.COMPLETED else 1


async def _cmd_run(args: argparse.Namespace) -> int:
    goal = " ".join(args.goal).strip()
    if not goal:
        console.print(Text("goal must not be empty", style="red"))
        return 2
    settings = _make_settings(args)
    async with AgentForgeRuntime(settings) as runtime:
        console.print(
            Text(
                f"Provider: {runtime.provider_name} ({runtime.provider_model})",
                style="bold",
            )
        )
        if runtime.provider_name == "mock":
            console.print(
                Text(
                    "WARNING: mock provider output is deterministic test data, "
                    "not real LLM output.",
                    style="yellow",
                )
            )
        workflow = await runtime.submit_goal(goal)
        _render_workflow(console, runtime.store, workflow)
        _print_persistence(console, runtime)
        return 0 if workflow.status == WorkflowStatus.COMPLETED else 1


async def _cmd_chat(args: argparse.Namespace) -> int:
    settings = _make_settings(args)
    async with AgentForgeRuntime(settings) as runtime:
        agent = runtime.get_agent(args.agent_id)
        console.print(
            Text(
                f"Private chat with {agent.name} ({agent.agent_id}). "
                f"Provider: {runtime.provider_name}.",
                style="bold",
            )
        )
        if args.message:
            exchange = await runtime.chat_with_agent(agent.agent_id, args.message)
            console.print()
            console.print(Text(f"You: {args.message}", style="cyan"))
            console.print(Text(f"{agent.name}:", style="bold"))
            console.print(Markdown(exchange.outgoing.content))
            return 0

        console.print(
            Text("Type /exit to leave. Messages are persisted.", style="dim")
        )
        while True:
            try:
                text = console.input("[bold cyan]you> [/bold cyan]").strip()
            except EOFError:
                break
            if not text:
                continue
            if text.lower() in {"/exit", "/quit"}:
                break
            try:
                exchange = await runtime.chat_with_agent(agent.agent_id, text)
            except Exception as exc:
                console.print(Text(f"Error: {exc}", style="red"))
                continue
            console.print(Text(f"{agent.name}:", style="bold"))
            console.print(Markdown(exchange.outgoing.content))
        return 0


async def _cmd_repl(args: argparse.Namespace) -> int:
    settings = _make_settings(args)
    async with AgentForgeRuntime(settings) as runtime:
        console.rule("[bold]AgentForge REPL[/bold]")
        console.print(
            Text(
                f"Provider: {runtime.provider_name} ({runtime.provider_model}). "
                "Type /help for commands.",
                style="bold",
            )
        )
        while True:
            try:
                line = console.input("[bold cyan]agentforge> [/bold cyan]").strip()
            except EOFError:
                break
            if not line:
                continue
            if line.lower() in {"/quit", "/exit"}:
                break
            try:
                should_continue = await _handle_repl_command(line, runtime)
            except Exception as exc:
                console.print(Text(f"Error: {type(exc).__name__}: {exc}", style="red"))
                continue
            if not should_continue:
                break
        return 0


async def _handle_repl_command(line: str, runtime: AgentForgeRuntime) -> bool:
    if not line.startswith("/"):
        console.print(
            Text("Commands start with '/'. Use /goal <text> to run a workflow.", style="yellow")
        )
        return True

    command, _, rest = line.partition(" ")
    command = command.lower()
    rest = rest.strip()

    if command == "/help":
        console.print(
            Panel(
                Text(
                    "/agents                       list agents and capabilities\n"
                    "/tools                        list tool contracts\n"
                    "/tool-calls [workflow_id]     tool audit records\n"
                    "/dm <agent_id> <message>      private message to one agent\n"
                    "/goal <text>                  publish a goal to a group workflow\n"
                    "/tasks [workflow_id]          task status\n"
                    "/messages [conversation_id]   transcript replay\n"
                    "/conversations                list conversations\n"
                    "/history                      workflow history\n"
                    "/result <workflow_id>         print final report\n"
                    "/metrics [workflow_id]        workflow metrics\n"
                    "/approvals [workflow_id]      list approval requests\n"
                    "/approve <id> [comment]       approve a pending action\n"
                    "/reject <id> [comment]        reject a pending action\n"
                    "/clarifications [workflow_id] list clarification requests\n"
                    "/answer <id> <text>           answer a clarification\n"
                    "/workflows                    list workflows\n"
                    "/pause <workflow_id>          request workflow pause\n"
                    "/resume <workflow_id>         resume a paused workflow\n"
                    "/cancel <workflow_id>         cancel a workflow\n"
                    "/quit                         exit",
                ),
                title="REPL commands",
            )
        )
        return True

    if command == "/agents":
        _print_agents(console)
        return True

    if command == "/tools":
        _print_tool_specs(console, runtime.list_tool_specs())
        return True

    if command == "/tool-calls":
        _print_tool_calls(console, runtime.list_tool_calls(rest or None))
        return True

    if command == "/dm":
        agent_id, _, message = rest.partition(" ")
        if not agent_id or not message.strip():
            console.print(Text("usage: /dm <agent_id> <message>", style="yellow"))
            return True
        exchange = await runtime.chat_with_agent(agent_id, message.strip())
        console.print(Text(f"You: {message.strip()}", style="cyan"))
        console.print(Text(f"{exchange.outgoing.sender}:", style="bold"))
        console.print(Markdown(exchange.outgoing.content))
        return True

    if command == "/goal":
        if not rest:
            console.print(Text("usage: /goal <text>", style="yellow"))
            return True
        workflow = await runtime.submit_goal(rest)
        _render_workflow(console, runtime.store, workflow)
        return True

    if command == "/tasks":
        _cmd_tasks_from_store(runtime.store, workflow_id=rest or None)
        return True

    if command == "/messages":
        if rest:
            _print_transcript(runtime.store, rest)
        else:
            _print_conversations(runtime.store)
        return True

    if command == "/conversations":
        _print_conversations(runtime.store)
        return True

    if command == "/history":
        _print_history(runtime.store)
        return True

    if command == "/result":
        if not rest:
            console.print(Text("usage: /result <workflow_id>", style="yellow"))
            return True
        workflow = runtime.get_workflow(rest)
        if workflow is None:
            console.print(Text(f"unknown workflow {rest!r}", style="red"))
        else:
            _print_final(workflow)
        return True

    if command == "/metrics":
        _cmd_metrics_from_store(runtime.store, workflow_id=rest or None)
        return True

    if command == "/approvals":
        _print_approvals(console, runtime.list_approvals(rest or None))
        return True

    if command in {"/approve", "/reject"}:
        approval_id, _, comment = rest.partition(" ")
        if not approval_id:
            console.print(
                Text(f"usage: {command} <approval_id> [comment]", style="yellow")
            )
            return True
        if command == "/approve":
            approval = runtime.approve_tool(approval_id, comment=comment.strip())
        else:
            approval = runtime.reject_tool(approval_id, comment=comment.strip())
        console.print(
            Text(
                f"{approval.status.value}: {approval.approval_id}. "
                f"Resume with /resume {approval.workflow_id}",
                style="bold green" if command == "/approve" else "bold red",
            )
        )
        return True

    if command == "/clarifications":
        _print_clarifications(
            console, runtime.list_clarifications(rest or None)
        )
        return True

    if command == "/answer":
        clarification_id, _, answer = rest.partition(" ")
        if not clarification_id or not answer.strip():
            console.print(
                Text("usage: /answer <clarification_id> <text>", style="yellow")
            )
            return True
        request = runtime.answer_clarification(
            clarification_id, answer.strip()
        )
        console.print(
            Text(
                f"Answered {request.clarification_id}. "
                f"Resume with /resume {request.workflow_id}",
                style="bold green",
            )
        )
        return True

    if command == "/workflows":
        _print_workflows(runtime.list_workflows(20))
        return True

    if command == "/pause":
        if not rest:
            console.print(Text("usage: /pause <workflow_id>", style="yellow"))
            return True
        workflow = runtime.pause_workflow(rest)
        console.print(
            Text(
                f"{workflow.workflow_id} is {workflow.status.value}",
                style="bold yellow",
            )
        )
        return True

    if command == "/resume":
        if not rest:
            console.print(Text("usage: /resume <workflow_id>", style="yellow"))
            return True
        workflow = await runtime.resume_workflow(rest)
        _print_workflow_detail(runtime, workflow)
        return True

    if command == "/cancel":
        if not rest:
            console.print(Text("usage: /cancel <workflow_id>", style="yellow"))
            return True
        workflow = await runtime.cancel_workflow(rest)
        console.print(
            Text(
                f"{workflow.workflow_id} is {workflow.status.value}",
                style="bold red",
            )
        )
        return True

    if command == "/quit":
        return False

    console.print(Text(f"unknown command {command!r}; try /help", style="yellow"))
    return True


# ------------------------------------------------------------------- read-only cmds
def _cmd_agents(args: argparse.Namespace) -> int:
    _print_agents(console)
    return 0


async def _cmd_tools(args: argparse.Namespace) -> int:
    settings = _make_settings(args)
    settings.ensure_directories()
    store = SQLiteStore(settings.resolved_sqlite_file)
    workspaces = WorkspaceManager(settings.resolved_workspace_root)
    try:
        built = build_default_tools(
            settings,
            workspaces=workspaces,
            artifacts=ArtifactStore(
                settings.resolved_artifacts_root,
                workspaces,
                store,
                max_bytes=settings.max_file_bytes,
            ),
        )
        try:
            _print_tool_specs(console, built.registry.list_specs())
        finally:
            await built.search_provider.aclose()
    finally:
        store.close()
    return 0


def _cmd_tool_calls(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store is None:
        return 0
    try:
        records = store.list_tool_calls(
            args.workflow,
            task_id=args.task,
            agent_id=args.agent,
            limit=args.limit,
        )
        _print_tool_calls(console, records)
    finally:
        store.close()
    return 0


def _cmd_tasks(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store is None:
        return 0
    try:
        _cmd_tasks_from_store(store, workflow_id=args.workflow, limit=args.limit)
    finally:
        store.close()
    return 0


def _cmd_tasks_from_store(
    store: SQLiteStore, *, workflow_id: str | None, limit: int = 100
) -> None:
    tasks = store.list_tasks(workflow_id)[:limit]
    if not tasks:
        console.print(Text("No tasks found.", style="dim"))
        return
    kind_by_id = {
        task.task_id: str(task.metadata.get("kind", "generic")) for task in tasks
    }
    table = Table(title="Tasks", show_lines=False)
    table.add_column("Task", style="dim")
    table.add_column("Workflow", style="dim")
    table.add_column("Kind")
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Depth", justify="right")
    table.add_column("Deps")
    table.add_column("Duration", justify="right")
    for task in tasks:
        deps = ", ".join(kind_by_id.get(dep, dep[:6]) for dep in task.dependencies)
        duration = task.duration_seconds()
        table.add_row(
            task.task_id[:8],
            task.workflow_id[:8],
            str(task.metadata.get("kind", "generic")),
            task.assigned_agent or "-",
            Text(
                task.status.value,
                style=STATUS_STYLES.get(task.status.value, "white"),
            ),
            str(task.depth),
            deps or "-",
            f"{duration:.3f}s" if duration is not None else "-",
        )
    console.print(table)


def _cmd_messages(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store is None:
        return 0
    try:
        if args.conversation:
            _print_transcript(store, args.conversation, limit=args.limit)
        elif args.all:
            _print_all_messages(store, limit=args.limit)
        else:
            _print_conversations(store)
    finally:
        store.close()
    return 0


def _cmd_conversations(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store is None:
        return 0
    try:
        _print_conversations(store, limit=args.limit)
    finally:
        store.close()
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store is None:
        return 0
    try:
        _print_history(store, limit=args.limit)
    finally:
        store.close()
    return 0


def _cmd_result(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store is None:
        return 0
    try:
        workflow = store.get_workflow(args.workflow_id)
        if workflow is None:
            console.print(Text(f"unknown workflow {args.workflow_id!r}", style="red"))
            return 1
        _print_final(workflow)
        return 0
    finally:
        store.close()


def _cmd_metrics(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store is None:
        return 0
    try:
        return _cmd_metrics_from_store(store, workflow_id=args.workflow_id)
    finally:
        store.close()


def _cmd_metrics_from_store(
    store: SQLiteStore, *, workflow_id: str | None
) -> int:
    workflow = store.get_workflow(workflow_id) if workflow_id else None
    if workflow is None and workflow_id is None:
        recent = store.list_workflows(limit=1)
        workflow = recent[0] if recent else None
    if workflow is None:
        console.print(Text("No workflow found.", style="dim"))
        return 0
    console.print(
        Text(
            f"Workflow {workflow.workflow_id} | {workflow.status.value} | "
            f"{workflow.goal[:100]}",
            style="bold",
        )
    )
    _print_metrics(console, workflow)
    return 0


async def _cmd_approvals(args: argparse.Namespace) -> int:
    settings = _make_settings(args)
    async with AgentForgeRuntime(settings) as runtime:
        subcommand = getattr(args, "approvals_command", None) or "list"
        if subcommand == "list":
            status = (
                ApprovalStatus(args.status)
                if getattr(args, "status", None)
                else None
            )
            approvals = runtime.approval_service.list(
                getattr(args, "workflow", None), status=status
            )
            _print_approvals(console, approvals)
            return 0
        if subcommand == "show":
            approval = runtime.get_approval(args.approval_id)
            if approval is None:
                console.print(
                    Text(f"unknown approval {args.approval_id!r}", style="red")
                )
                return 1
            _print_approval_detail(console, approval)
            return 0
        if subcommand == "approve":
            approval = runtime.approve_tool(
                args.approval_id, comment=args.comment
            )
            console.print(
                Text(
                    f"Approved {approval.approval_id} ({approval.tool_name}). "
                    f"Resume with: agentforge workflows resume "
                    f"{approval.workflow_id}",
                    style="bold green",
                )
            )
            return 0
        if subcommand == "reject":
            approval = runtime.reject_tool(
                args.approval_id, comment=args.comment
            )
            console.print(
                Text(
                    f"Rejected {approval.approval_id} ({approval.tool_name}). "
                    f"Resume with: agentforge workflows resume "
                    f"{approval.workflow_id}",
                    style="bold red",
                )
            )
            return 0
        console.print(Text("usage: agentforge approvals <list|show|approve|reject>"))
        return 2


async def _cmd_clarification(args: argparse.Namespace) -> int:
    settings = _make_settings(args)
    async with AgentForgeRuntime(settings) as runtime:
        subcommand = getattr(args, "clarification_command", None) or "list"
        if subcommand == "list":
            status = (
                ClarificationStatus(args.status)
                if getattr(args, "status", None)
                else None
            )
            requests = runtime.clarification_service.list(
                getattr(args, "workflow", None), status=status
            )
            _print_clarifications(console, requests)
            return 0
        if subcommand == "show":
            request = runtime.get_clarification(args.clarification_id)
            if request is None:
                console.print(
                    Text(
                        f"unknown clarification {args.clarification_id!r}",
                        style="red",
                    )
                )
                return 1
            _print_clarification_detail(console, request)
            return 0
        if subcommand == "answer":
            answer = " ".join(args.answer).strip()
            request = runtime.answer_clarification(
                args.clarification_id, answer
            )
            console.print(
                Text(
                    f"Answered {request.clarification_id}. Resume with: "
                    f"agentforge workflows resume {request.workflow_id}",
                    style="bold green",
                )
            )
            return 0
        console.print(
            Text("usage: agentforge clarification <list|show|answer>")
        )
        return 2


async def _cmd_workflows(args: argparse.Namespace) -> int:
    settings = _make_settings(args)
    async with AgentForgeRuntime(settings) as runtime:
        subcommand = getattr(args, "workflows_command", None) or "list"
        if subcommand == "list":
            _print_workflows(runtime.list_workflows(args.limit))
            return 0
        workflow_id = getattr(args, "workflow_id", None)
        if not workflow_id:
            console.print(Text("workflow id is required", style="yellow"))
            return 2
        if subcommand == "show":
            workflow = runtime.get_workflow(workflow_id)
            if workflow is None:
                console.print(Text(f"unknown workflow {workflow_id!r}", style="red"))
                return 1
            _print_workflow_detail(runtime, workflow)
            return 0
        if subcommand == "pause":
            workflow = runtime.pause_workflow(workflow_id)
            console.print(
                Text(
                    f"Workflow {workflow.workflow_id} is {workflow.status.value}. "
                    "An active execution stops at its next control point; "
                    "persisted tasks are preserved for resume.",
                    style="bold yellow",
                )
            )
            return 0
        if subcommand == "resume":
            workflow = await runtime.resume_workflow(workflow_id)
            console.print(
                Text(
                    f"Workflow {workflow.workflow_id} is {workflow.status.value}.",
                    style="bold",
                )
            )
            _print_workflow_detail(runtime, workflow)
            return 0
        if subcommand == "cancel":
            workflow = await runtime.cancel_workflow(
                workflow_id, reason=args.reason
            )
            console.print(
                Text(
                    f"Workflow {workflow.workflow_id} is {workflow.status.value}. "
                    "Pending approvals/clarifications and agent sandboxes were "
                    "cleaned up.",
                    style="bold red",
                )
            )
            return 0
        console.print(
            Text("usage: agentforge workflows <list|show|pause|resume|cancel>")
        )
        return 2


def _open_store(args: argparse.Namespace) -> SQLiteStore | None:
    settings = _make_settings(args)
    path = settings.resolved_sqlite_file
    if not path.exists():
        console.print(
            Panel(
                Text(
                    f"No database found at {path}. Run 'agentforge demo' first.",
                    style="yellow",
                ),
                title="No data",
            )
        )
        return None
    return SQLiteStore(path)


# ------------------------------------------------------------------------ renderers
def _print_agents(output: Console) -> None:
    table = Table(title="Agents and capabilities")
    table.add_column("ID", style="bold cyan")
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Capabilities")
    table.add_column("Description")
    for profile in DEFAULT_PROFILES:
        card = AgentCard.from_profile(profile)
        table.add_row(
            card.agent_id,
            card.name,
            card.role.value,
            ", ".join(card.capabilities),
            card.description,
        )
    output.print(table)


def _print_tool_specs(output: Console, specs: list) -> None:
    if not specs:
        output.print(Text("No tools are enabled.", style="dim"))
        return
    table = Table(title="Tool contracts")
    table.add_column("Tool", style="bold cyan")
    table.add_column("Permissions")
    table.add_column("Timeout", justify="right")
    table.add_column("Approval")
    table.add_column("Untrusted", justify="center")
    table.add_column("Description")
    for spec in specs:
        table.add_row(
            spec.name,
            ", ".join(permission.value for permission in spec.permissions) or "-",
            f"{spec.timeout_seconds:g}s",
            "required" if spec.requires_approval else "no",
            "yes" if spec.untrusted_output else "no",
            spec.description,
        )
    output.print(table)


def _print_tool_calls(output: Console, records: list[ToolCallRecord]) -> None:
    if not records:
        output.print(Text("No tool calls found.", style="dim"))
        return
    table = Table(title=f"Tool calls ({len(records)})", show_lines=True)
    table.add_column("Time", style="dim")
    table.add_column("Agent")
    table.add_column("Tool", style="bold")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Output / error")
    for record in records:
        if record.output.get("stdout"):
            preview = str(record.output["stdout"])
        elif record.output.get("content"):
            preview = str(record.output["content"])
        elif record.error:
            preview = record.error
        else:
            preview = json.dumps(record.output, ensure_ascii=False)
        preview = preview.replace("\n", " ")
        if len(preview) > 260:
            preview = preview[:260] + "..."
        table.add_row(
            record.started_at.strftime("%H:%M:%S"),
            record.agent_id,
            record.tool_name,
            Text(
                record.status.value,
                style=STATUS_STYLES.get(
                    record.status.value,
                    "yellow" if record.status.value == "pending_approval" else "white",
                ),
            ),
            f"{record.duration_seconds:.3f}s",
            preview,
        )
    output.print(table)


def _print_approvals(output: Console, approvals) -> None:
    if not approvals:
        output.print(Text("No approval requests found.", style="dim"))
        return
    table = Table(title="Approval requests")
    table.add_column("Approval", style="bold cyan")
    table.add_column("Workflow", style="dim")
    table.add_column("Agent")
    table.add_column("Tool")
    table.add_column("Risk")
    table.add_column("Status")
    table.add_column("Expires")
    for item in approvals:
        table.add_row(
            item.approval_id,
            (item.workflow_id or "-")[:12],
            item.agent_id,
            item.tool_name or item.action,
            item.risk_level.value,
            Text(
                item.status.value,
                style=STATUS_STYLES.get(item.status.value, "white"),
            ),
            item.expires_at.strftime("%Y-%m-%d %H:%M:%S")
            if item.expires_at
            else "-",
        )
    output.print(table)


def _print_approval_detail(output: Console, approval) -> None:
    table = Table(show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Approval", approval.approval_id)
    table.add_row("Status", approval.status.value)
    table.add_row("Decision", approval.decision or "-")
    table.add_row("Workflow", approval.workflow_id or "-")
    table.add_row("Task", approval.task_id or "-")
    table.add_row("Agent", approval.agent_id)
    table.add_row("Tool", approval.tool_name or approval.action)
    table.add_row("Risk", approval.risk_level.value)
    table.add_row("Reason", approval.reason or "-")
    table.add_row("Summary", approval.summary)
    table.add_row(
        "Arguments",
        json.dumps(approval.arguments, ensure_ascii=False, indent=2, default=str),
    )
    table.add_row("Argument digest", approval.argument_digest or "-")
    table.add_row(
        "Created", approval.created_at.strftime("%Y-%m-%d %H:%M:%S")
    )
    table.add_row(
        "Expires",
        approval.expires_at.strftime("%Y-%m-%d %H:%M:%S")
        if approval.expires_at
        else "-",
    )
    table.add_row(
        "Decided",
        approval.decided_at.strftime("%Y-%m-%d %H:%M:%S")
        if approval.decided_at
        else "-",
    )
    table.add_row("Decided by", approval.decided_by or "-")
    table.add_row("Comment", approval.comment or "-")
    table.add_row(
        "Consumed",
        approval.consumed_at.strftime("%Y-%m-%d %H:%M:%S")
        if approval.consumed_at
        else "-",
    )
    output.print(table)


def _print_clarifications(output: Console, requests) -> None:
    if not requests:
        output.print(Text("No clarification requests found.", style="dim"))
        return
    table = Table(title="Clarification requests")
    table.add_column("Clarification", style="bold cyan")
    table.add_column("Workflow", style="dim")
    table.add_column("Agent")
    table.add_column("Status")
    table.add_column("Question")
    for item in requests:
        question = item.question.replace("\n", " ")
        if len(question) > 90:
            question = question[:90] + "..."
        table.add_row(
            item.clarification_id,
            item.workflow_id[:12],
            item.agent_id,
            Text(
                item.status.value,
                style=STATUS_STYLES.get(item.status.value, "white"),
            ),
            question,
        )
    output.print(table)


def _print_clarification_detail(output: Console, request) -> None:
    table = Table(show_header=False)
    table.add_column("Field", style="bold")
    table.add_column("Value")
    table.add_row("Clarification", request.clarification_id)
    table.add_row("Status", request.status.value)
    table.add_row("Workflow", request.workflow_id)
    table.add_row("Task", request.task_id)
    table.add_row("Agent", request.agent_id)
    table.add_row("Question", request.question)
    table.add_row("Context", request.context or "-")
    table.add_row(
        "Interpretations",
        "\n".join(request.possible_interpretations) or "-",
    )
    table.add_row(
        "Created", request.created_at.strftime("%Y-%m-%d %H:%M:%S")
    )
    table.add_row(
        "Expires",
        request.expires_at.strftime("%Y-%m-%d %H:%M:%S")
        if request.expires_at
        else "-",
    )
    table.add_row("Answer", request.answer or "-")
    table.add_row("Answered by", request.answered_by or "-")
    table.add_row(
        "Answered at",
        request.answered_at.strftime("%Y-%m-%d %H:%M:%S")
        if request.answered_at
        else "-",
    )
    output.print(table)


def _print_workflows(workflows) -> None:
    if not workflows:
        console.print(Text("No workflows found.", style="dim"))
        return
    table = Table(title="Workflows")
    table.add_column("Workflow", style="bold cyan")
    table.add_column("Status")
    table.add_column("Goal")
    table.add_column("Created")
    table.add_column("Duration", justify="right")
    for workflow in workflows:
        duration = workflow.duration_seconds()
        table.add_row(
            workflow.workflow_id,
            Text(
                workflow.status.value,
                style=STATUS_STYLES.get(workflow.status.value, "white"),
            ),
            workflow.goal[:90],
            workflow.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            f"{duration:.3f}s" if duration is not None else "-",
        )
    console.print(table)


def _print_workflow_detail(runtime: AgentForgeRuntime, workflow) -> None:
    _print_metrics(console, workflow)
    tasks = runtime.list_tasks(workflow.workflow_id)
    if tasks:
        console.rule("[bold]Tasks[/bold]")
        table = Table()
        table.add_column("Task", style="dim")
        table.add_column("Kind")
        table.add_column("Agent")
        table.add_column("Status")
        table.add_column("Depth", justify="right")
        for task in tasks:
            table.add_row(
                task.task_id[:12],
                str(task.metadata.get("kind", "generic")),
                task.assigned_agent or "-",
                Text(
                    task.status.value,
                    style=STATUS_STYLES.get(task.status.value, "white"),
                ),
                str(task.depth),
            )
        console.print(table)

    approvals = runtime.list_approvals(workflow.workflow_id)
    if approvals:
        console.rule("[bold]Approvals[/bold]")
        _print_approvals(console, approvals)
    clarifications = runtime.list_clarifications(workflow.workflow_id)
    if clarifications:
        console.rule("[bold]Clarifications[/bold]")
        _print_clarifications(console, clarifications)
    events = runtime.list_workflow_events(workflow.workflow_id)
    if events:
        console.rule("[bold]Lifecycle events[/bold]")
        event_table = Table()
        event_table.add_column("Time")
        event_table.add_column("Event")
        event_table.add_column("Metadata")
        for event in events:
            event_table.add_row(
                event.created_at.strftime("%H:%M:%S"),
                event.event_type,
                json.dumps(event.metadata, ensure_ascii=False, default=str)[:160],
            )
        console.print(event_table)
    if workflow.final_result:
        console.rule("[bold]Final report[/bold]")
        console.print(Markdown(workflow.final_result))
    elif workflow.error:
        console.print(Text(workflow.error, style="red"))


def _render_workflow(
    output: Console,
    store: SQLiteStore,
    workflow: Workflow,
) -> None:
    output.print()
    output.rule("[bold]Execution plan[/bold]")
    plan_messages = [
        message
        for message in store.list_messages(workflow.conversation_id)
        if message.message_type.value == "plan"
    ]
    if plan_messages:
        output.print(Markdown(plan_messages[-1].content))
    else:
        output.print(Text("(no plan message was recorded)", style="dim"))

    tasks = store.list_tasks(workflow.workflow_id)
    kind_by_id = {
        task.task_id: str(task.metadata.get("kind", "generic")) for task in tasks
    }
    output.rule("[bold]Task execution[/bold]")
    table = Table(show_lines=False)
    table.add_column("Kind")
    table.add_column("Agent")
    table.add_column("Capabilities")
    table.add_column("Status")
    table.add_column("Depends on")
    table.add_column("Started", justify="right")
    table.add_column("Duration", justify="right")
    for task in tasks:
        if task.metadata.get("role") == "root":
            continue
        deps = ", ".join(kind_by_id.get(dep, dep[:6]) for dep in task.dependencies)
        duration = task.duration_seconds()
        table.add_row(
            str(task.metadata.get("kind", "generic")),
            task.assigned_agent or "-",
            ", ".join(task.required_capabilities) or "-",
            Text(task.status.value, style=STATUS_STYLES.get(task.status.value, "white")),
            deps or "-",
            task.started_at.strftime("%H:%M:%S") if task.started_at else "-",
            f"{duration:.3f}s" if duration is not None else "-",
        )
    output.print(table)

    tool_records = store.list_tool_calls(workflow.workflow_id)
    if tool_records:
        output.rule("[bold]Tool execution[/bold]")
        _print_tool_calls(output, tool_records)

    review_tasks = [
        task for task in tasks if task.metadata.get("kind") == "review" and task.result
    ]
    if review_tasks:
        review = review_tasks[-1].result or ""
        preview = review if len(review) <= 1200 else review[:1200] + "\n..."
        output.rule("[bold]Independent review[/bold]")
        output.print(Markdown(preview))

    output.rule("[bold]Final report[/bold]")
    if workflow.final_result:
        output.print(Markdown(workflow.final_result))
    else:
        output.print(Text(f"Workflow failed: {workflow.error}", style="bold red"))

    output.rule("[bold]Metrics[/bold]")
    _print_metrics(output, workflow)


def _print_metrics(output: Console, workflow: Workflow) -> None:
    metrics = workflow.metrics
    table = Table(show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value")
    table.add_row("Workflow status", workflow.status.value)
    table.add_row(
        "Task completion rate", f"{metrics.task_completion_rate:.1%} "
        f"({metrics.completed_tasks}/{metrics.total_tasks})"
    )
    table.add_row("Failed / blocked tasks", f"{metrics.failed_tasks} / {metrics.blocked_tasks}")
    table.add_row("LLM calls", str(metrics.llm_calls))
    table.add_row(
        "Tool calls / failures",
        f"{metrics.tool_calls} / {metrics.tool_failures}",
    )
    table.add_row("Summed tool time", f"{metrics.tool_seconds:.3f}s")
    table.add_row(
        "Tokens (prompt / completion)",
        f"{metrics.prompt_tokens} / {metrics.completion_tokens}",
    )
    table.add_row(
        "Workflow duration",
        f"{workflow.duration_seconds() or 0.0:.3f}s",
    )
    table.add_row("Summed task duration", f"{metrics.total_task_seconds:.3f}s")
    table.add_row(
        "Delegations / max depth",
        f"{metrics.delegation_count} / {metrics.max_depth}",
    )
    table.add_row(
        "Delegated tasks / completed",
        f"{metrics.delegation_tasks} / {metrics.delegation_completed}",
    )
    table.add_row("Reassignments", str(metrics.reassignments))
    table.add_row(
        "Approvals (requests / approved / rejected / expired)",
        f"{metrics.approval_requests} / {metrics.approval_approved} / "
        f"{metrics.approval_rejected} / {metrics.approval_expired}",
    )
    table.add_row(
        "Approval rate / avg wait",
        f"{metrics.approval_rate:.1%} / "
        f"{metrics.average_approval_wait_seconds:.3f}s",
    )
    table.add_row(
        "Clarifications (requests / answered)",
        f"{metrics.clarifications} / {metrics.clarification_answered}",
    )
    table.add_row(
        "Clarification avg wait",
        f"{metrics.average_clarification_wait_seconds:.3f}s",
    )
    table.add_row(
        "Pauses / resumes / cancellations",
        f"{metrics.workflow_pauses} / {metrics.workflow_resumes} / "
        f"{metrics.workflow_cancellations}",
    )
    output.print(table)


def _print_final(workflow: Workflow) -> None:
    console.print(
        Text(
            f"Workflow {workflow.workflow_id} | {workflow.status.value}",
            style="bold",
        )
    )
    if workflow.final_result:
        console.print(Markdown(workflow.final_result))
    else:
        console.print(Text(f"Workflow failed: {workflow.error}", style="red"))


def _print_persistence(output: Console, runtime: AgentForgeRuntime) -> None:
    counts = runtime.table_counts()
    output.print()
    output.rule("[bold]Persistence and observability[/bold]")
    table = Table(show_header=False)
    table.add_column("Item", style="bold")
    table.add_column("Value")
    table.add_row("SQLite", str(runtime.store.path))
    table.add_row("Event log (JSONL)", str(runtime.settings.resolved_events_file))
    table.add_row("Conversations", str(counts["conversations"]))
    table.add_row("Messages", str(counts["messages"]))
    table.add_row("Tasks", str(counts["tasks"]))
    table.add_row("Agent executions", str(counts["agent_executions"]))
    table.add_row("Workflows", str(counts["workflows"]))
    table.add_row("Memory entries", str(counts["memory_entries"]))
    output.print(table)


def _print_conversations(store: SQLiteStore, limit: int = 50) -> None:
    conversations = store.list_conversations(limit)
    if not conversations:
        console.print(Text("No conversations found.", style="dim"))
        return
    table = Table(title="Conversations")
    table.add_column("Conversation", style="bold cyan")
    table.add_column("Type")
    table.add_column("Title")
    table.add_column("Participants")
    table.add_column("Messages", justify="right")
    table.add_column("Created")
    for conversation in conversations:
        table.add_row(
            conversation.conversation_id,
            conversation.conversation_type.value,
            conversation.title[:70],
            ", ".join(conversation.participants),
            str(store.count_messages(conversation.conversation_id)),
            conversation.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
    console.print(table)
    console.print(
        Text(
            "Use 'agentforge messages --conversation <id>' to replay a transcript.",
            style="dim",
        )
    )


def _print_transcript(
    store: SQLiteStore, conversation_id: str, limit: int = 100
) -> None:
    conversation = store.get_conversation(conversation_id)
    if conversation is None:
        console.print(Text(f"unknown conversation {conversation_id!r}", style="red"))
        return
    messages = store.list_messages(conversation_id, limit=limit)
    console.print(
        Text(
            f"{conversation.title} ({conversation.conversation_type.value}) | "
            f"{len(messages)} message(s)",
            style="bold",
        )
    )
    table = Table(show_lines=True)
    table.add_column("Time", style="dim")
    table.add_column("Type")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Content")
    for message in messages:
        content = message.content.replace("\n", " ")
        if len(content) > 180:
            content = content[:180] + "..."
        table.add_row(
            message.timestamp.strftime("%H:%M:%S"),
            message.message_type.value,
            message.sender,
            message.receiver or "-",
            content,
        )
    console.print(table)


def _print_all_messages(store: SQLiteStore, limit: int = 100) -> None:
    messages = store.list_messages(limit=limit)
    if not messages:
        console.print(Text("No messages found.", style="dim"))
        return
    table = Table(title=f"Latest {len(messages)} messages")
    table.add_column("Time", style="dim")
    table.add_column("Conversation", style="dim")
    table.add_column("Type")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Content")
    for message in messages:
        content = message.content.replace("\n", " ")
        if len(content) > 120:
            content = content[:120] + "..."
        table.add_row(
            message.timestamp.strftime("%H:%M:%S"),
            message.conversation_id[:14],
            message.message_type.value,
            message.sender,
            message.receiver or "-",
            content,
        )
    console.print(table)


def _print_history(store: SQLiteStore, limit: int = 20) -> None:
    workflows = store.list_workflows(limit)
    if not workflows:
        console.print(Text("No workflows found.", style="dim"))
        return
    table = Table(title="Workflow history")
    table.add_column("Workflow", style="bold cyan")
    table.add_column("Status")
    table.add_column("Goal")
    table.add_column("Tasks", justify="right")
    table.add_column("Rate", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Created")
    for workflow in workflows:
        metrics = workflow.metrics
        table.add_row(
            workflow.workflow_id,
            Text(
                workflow.status.value,
                style=STATUS_STYLES.get(workflow.status.value, "white"),
            ),
            workflow.goal[:80],
            f"{metrics.completed_tasks}/{metrics.total_tasks}",
            f"{metrics.task_completion_rate:.0%}",
            f"{workflow.duration_seconds() or 0.0:.2f}s",
            workflow.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
    console.print(table)


if __name__ == "__main__":
    sys.exit(main())
