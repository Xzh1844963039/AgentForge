"""Controlled artifact share/fetch tools."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from agentforge.tools.artifact_store import ArtifactStore
from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolResult,
)
from agentforge.tools.workspace import WorkspaceError


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ShareArtifactArgs(_Args):
    path: str = Field(min_length=1, max_length=240)


class ShareArtifactOutput(BaseModel):
    artifact_id: str
    filename: str
    owner_agent_id: str
    sha256: str
    size_bytes: int


class FetchArtifactArgs(_Args):
    artifact_id: str = Field(min_length=1, max_length=120)
    dest_path: str = Field(min_length=1, max_length=240)


class FetchArtifactOutput(BaseModel):
    artifact_id: str
    path: str
    owner_agent_id: str
    filename: str
    sha256: str
    size_bytes: int


class ShareArtifactTool(BaseTool):
    name = "share_artifact"
    description = (
        "Publish one Markdown or text file from the agent's private workspace as "
        "an immutable artifact for controlled cross-agent transfer."
    )
    args_model = ShareArtifactArgs
    output_model = ShareArtifactOutput
    permissions = [ToolPermission.ARTIFACT_WRITE]
    timeout_seconds = 15

    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts
        super().__init__()

    async def run(
        self, arguments: ShareArtifactArgs, context: ToolExecutionContext
    ) -> ToolResult:
        try:
            artifact = self.artifacts.share(
                context.agent_id,
                arguments.path,
                workflow_id=context.workflow_id,
                task_id=context.task_id,
            )
        except (WorkspaceError, OSError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(
            ShareArtifactOutput(
                artifact_id=artifact.artifact_id,
                filename=artifact.filename,
                owner_agent_id=artifact.owner_agent_id,
                sha256=artifact.sha256,
                size_bytes=artifact.size_bytes,
            ).model_dump()
        )


class FetchArtifactTool(BaseTool):
    name = "fetch_artifact"
    description = (
        "Copy a published artifact into the agent's own private workspace. "
        "Direct access to another agent's workspace is never allowed."
    )
    args_model = FetchArtifactArgs
    output_model = FetchArtifactOutput
    permissions = [ToolPermission.ARTIFACT_READ]
    timeout_seconds = 15

    def __init__(self, artifacts: ArtifactStore) -> None:
        self.artifacts = artifacts
        super().__init__()

    async def run(
        self, arguments: FetchArtifactArgs, context: ToolExecutionContext
    ) -> ToolResult:
        try:
            artifact, path, _size, digest = self.artifacts.fetch(
                context.agent_id,
                arguments.artifact_id,
                arguments.dest_path,
            )
        except (WorkspaceError, OSError) as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(
            FetchArtifactOutput(
                artifact_id=artifact.artifact_id,
                path=path.relative_to(
                    self.artifacts.workspaces.workspace_for(context.agent_id)
                ).as_posix(),
                owner_agent_id=artifact.owner_agent_id,
                filename=artifact.filename,
                sha256=digest,
                size_bytes=artifact.size_bytes,
            ).model_dump()
        )


def artifact_tools(artifacts: ArtifactStore) -> list[BaseTool]:
    return [ShareArtifactTool(artifacts), FetchArtifactTool(artifacts)]
