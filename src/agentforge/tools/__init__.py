"""Tool interfaces and registry.

TODO (Phase 3): implement internet search, sandboxed Python, Markdown/TXT file
operations, and GitHub commit/push tools with explicit permission checks.
"""

from agentforge.tools.base import BaseTool, ToolRegistry, ToolResult, ToolSpec

__all__ = ["BaseTool", "ToolRegistry", "ToolResult", "ToolSpec"]
