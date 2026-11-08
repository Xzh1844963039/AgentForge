"""AgentForge: a multi-agent collaborative platform.

Phase 4 provides:
- a multi-agent runtime with DeepSeek/HKGAI/mock providers,
- dynamic task decomposition, bounded delegation, rejection and reassignment,
- a unified, schema-validated tool dispatcher with role permissions and approval gates,
- private per-agent workspaces and controlled artifact transfer,
- a Docker sandbox with CPU/memory/PID/time/network limits for Python execution,
- mock and HTTP search providers with SSRF protections,
- local Git tools with an approval gate for push,
- evidence-first memory evolution with task-scoped retrieval feedback,
- persistent approvals and clarifications,
- workflow pause/resume/cancel with deterministic replay and crash recovery,
- SQLite + JSONL observability for messages, tasks, LLM calls, tools and lifecycle.

Future phases add external integrations and real-provider hardening.
See docs/DEVELOPMENT_PLAN.md.
"""

__version__ = "0.4.0"

__all__ = ["__version__"]
