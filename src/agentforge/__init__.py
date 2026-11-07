"""AgentForge: a multi-agent collaborative platform.

Phase 2 provides:
- a multi-agent runtime with DeepSeek/HKGAI/mock providers,
- dynamic task decomposition and capability-based delegation,
- a unified, schema-validated tool dispatcher with permissions and approval gates,
- private per-agent workspaces and controlled artifact transfer,
- a Docker sandbox with CPU/memory/PID/time/network limits for Python execution,
- mock and HTTP search providers with SSRF protections,
- local Git tools with an approval gate for push,
- SQLite + JSONL observability for messages, tasks, LLM calls and tool calls.

Future phases add memory evolution and full human-in-the-loop workflows.
See docs/DEVELOPMENT_PLAN.md.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
