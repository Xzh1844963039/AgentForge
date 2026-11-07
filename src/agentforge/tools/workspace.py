"""Private per-agent workspaces with path-traversal and symlink protections."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path


TEXT_EXTENSIONS = frozenset({".md", ".txt"})
CODE_EXTENSIONS = frozenset({".py"})
_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_BAD_CHARS_RE = re.compile(r'[<>:"|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class WorkspaceError(ValueError):
    """Raised when a path violates workspace isolation rules."""


@dataclass(frozen=True)
class FileInfo:
    path: str
    size_bytes: int
    modified_at: float


class WorkspaceManager:
    """Resolves safe paths inside an agent's private directory."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def workspace_for(self, agent_id: str) -> Path:
        if not _AGENT_ID_RE.match(agent_id):
            raise WorkspaceError(f"invalid agent id {agent_id!r}")
        path = (self.root / agent_id).resolve()
        if not path.is_relative_to(self.root):
            raise WorkspaceError("workspace escaped the workspace root")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def secure_path(
        self,
        agent_id: str,
        relative_path: str,
        *,
        allowed_extensions: frozenset[str] = TEXT_EXTENSIONS,
        must_exist: bool = False,
        must_be_file: bool = True,
        create_parents: bool = False,
    ) -> Path:
        workspace = self.workspace_for(agent_id)
        if relative_path is None:
            raise WorkspaceError("path must not be null")
        if not isinstance(relative_path, str):
            raise WorkspaceError("path must be a string")
        if "\x00" in relative_path:
            raise WorkspaceError("path must not contain NUL bytes")

        raw = relative_path.strip().replace("\\", "/")
        if raw.startswith("/") or raw.startswith("//") or _DRIVE_RE.match(raw):
            raise WorkspaceError("absolute paths are not allowed")
        if len(raw) > 240:
            raise WorkspaceError("path is too long")

        parts = [part for part in raw.split("/") if part not in ("", ".")]
        if not parts:
            if must_be_file:
                raise WorkspaceError("a file path is required")
            return workspace
        if any(part == ".." for part in parts):
            raise WorkspaceError("path traversal is not allowed")
        if len(parts) > 12:
            raise WorkspaceError("path nesting is too deep")
        if any(_BAD_CHARS_RE.search(part) for part in parts):
            raise WorkspaceError("path contains unsupported characters")
        for part in parts:
            stem = part.split(".", 1)[0].upper()
            if stem in _WINDOWS_RESERVED:
                raise WorkspaceError("Windows reserved device names are not allowed")
            if part.endswith(" ") or part.endswith("."):
                raise WorkspaceError("paths must not end with a space or dot")

        target = workspace.joinpath(*parts)
        if must_be_file and target.suffix.lower() not in allowed_extensions:
            allowed = ", ".join(sorted(allowed_extensions))
            raise WorkspaceError(f"file type not allowed; expected one of: {allowed}")

        self._reject_symlink_components(workspace, parts)
        if create_parents:
            parent = target.parent
            parent.mkdir(parents=True, exist_ok=True)
            self._reject_symlink_components(workspace, parts[:-1])

        resolved = target.resolve(strict=False)
        if not resolved.is_relative_to(workspace.resolve()):
            raise WorkspaceError("resolved path escaped the agent workspace")
        if target.is_symlink():
            raise WorkspaceError("symbolic links are not allowed")
        if must_exist and not target.exists():
            raise WorkspaceError(f"file does not exist: {relative_path}")
        if must_exist and must_be_file and not target.is_file():
            raise WorkspaceError(f"not a regular file: {relative_path}")
        if target.exists() and must_be_file and not target.is_file():
            raise WorkspaceError(f"not a regular file: {relative_path}")
        return target

    @staticmethod
    def _reject_symlink_components(workspace: Path, parts: list[str]) -> None:
        current = workspace
        for part in parts:
            current = current / part
            if current.is_symlink():
                raise WorkspaceError("symbolic links are not allowed")
            if current.exists() and part != parts[-1] and not current.is_dir():
                raise WorkspaceError("a parent path component is not a directory")

    def relative(self, agent_id: str, path: Path) -> str:
        workspace = self.workspace_for(agent_id)
        return path.resolve().relative_to(workspace.resolve()).as_posix()

    @staticmethod
    def sha256_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def read_text(
        self,
        agent_id: str,
        relative_path: str,
        *,
        max_bytes: int,
        allowed_extensions: frozenset[str] = TEXT_EXTENSIONS,
    ) -> tuple[str, int, str, bool]:
        path = self.secure_path(
            agent_id,
            relative_path,
            allowed_extensions=allowed_extensions,
            must_exist=True,
        )
        size = path.stat().st_size
        raw = path.read_bytes()[:max_bytes]
        truncated = size > len(raw)
        text = raw.decode("utf-8", errors="replace")
        return text, size, self.sha256_bytes(raw), truncated

    def atomic_write(
        self,
        agent_id: str,
        relative_path: str,
        content: str,
        *,
        max_bytes: int,
        allowed_extensions: frozenset[str] = TEXT_EXTENSIONS,
        overwrite: bool = True,
    ) -> tuple[Path, int, str]:
        data = content.encode("utf-8")
        if len(data) > max_bytes:
            raise WorkspaceError(
                f"content exceeds the {max_bytes}-byte file limit"
            )
        path = self.secure_path(
            agent_id,
            relative_path,
            allowed_extensions=allowed_extensions,
            must_be_file=True,
            create_parents=True,
        )
        if path.exists() and not overwrite:
            raise WorkspaceError(f"file already exists: {relative_path}")

        fd, temp_name = tempfile.mkstemp(
            prefix=".agentforge-tmp-", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return path, len(data), self.sha256_bytes(data)

    def append_text(
        self,
        agent_id: str,
        relative_path: str,
        content: str,
        *,
        max_bytes: int,
    ) -> tuple[Path, int, str]:
        existing, _, _, _ = self.read_text(
            agent_id, relative_path, max_bytes=max_bytes
        )
        return self.atomic_write(
            agent_id,
            relative_path,
            existing + content,
            max_bytes=max_bytes,
        )

    def delete_file(self, agent_id: str, relative_path: str) -> Path:
        path = self.secure_path(agent_id, relative_path, must_exist=True)
        path.unlink()
        return path

    def list_files(
        self,
        agent_id: str,
        relative_path: str = "",
        *,
        recursive: bool = True,
        max_files: int = 200,
    ) -> list[FileInfo]:
        base = self.secure_path(
            agent_id,
            relative_path or ".",
            must_be_file=False,
        )
        if not base.exists() or not base.is_dir():
            raise WorkspaceError(f"directory does not exist: {relative_path}")
        workspace = self.workspace_for(agent_id)
        results: list[FileInfo] = []

        if recursive:
            for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
                directory = Path(dirpath)
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not (directory / name).is_symlink()
                ]
                for filename in filenames:
                    path = directory / filename
                    if path.is_symlink():
                        continue
                    if path.suffix.lower() not in TEXT_EXTENSIONS:
                        continue
                    stat = path.stat()
                    results.append(
                        FileInfo(
                            path=path.relative_to(workspace).as_posix(),
                            size_bytes=stat.st_size,
                            modified_at=stat.st_mtime,
                        )
                    )
                    if len(results) >= max_files:
                        return sorted(results, key=lambda item: item.path)
        else:
            for path in sorted(base.iterdir()):
                if path.is_symlink() or not path.is_file():
                    continue
                if path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                stat = path.stat()
                results.append(
                    FileInfo(
                        path=path.relative_to(workspace).as_posix(),
                        size_bytes=stat.st_size,
                        modified_at=stat.st_mtime,
                    )
                )
                if len(results) >= max_files:
                    break
        return sorted(results, key=lambda item: item.path)

    def search_files(
        self,
        agent_id: str,
        query: str,
        *,
        relative_path: str = "",
        case_sensitive: bool = False,
        max_results: int = 50,
        max_file_bytes: int = 262_144,
    ) -> list[dict]:
        base = self.secure_path(
            agent_id, relative_path or ".", must_be_file=False
        )
        if not base.exists() or not base.is_dir():
            raise WorkspaceError(f"directory does not exist: {relative_path}")
        workspace = self.workspace_for(agent_id)
        needle = query if case_sensitive else query.lower()
        matches: list[dict] = []

        for dirpath, dirnames, filenames in os.walk(base, followlinks=False):
            directory = Path(dirpath)
            dirnames[:] = [
                name for name in dirnames if not (directory / name).is_symlink()
            ]
            for filename in filenames:
                path = directory / filename
                if path.is_symlink() or path.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                try:
                    raw = path.read_bytes()[:max_file_bytes]
                except OSError:
                    continue
                text = raw.decode("utf-8", errors="replace")
                for line_number, line in enumerate(text.splitlines(), start=1):
                    haystack = line if case_sensitive else line.lower()
                    if needle in haystack:
                        matches.append(
                            {
                                "path": path.relative_to(workspace).as_posix(),
                                "line": line_number,
                                "text": line[:300],
                            }
                        )
                        if len(matches) >= max_results:
                            return matches
        return matches
