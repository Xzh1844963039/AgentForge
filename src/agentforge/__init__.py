"""AgentForge: a multi-agent collaborative platform.

Phase 5 provides:
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
- SQLite + JSONL observability for messages, tasks, LLM calls, tools and lifecycle,
- a resumable benchmark harness with objective verifiers, metrics, failure
  classification, A/B/C ablation and explicit live-provider acceptance,
- reconciliation state for high-risk side effects with unknown audit outcomes.

Future phases add external integrations and real-provider hardening.
See docs/DEVELOPMENT_PLAN.md.
"""

__version__ = "0.5.0"

__all__ = ["__version__"]
