"""Controlled cross-agent artifact transfer."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from agentforge.models.tool import ArtifactRecord
from agentforge.persistence.database import SQLiteStore
from agentforge.tools.workspace import TEXT_EXTENSIONS, WorkspaceManager, WorkspaceError


class ArtifactStore:
    """Central artifact repository with explicit publish/fetch operations.

    Agents cannot read another agent's workspace directly. A file can cross the
    boundary only after its owner publishes it as an artifact; the consumer then
    fetches a copy into its own workspace.
    """

    def __init__(
        self,
        root: str | Path,
        workspaces: WorkspaceManager,
        store: SQLiteStore,
        *,
        max_bytes: int = 262_144,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspaces = workspaces
        self.store = store
        self.max_bytes = max_bytes

    def share(
        self,
        agent_id: str,
        relative_path: str,
        *,
        workflow_id: str | None = None,
        task_id: str | None = None,
    ) -> ArtifactRecord:
        source = self.workspaces.secure_path(
            agent_id,
            relative_path,
            allowed_extensions=TEXT_EXTENSIONS,
            must_exist=True,
        )
        size = source.stat().st_size
        if size > self.max_bytes:
            raise WorkspaceError(
                f"artifact exceeds the {self.max_bytes}-byte transfer limit"
            )
        data = source.read_bytes()
        artifact = ArtifactRecord(
            owner_agent_id=agent_id,
            filename=source.name,
            stored_path="",
            sha256=hashlib.sha256(data).hexdigest(),
            size_bytes=len(data),
            source_path=relative_path,
            workflow_id=workflow_id,
            task_id=task_id,
        )
        target_dir = (self.root / artifact.artifact_id).resolve()
        if not target_dir.is_relative_to(self.root):
            raise WorkspaceError("artifact path escaped the artifact root")
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / artifact.filename
        fd, temp_name = tempfile.mkstemp(prefix=".artifact-", dir=str(target_dir))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        artifact.stored_path = target.relative_to(self.root).as_posix()
        self.store.save_artifact(artifact)
        return artifact

    def fetch(
        self,
        agent_id: str,
        artifact_id: str,
        destination_path: str,
    ) -> tuple[ArtifactRecord, Path, int, str]:
        artifact = self.store.get_artifact(artifact_id)
        if artifact is None:
            raise WorkspaceError(f"unknown artifact: {artifact_id}")
        source = (self.root / artifact.stored_path).resolve()
        if not source.is_relative_to(self.root) or not source.is_file():
            raise WorkspaceError("artifact payload is missing or unsafe")
        if source.is_symlink():
            raise WorkspaceError("artifact symlinks are not allowed")
        data = source.read_bytes()
        if len(data) > self.max_bytes:
            raise WorkspaceError("artifact exceeds the transfer size limit")
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise WorkspaceError("artifact integrity check failed")

        extension = Path(destination_path).suffix.lower()
        if extension not in TEXT_EXTENSIONS:
            raise WorkspaceError("artifact destination must be .md or .txt")
        target, size, digest = self.workspaces.atomic_write(
            agent_id,
            destination_path,
            data.decode("utf-8", errors="replace"),
            max_bytes=self.max_bytes,
            overwrite=False,
        )
        if digest != artifact.sha256:
            target.unlink(missing_ok=True)
            raise WorkspaceError("artifact copy integrity check failed")
        return artifact, target, size, digest
