"""Sandbox manager: one Docker sandbox and private workspace per agent."""

from __future__ import annotations

import logging

from agentforge.sandbox.base import (
    BaseSandbox,
    SandboxConfig,
    SandboxStatus,
)
from agentforge.sandbox.docker import DockerSandbox
from agentforge.tools.workspace import WorkspaceManager

logger = logging.getLogger("agentforge.sandbox.manager")


class SandboxManager:
    def __init__(
        self,
        config: SandboxConfig,
        *,
        workspaces: WorkspaceManager | None = None,
    ) -> None:
        self.config = config
        self.workspaces = workspaces or WorkspaceManager(config.workspace_root)
        self._sandboxes: dict[str, BaseSandbox] = {}

    def workspace_for(self, agent_id: str):
        return self.workspaces.workspace_for(agent_id)

    def get(self, agent_id: str) -> BaseSandbox:
        if agent_id not in self._sandboxes:
            self._sandboxes[agent_id] = DockerSandbox(
                agent_id,
                self.config,
                workspaces=self.workspaces,
            )
        return self._sandboxes[agent_id]

    def status(self) -> dict[str, str]:
        return {
            agent_id: sandbox.status.value
            for agent_id, sandbox in self._sandboxes.items()
        }

    async def stop_agent(self, agent_id: str) -> None:
        sandbox = self._sandboxes.get(agent_id)
        if sandbox is not None and sandbox.status in {
            SandboxStatus.RUNNING,
            SandboxStatus.FAILED,
        }:
            try:
                await sandbox.stop()
            except Exception:
                logger.exception("failed to stop sandbox for %s", agent_id)

    async def stop_all(self) -> None:
        for agent_id in list(self._sandboxes):
            await self.stop_agent(agent_id)

    async def aclose(self) -> None:
        await self.stop_all()
