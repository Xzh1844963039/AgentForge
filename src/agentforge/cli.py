"""AgentForge command-line interface.

Commands:
  agentforge demo                  Deterministic offline demo (mock provider)
  agentforge run "<goal>"          Execute a goal with the configured provider
  agentforge chat <agent_id>       Private chat with one agent
  agentforge repl                  Interactive shell
  agentforge agents                List agents and capabilities
  agentforge tasks [--workflow ID] Task status
  agentforge messages              Conversations / transcript replay
  agentforge history               Workflow history
  agentforge result <workflow_id>  Print a final report
  agentforge metrics [workflow_id] Print workflow metrics
"""

from __future__ import annotations

import argparse
import asyncio
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
from agentforge.models.agent import AgentCard
from agentforge.models.workflow import Workflow, WorkflowStatus
from agentforge.persistence.database import SQLiteStore
from agentforge.runtime import AgentForgeRuntime

console = Console()
logger = logging.getLogger("agentforge.cli")

DEMO_GOAL = (
    "Research the benefits of multi-agent systems, propose a Python "
    "implementation plan, and review the plan for potential issues."
)

STATUS_STYLES = {
    "completed": "bold green",
    "failed": "bold red",
    "running": "bold yellow",
    "pending": "cyan",
    "blocked": "magenta",
}


# --------------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentforge",
        description="AgentForge: multi-agent collaborative platform (Phase 1)",
    )
    parser.add_argument("--version", action="version", version=__version__)
    _add_common_options(parser)

    subparsers = parser.add_subparsers(dest="command")

    demo = subparsers.add_parser("demo", help="run the deterministic offline demo")
    _add_common_options(demo, suppress_defaults=True)

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
    if command == "run":
        return await _cmd_run(args)
    if command == "chat":
        return await _cmd_chat(args)
    if command == "repl":
        return await _cmd_repl(args)
    if command == "agents":
        return _cmd_agents(args)
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
    console.print(Text(f"unknown command {command!r}", style="red"))
    return 2


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
                    "/dm <agent_id> <message>      private message to one agent\n"
                    "/goal <text>                  publish a goal to a group workflow\n"
                    "/tasks [workflow_id]          task status\n"
                    "/messages [conversation_id]   transcript replay\n"
                    "/conversations                list conversations\n"
                    "/history                      workflow history\n"
                    "/result <workflow_id>         print final report\n"
                    "/metrics [workflow_id]        workflow metrics\n"
                    "/quit                         exit",
                ),
                title="REPL commands",
            )
        )
        return True

    if command == "/agents":
        _print_agents(console)
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

    if command == "/quit":
        return False

    console.print(Text(f"unknown command {command!r}; try /help", style="yellow"))
    return True


# ------------------------------------------------------------------- read-only cmds
def _cmd_agents(args: argparse.Namespace) -> int:
    _print_agents(console)
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
