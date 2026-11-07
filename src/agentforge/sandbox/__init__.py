"""Sandbox package.

Phase 1 provides interfaces only; Docker execution and private per-agent
filesystems are explicitly **not implemented** and are marked TODO for Phase 3.
"""

from agentforge.sandbox.base import (
    BaseSandbox,
    DockerSandbox,
    SandboxConfig,
    SandboxResult,
    SandboxStatus,
)

__all__ = [
    "BaseSandbox",
    "DockerSandbox",
    "SandboxConfig",
    "SandboxResult",
    "SandboxStatus",
]
