"""Git-backed repository materialization for canonical indexing snapshots.

The provider keeps a persistent bare mirror so historical revisions remain
available for the existing Git delta detector, then exposes one exact revision
through a temporary detached worktree. Repository indexing itself remains
transport-agnostic.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from fetchcore.repository.sources import (
    MaterializedRepository,
    RepositorySourceRequest,
    repository_identity_from_remote,
)


DEFAULT_REPOSITORY_CACHE = Path("~/.cache/fetchcore/repositories").expanduser()
FETCHCORE_REPOSITORY_CACHE_ENV = "FETCHCORE_REPOSITORY_CACHE"
LEGACY_REPOSITORY_CACHE_ENV = "AGENTSORCH_REPOSITORY_CACHE"
DEFAULT_GIT_TIMEOUT_SECONDS = 120.0


class GitRepositorySourceError(ValueError):
    """Raised when a Git repository cannot be safely materialized."""


class GitRepositorySourceProvider:
    """Materialize exact Git revisions from a persistent history-preserving mirror."""

    provider_id = "git"

    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Git timeout_seconds must be positive")
        configured_root = (
            os.environ.get(FETCHCORE_REPOSITORY_CACHE_ENV)
            or os.environ.get(LEGACY_REPOSITORY_CACHE_ENV)
        )
        root = (
            cache_root
            if cache_root is not None
            else Path(configured_root).expanduser()
            if configured_root
            else DEFAULT_REPOSITORY_CACHE
        )
        self.cache_root = root.expanduser().resolve() / "git"
        self.environment = dict(environment or {})
        self.timeout_seconds = timeout_seconds

    @contextmanager
    def materialize(
        self,
        request: RepositorySourceRequest,
    ) -> Iterator[MaterializedRepository]:
        remote = str(request.source).strip()
        if not remote:
            raise GitRepositorySourceError("Git repository source must not be empty")

        safe_remote = _safe_remote(remote)
        repository = request.repository or repository_identity_from_remote(
            safe_remote,
            fallback="repository",
        )
        self.cache_root.mkdir(parents=True, exist_ok=True)
        mirror = self.cache_root / f"{_cache_key(safe_remote)}.git"
        self._ensure_mirror(remote, safe_remote=safe_remote, mirror=mirror)
        self._fetch_mirror(mirror, safe_remote=safe_remote)

        branch = request.branch or self._default_branch(mirror)
        revision_spec = request.revision or branch or "HEAD"
        revision = self._resolve_revision(mirror, revision_spec)

        worktree_parent = self.cache_root / "worktrees"
        worktree_parent.mkdir(parents=True, exist_ok=True)
        workspace = Path(
            tempfile.mkdtemp(
                prefix=f"{_safe_name(repository)}-",
                dir=worktree_parent,
            )
        )
        # git worktree requires the destination itself not to exist.
        workspace.rmdir()

        try:
            self._git(
                (
                    "--git-dir",
                    str(mirror),
                    "worktree",
                    "add",
                    "--detach",
                    str(workspace),
                    revision,
                ),
                operation="materialize worktree",
                sensitive_values=(remote,),
            )
            yield MaterializedRepository(
                workspace=workspace.resolve(),
                repository=repository,
                branch=branch,
                revision=revision,
                provider=self.provider_id,
                source_ref=f"git:{repository}@{revision}",
            )
        finally:
            if workspace.exists():
                try:
                    self._git(
                        (
                            "--git-dir",
                            str(mirror),
                            "worktree",
                            "remove",
                            "--force",
                            str(workspace),
                        ),
                        operation="remove worktree",
                        sensitive_values=(remote,),
                    )
                except GitRepositorySourceError:
                    shutil.rmtree(workspace, ignore_errors=True)
            self._git_best_effort(("--git-dir", str(mirror), "worktree", "prune"))

    def _ensure_mirror(self, remote: str, *, safe_remote: str, mirror: Path) -> None:
        if mirror.exists():
            if not mirror.is_dir():
                raise GitRepositorySourceError(
                    f"Git mirror cache path is not a directory: {mirror}"
                )
            bare = self._git(
                ("--git-dir", str(mirror), "rev-parse", "--is-bare-repository"),
                operation="validate mirror cache",
                sensitive_values=(remote,),
            )
            if bare.strip() != "true":
                raise GitRepositorySourceError(
                    f"Git mirror cache is not a bare repository: {mirror}"
                )
            return

        try:
            self._git(
                ("clone", "--mirror", remote, str(mirror)),
                operation=f"clone mirror {safe_remote}",
                sensitive_values=(remote,),
            )
        except GitRepositorySourceError:
            shutil.rmtree(mirror, ignore_errors=True)
            raise

    def _fetch_mirror(self, mirror: Path, *, safe_remote: str) -> None:
        self._git(
            ("--git-dir", str(mirror), "fetch", "--prune", "origin"),
            operation=f"fetch mirror {safe_remote}",
        )

    def _default_branch(self, mirror: Path) -> str | None:
        value = self._git_optional(
            ("--git-dir", str(mirror), "symbolic-ref", "--short", "HEAD")
        )
        if value is None:
            return None
        return value.removeprefix("refs/heads/") or None

    def _resolve_revision(self, mirror: Path, revision: str) -> str:
        candidates = [revision]
        if not revision.startswith("refs/"):
            candidates.insert(0, f"refs/heads/{revision}")
        for candidate in candidates:
            value = self._git_optional(
                (
                    "--git-dir",
                    str(mirror),
                    "rev-parse",
                    "--verify",
                    f"{candidate}^{{commit}}",
                )
            )
            if value:
                return value
        raise GitRepositorySourceError(
            f"Git revision could not be resolved from mirror: {revision!r}"
        )

    def _git(
        self,
        args: Sequence[str],
        *,
        operation: str,
        sensitive_values: Sequence[str] = (),
    ) -> str:
        completed = self._run(args)
        if completed.returncode == 0:
            return completed.stdout.strip()
        message = completed.stderr.strip() or completed.stdout.strip() or "Git command failed"
        for value in sensitive_values:
            if value:
                message = message.replace(value, _safe_remote(value))
        raise GitRepositorySourceError(f"{operation} failed: {message}")

    def _git_optional(self, args: Sequence[str]) -> str | None:
        completed = self._run(args)
        if completed.returncode != 0:
            return None
        return completed.stdout.strip() or None

    def _git_best_effort(self, args: Sequence[str]) -> None:
        try:
            self._run(args)
        except GitRepositorySourceError:
            return

    def _run(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment.update(self.environment)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        try:
            return subprocess.run(
                ["git", *args],
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise GitRepositorySourceError("unable to execute Git") from exc


def _safe_remote(remote: str) -> str:
    """Remove URL userinfo so cache identity and diagnostics never require secrets."""

    value = remote.strip()
    if "://" not in value:
        return value
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    try:
        port = parsed.port
    except ValueError:
        port = None
    if port is not None:
        host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))


def _cache_key(remote: str) -> str:
    normalized = remote.strip().removesuffix("/").removesuffix(".git")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


def _safe_name(repository: str) -> str:
    value = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in repository
    )
    value = value.strip("-")
    return (value or "repository")[:80]
