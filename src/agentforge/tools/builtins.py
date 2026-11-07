"""Default tool registry construction."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from agentforge.config.settings import Settings
from agentforge.tools.artifact_store import ArtifactStore
from agentforge.tools.artifact_tools import artifact_tools
from agentforge.tools.base import ToolRegistry
from agentforge.tools.file_tools import file_tools
from agentforge.tools.git_tools import GitError, git_tools
from agentforge.tools.python_tools import python_tools
from agentforge.tools.search import SearchProvider, build_search_provider
from agentforge.tools.search_tools import search_tools
from agentforge.tools.workspace import WorkspaceManager

logger = logging.getLogger("agentforge.tools.builtins")


@dataclass
class BuiltTools:
    registry: ToolRegistry
    search_provider: SearchProvider
    workspaces: WorkspaceManager
    artifacts: ArtifactStore


def build_default_tools(
    settings: Settings,
    *,
    workspaces: WorkspaceManager,
    artifacts: ArtifactStore,
) -> BuiltTools:
    registry = ToolRegistry()
    if not settings.tools_enabled:
        search_provider = build_search_provider(
            provider="mock", endpoint=None, api_key=None
        )
        return BuiltTools(
            registry=registry,
            search_provider=search_provider,
            workspaces=workspaces,
            artifacts=artifacts,
        )

    for tool in file_tools(
        workspaces,
        max_file_bytes=settings.max_file_bytes,
        max_files=settings.max_workspace_files,
    ):
        registry.register(tool)
    for tool in artifact_tools(artifacts):
        registry.register(tool)
    for tool in python_tools():
        registry.register(tool)

    search_provider = build_search_provider(
        provider=settings.search_provider,
        endpoint=settings.search_endpoint,
        api_key=(
            settings.search_api_key.get_secret_value()
            if settings.search_api_key
            else None
        ),
        timeout=settings.search_timeout,
        max_retries=settings.max_retries,
        max_redirects=settings.web_fetch_max_redirects,
    )
    for tool in search_tools(
        search_provider,
        fetch_max_bytes=settings.web_fetch_max_bytes,
        default_max_results=settings.search_max_results,
    ):
        registry.register(tool)

    if settings.git_repo_root is not None:
        try:
            for tool in git_tools(
                settings.git_repo_root, timeout=settings.git_timeout
            ):
                registry.register(tool)
        except GitError as exc:
            logger.warning("git tools are disabled: %s", exc)

    return BuiltTools(
        registry=registry,
        search_provider=search_provider,
        workspaces=workspaces,
        artifacts=artifacts,
    )
