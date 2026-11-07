"""Tool framework: contracts, dispatcher, permissions, and built-in tools."""

from agentforge.tools.approval import (
    ApprovalGate,
    AutoApprovalGate,
    PendingApprovalGate,
)
from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from agentforge.tools.builtins import BuiltTools, build_default_tools
from agentforge.tools.executor import ToolExecutor
from agentforge.tools.policy import (
    DEFAULT_AGENT_PERMISSIONS,
    PermissionEvaluation,
    ToolPermissionPolicy,
)
from agentforge.tools.workspace import WorkspaceError, WorkspaceManager

__all__ = [
    "ApprovalGate",
    "AutoApprovalGate",
    "BaseTool",
    "BuiltTools",
    "DEFAULT_AGENT_PERMISSIONS",
    "PendingApprovalGate",
    "PermissionEvaluation",
    "ToolExecutionContext",
    "ToolExecutor",
    "ToolPermission",
    "ToolPermissionPolicy",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "WorkspaceError",
    "WorkspaceManager",
    "build_default_tools",
]
