"""Repository acquisition contracts for indexing workflows."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse


@dataclass(frozen=True)
class RepositorySourceRequest:
    """Describe a repository source before it is materialized for indexing."""

    source: str | Path
    repository: str | None = None
    branch: str | None = None
    revision: str | None = None


@dataclass(frozen=True)
class MaterializedRepository:
    """Canonical local snapshot exposed to the repository indexer."""

    workspace: Path
    repository: str
    branch: str | None
    revision: str | None
    provider: str
    source_ref: str

    def to_mapping(self) -> dict[str, object]:
        payload = asdict(self)
        payload["workspace"] = str(self.workspace)
        return payload


class RepositorySourceProvider(Protocol):
    """Materialize an arbitrary repository source as a local snapshot."""

    provider_id: str

    def materialize(
        self, request: RepositorySourceRequest
    ) -> AbstractContextManager[MaterializedRepository]: ...


class LocalWorkspaceSourceProvider:
    """Expose an existing local workspace without copying or mutating it."""

    provider_id = "local-workspace"

    @contextmanager
    def materialize(self, request: RepositorySourceRequest) -> Iterator[MaterializedRepository]:
        workspace = Path(request.source).expanduser().resolve(strict=True)
        if not workspace.is_dir():
            raise ValueError(f"repository workspace is not a directory: {request.source}")

        repository = request.repository or resolve_repository_identity(workspace)
        branch = (
            request.branch
            if request.branch is not None
            else _git_value(workspace, ("branch", "--show-current"))
        )
        revision = request.revision or _git_value(workspace, ("rev-parse", "HEAD"))

        yield MaterializedRepository(
            workspace=workspace,
            repository=repository,
            branch=branch or None,
            revision=revision,
            provider=self.provider_id,
            source_ref=workspace.as_uri(),
        )


def resolve_repository_identity(workspace: Path) -> str:
    """Resolve a stable repository identity from origin, falling back to directory name."""

    remote = _git_value(workspace, ("remote", "get-url", "origin"))
    if not remote:
        return workspace.name
    return repository_identity_from_remote(remote, fallback=workspace.name)


def repository_identity_from_remote(remote: str, *, fallback: str) -> str:
    """Normalize common HTTPS, SSH and scp-style Git remotes to owner/repository."""

    value = remote.strip().removesuffix(".git")
    if not value:
        return fallback
    if "://" in value:
        parsed = urlparse(value)
        path = parsed.path.strip("/")
        return path or fallback
    if "@" in value and ":" in value:
        path = value.split(":", 1)[1].strip("/")
        return path or fallback
    return value.strip("/") or fallback


def _git_value(workspace: Path, args: Sequence[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=workspace,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None
