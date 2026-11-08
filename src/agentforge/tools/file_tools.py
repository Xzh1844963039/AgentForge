"""Private-workspace file tools (Markdown and plain text only)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from agentforge.tools.base import (
    BaseTool,
    ToolExecutionContext,
    ToolPermission,
    ToolResult,
)
from agentforge.tools.workspace import (
    FileInfo,
    WorkspaceError,
    WorkspaceManager,
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- models
class FileEntry(BaseModel):
    path: str
    size_bytes: int
    modified_at: float


class ListFilesArgs(_Args):
    path: str = Field(default="", max_length=240)
    recursive: bool = True
    max_files: int = Field(default=200, ge=1, le=1000)


class ListFilesOutput(BaseModel):
    files: list[FileEntry]
    count: int


class ReadFileArgs(_Args):
    path: str = Field(min_length=1, max_length=240)
    max_bytes: int | None = Field(default=None, ge=1)


class ReadFileOutput(BaseModel):
    path: str
    content: str
    size_bytes: int
    sha256: str
    truncated: bool


class WriteFileArgs(_Args):
    path: str = Field(min_length=1, max_length=240)
    content: str
    overwrite: bool = False


class WriteFileOutput(BaseModel):
    path: str
    size_bytes: int
    sha256: str


class UpdateFileArgs(_Args):
    path: str = Field(min_length=1, max_length=240)
    content: str
    mode: Literal["replace", "append"] = "replace"


class UpdateFileOutput(BaseModel):
    path: str
    size_bytes: int
    sha256: str
    mode: str


class DeleteFileArgs(_Args):
    path: str = Field(min_length=1, max_length=240)


class DeleteFileOutput(BaseModel):
    path: str
    deleted: bool


class SearchFilesArgs(_Args):
    query: str = Field(min_length=1, max_length=500)
    path: str = Field(default="", max_length=240)
    case_sensitive: bool = False
    max_results: int = Field(default=50, ge=1, le=200)


class SearchMatch(BaseModel):
    path: str
    line: int
    text: str


class SearchFilesOutput(BaseModel):
    matches: list[SearchMatch]
    count: int


class _WorkspaceTool(BaseTool):
    def __init__(
        self,
        workspaces: WorkspaceManager,
        *,
        max_file_bytes: int = 262_144,
        max_files: int = 200,
    ) -> None:
        self.workspaces = workspaces
        self.max_file_bytes = max_file_bytes
        self.max_files = max_files
        super().__init__()

    @staticmethod
    def _context_workspace_error(context: ToolExecutionContext) -> ToolResult | None:
        if context.workspace is None:
            return ToolResult.fail("no private workspace is configured")
        return None


class ListFilesTool(_WorkspaceTool):
    name = "list_files"
    description = "List Markdown (.md) and text (.txt) files in the agent's private workspace."
    args_model = ListFilesArgs
    output_model = ListFilesOutput
    permissions = [ToolPermission.FILE_READ]
    timeout_seconds = 10

    async def run(
        self, arguments: ListFilesArgs, context: ToolExecutionContext
    ) -> ToolResult:
        error = self._context_workspace_error(context)
        if error:
            return error
        try:
            files: list[FileInfo] = self.workspaces.list_files(
                context.agent_id,
                arguments.path,
                recursive=arguments.recursive,
                max_files=min(arguments.max_files, self.max_files),
            )
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        payload = ListFilesOutput(
            files=[
                FileEntry(
                    path=item.path,
                    size_bytes=item.size_bytes,
                    modified_at=item.modified_at,
                )
                for item in files
            ],
            count=len(files),
        )
        return ToolResult.ok(payload.model_dump())


class ReadFileTool(_WorkspaceTool):
    name = "read_file"
    description = "Read a Markdown (.md) or plain-text (.txt) file from the agent's private workspace."
    args_model = ReadFileArgs
    output_model = ReadFileOutput
    permissions = [ToolPermission.FILE_READ]
    timeout_seconds = 10

    async def run(
        self, arguments: ReadFileArgs, context: ToolExecutionContext
    ) -> ToolResult:
        error = self._context_workspace_error(context)
        if error:
            return error
        try:
            content, size, digest, truncated = self.workspaces.read_text(
                context.agent_id,
                arguments.path,
                max_bytes=min(
                    arguments.max_bytes or self.max_file_bytes,
                    self.max_file_bytes,
                ),
            )
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(
            ReadFileOutput(
                path=arguments.path,
                content=content,
                size_bytes=size,
                sha256=digest,
                truncated=truncated,
            ).model_dump()
        )


class WriteFileTool(_WorkspaceTool):
    name = "write_file"
    description = "Create a Markdown (.md) or plain-text (.txt) file in the agent's private workspace."
    args_model = WriteFileArgs
    output_model = WriteFileOutput
    permissions = [ToolPermission.FILE_WRITE]
    timeout_seconds = 10

    async def run(
        self, arguments: WriteFileArgs, context: ToolExecutionContext
    ) -> ToolResult:
        error = self._context_workspace_error(context)
        if error:
            return error
        try:
            path, size, digest = self.workspaces.atomic_write(
                context.agent_id,
                arguments.path,
                arguments.content,
                max_bytes=self.max_file_bytes,
                overwrite=arguments.overwrite,
            )
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(
            WriteFileOutput(
                path=self.workspaces.relative(context.agent_id, path),
                size_bytes=size,
                sha256=digest,
            ).model_dump()
        )


class UpdateFileTool(_WorkspaceTool):
    name = "update_file"
    description = (
        "Replace or append content in an existing Markdown (.md) or text (.txt) "
        "file. Any in-place modification requires human approval."
    )
    args_model = UpdateFileArgs
    output_model = UpdateFileOutput
    permissions = [ToolPermission.FILE_WRITE]
    requires_approval = True
    timeout_seconds = 10

    async def run(
        self, arguments: UpdateFileArgs, context: ToolExecutionContext
    ) -> ToolResult:
        error = self._context_workspace_error(context)
        if error:
            return error
        try:
            self.workspaces.secure_path(
                context.agent_id,
                arguments.path,
                must_exist=True,
            )
            if arguments.mode == "append":
                path, size, digest = self.workspaces.append_text(
                    context.agent_id,
                    arguments.path,
                    arguments.content,
                    max_bytes=self.max_file_bytes,
                )
            else:
                path, size, digest = self.workspaces.atomic_write(
                    context.agent_id,
                    arguments.path,
                    arguments.content,
                    max_bytes=self.max_file_bytes,
                    overwrite=True,
                )
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(
            UpdateFileOutput(
                path=self.workspaces.relative(context.agent_id, path),
                size_bytes=size,
                sha256=digest,
                mode=arguments.mode,
            ).model_dump()
        )


class DeleteFileTool(_WorkspaceTool):
    name = "delete_file"
    description = "Delete one Markdown (.md) or text (.txt) file. Requires human approval."
    args_model = DeleteFileArgs
    output_model = DeleteFileOutput
    permissions = [ToolPermission.FILE_DELETE]
    requires_approval = True
    timeout_seconds = 10

    async def run(
        self, arguments: DeleteFileArgs, context: ToolExecutionContext
    ) -> ToolResult:
        error = self._context_workspace_error(context)
        if error:
            return error
        try:
            path = self.workspaces.delete_file(context.agent_id, arguments.path)
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.ok(
            DeleteFileOutput(
                path=self.workspaces.relative(context.agent_id, path),
                deleted=True,
            ).model_dump()
        )


class SearchFilesTool(_WorkspaceTool):
    name = "search_files"
    description = "Search Markdown and text files in the agent's private workspace."
    args_model = SearchFilesArgs
    output_model = SearchFilesOutput
    permissions = [ToolPermission.FILE_READ]
    timeout_seconds = 15

    async def run(
        self, arguments: SearchFilesArgs, context: ToolExecutionContext
    ) -> ToolResult:
        error = self._context_workspace_error(context)
        if error:
            return error
        try:
            matches = self.workspaces.search_files(
                context.agent_id,
                arguments.query,
                relative_path=arguments.path,
                case_sensitive=arguments.case_sensitive,
                max_results=min(arguments.max_results, self.max_files),
                max_file_bytes=self.max_file_bytes,
            )
        except WorkspaceError as exc:
            return ToolResult.fail(str(exc))
        payload = SearchFilesOutput(
            matches=[SearchMatch(**match) for match in matches],
            count=len(matches),
        )
        return ToolResult.ok(payload.model_dump())


def file_tools(
    workspaces: WorkspaceManager,
    *,
    max_file_bytes: int = 262_144,
    max_files: int = 200,
) -> list[BaseTool]:
    return [
        ListFilesTool(
            workspaces, max_file_bytes=max_file_bytes, max_files=max_files
        ),
        ReadFileTool(
            workspaces, max_file_bytes=max_file_bytes, max_files=max_files
        ),
        WriteFileTool(
            workspaces, max_file_bytes=max_file_bytes, max_files=max_files
        ),
        UpdateFileTool(
            workspaces, max_file_bytes=max_file_bytes, max_files=max_files
        ),
        DeleteFileTool(
            workspaces, max_file_bytes=max_file_bytes, max_files=max_files
        ),
        SearchFilesTool(
            workspaces, max_file_bytes=max_file_bytes, max_files=max_files
        ),
    ]
