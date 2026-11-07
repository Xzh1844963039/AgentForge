"""AgentForge: a multi-agent collaborative platform.

Phase 1 provides a runnable multi-agent runtime with:
- pluggable LLM providers (DeepSeek / HKGAI-compatible / deterministic mock),
- messaging and conversation persistence,
- capability-based task delegation with a DAG executor,
- independent per-agent memory namespaces,
- SQLite + JSONL observability.

Future phases add memory evolution, Docker sandboxes, human-in-the-loop
approval, web search and GitHub automation. See docs/DEVELOPMENT_PLAN.md.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
