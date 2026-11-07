"""Per-agent tool permission policy.

Default-deny: an agent may invoke a tool only when it holds every permission the
tool declares. Destructive operations additionally require a human approval
decision from the configured approval gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agentforge.models.tool import PermissionDecision
from agentforge.tools.base import ToolPermission, ToolSpec

APPROVAL_REQUIRED_PERMISSIONS: frozenset[ToolPermission] = frozenset(
    {ToolPermission.FILE_DELETE, ToolPermission.GIT_PUSH}
)

DEFAULT_AGENT_PERMISSIONS: dict[str, frozenset[ToolPermission]] = {
    "coordinator": frozenset(
        {
            ToolPermission.FILE_READ,
            ToolPermission.FILE_WRITE,
            ToolPermission.NETWORK_READ,
            ToolPermission.ARTIFACT_READ,
            ToolPermission.ARTIFACT_WRITE,
        }
    ),
    "research": frozenset(
        {
            ToolPermission.FILE_READ,
            ToolPermission.FILE_WRITE,
            ToolPermission.NETWORK_READ,
            ToolPermission.ARTIFACT_READ,
            ToolPermission.ARTIFACT_WRITE,
        }
    ),
    "coding": frozenset(
        {
            ToolPermission.FILE_READ,
            ToolPermission.FILE_WRITE,
            ToolPermission.FILE_DELETE,
            ToolPermission.PYTHON_EXECUTE,
            ToolPermission.NETWORK_READ,
            ToolPermission.GIT_READ,
            ToolPermission.GIT_WRITE,
            ToolPermission.GIT_PUSH,
            ToolPermission.ARTIFACT_READ,
            ToolPermission.ARTIFACT_WRITE,
        }
    ),
    "reviewer": frozenset(
        {
            ToolPermission.FILE_READ,
            ToolPermission.PYTHON_EXECUTE,
            ToolPermission.NETWORK_READ,
            ToolPermission.GIT_READ,
            ToolPermission.ARTIFACT_READ,
        }
    ),
}


@dataclass(frozen=True)
class PermissionEvaluation:
    decision: PermissionDecision
    requires_approval: bool
    reason: str = ""
    missing: tuple[str, ...] = ()
    permissions: tuple[str, ...] = field(default_factory=tuple)


class ToolPermissionPolicy:
    """Agent → permission grants with explicit approval requirements."""

    def __init__(
        self,
        grants: dict[str, set[ToolPermission] | frozenset[ToolPermission]] | None = None,
        *,
        approval_permissions: set[ToolPermission] | frozenset[ToolPermission] | None = None,
    ) -> None:
        source = grants or {
            agent: set(permissions)
            for agent, permissions in DEFAULT_AGENT_PERMISSIONS.items()
        }
        self._grants = {agent: set(permissions) for agent, permissions in source.items()}
        self._approval_permissions = set(
            APPROVAL_REQUIRED_PERMISSIONS
            if approval_permissions is None
            else approval_permissions
        )

    def grant(self, agent_id: str, *permissions: ToolPermission) -> None:
        self._grants.setdefault(agent_id, set()).update(permissions)

    def evaluate(self, agent_id: str, spec: ToolSpec) -> PermissionEvaluation:
        granted = self._grants.get(agent_id)
        if granted is None:
            return PermissionEvaluation(
                decision=PermissionDecision.DENIED,
                requires_approval=False,
                reason=f"agent {agent_id!r} has no tool grants",
                permissions=tuple(permission.value for permission in spec.permissions),
            )

        missing = [
            permission for permission in spec.permissions if permission not in granted
        ]
        requires_approval = spec.requires_approval or any(
            permission in self._approval_permissions for permission in spec.permissions
        )
        if missing:
            return PermissionEvaluation(
                decision=PermissionDecision.DENIED,
                requires_approval=requires_approval,
                reason=(
                    f"agent {agent_id!r} lacks required permissions: "
                    + ", ".join(permission.value for permission in missing)
                ),
                missing=tuple(permission.value for permission in missing),
                permissions=tuple(permission.value for permission in spec.permissions),
            )

        return PermissionEvaluation(
            decision=PermissionDecision.ALLOWED,
            requires_approval=requires_approval,
            reason="allowed",
            permissions=tuple(permission.value for permission in spec.permissions),
        )
