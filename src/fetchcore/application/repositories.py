"""Repository registration application boundary; no indexing or backend composition."""

from __future__ import annotations

from pathlib import Path
from functools import cached_property
from urllib.parse import unquote, urlsplit

from fetchcore.repository.git_source import GitRepositorySourceError, GitRepositorySourceProvider
from fetchcore.repository.registry import (
    RegisteredRepository,
    RepositoryRegistry,
    RepositoryRegistryError,
    local_source_path,
    normalize_source,
    validate_reference,
)
from fetchcore.repository.sources import (
    LocalWorkspaceSourceProvider,
    RepositorySourceProvider,
    RepositorySourceRequest,
)


class RepositoryApplicationService:
    def __init__(
        self,
        *,
        cache_root: Path | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> None:
        self._cache_root = cache_root
        self.allowed_roots = allowed_roots if allowed_roots is not None else (Path.cwd(),)

    @cached_property
    def git(self) -> GitRepositorySourceProvider:
        # Reuse operator cache precedence; resolve lazily inside the operation's error boundary.
        try:
            return GitRepositorySourceProvider(cache_root=self._cache_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise RepositoryRegistryError("unsafe_source", "Invalid repository cache path") from exc

    @cached_property
    def registry(self) -> RepositoryRegistry:
        return RepositoryRegistry(self.git.cache_root.parent / "registry.json")

    def _records(self) -> tuple[RegisteredRepository, ...]:
        try:
            self.git.validate_cache_paths("registry")
        except GitRepositorySourceError as exc:
            raise RepositoryRegistryError("unsafe_source", "Unsafe repository cache path") from exc
        records = self.registry.read()
        for record in records:
            # Persisted state is validated before presentation or reuse. Do not disclose raw failures.
            try:
                roots = (
                    (Path(record.source).parent,) if record.provider == "local-workspace" else ()
                )
                if record.source.startswith("file://"):
                    roots = (Path(unquote(urlsplit(record.source).path)).parent,)
                elif record.provider == "git" and Path(record.source).is_absolute():
                    roots = (Path(record.source).parent,)
                provider, source = normalize_source(record.source, record.provider, roots)
                if (provider, source) != (record.provider, record.source):
                    raise ValueError("Noncanonical source")
                if record.provider == "git":
                    if record.cache_path != str(self.git.cache_path_for(record.source)):
                        raise ValueError("Incompatible cache path")
                    if record.source_ref != f"git:{record.repository}@{record.revision}":
                        raise ValueError("Incompatible source reference")
                elif record.source_ref != Path(record.source).as_uri():
                    raise ValueError("Incompatible source reference")
            except (ValueError, OSError) as exc:
                raise RepositoryRegistryError(
                    "registry_persistence_failure",
                    "Invalid repository registration",
                ) from exc
        return records

    def add(
        self,
        source: str,
        *,
        provider: str | None = None,
    ) -> RegisteredRepository:
        provider, source = normalize_source(source, provider, self.allowed_roots)
        self._records()  # Validate before creating even a writer lock.
        with self.registry.writer():
            records = self._records()
            for record in records:
                same_source = record.source == source or (
                    provider == record.provider == "git"
                    and self.git.cache_path_for(record.source) == self.git.cache_path_for(source)
                )
                if record.provider == provider and same_source:
                    self._require_materialization(record)
                    return record
            acquisition: RepositorySourceProvider = (
                self.git if provider == "git" else LocalWorkspaceSourceProvider()
            )
            if provider == "git":
                try:
                    self.git.validate_cache_paths(source)
                except GitRepositorySourceError as exc:
                    raise RepositoryRegistryError(
                        "unsafe_source", "Unsafe Git cache target"
                    ) from exc
            try:
                with acquisition.materialize(RepositorySourceRequest(source=source)) as snapshot:
                    validate_reference(snapshot.repository)
                    record = RegisteredRepository(
                        repository=snapshot.repository,
                        provider=snapshot.provider,
                        source=source,
                        source_ref=snapshot.source_ref,
                        branch=snapshot.branch,
                        revision=snapshot.revision,
                        workspace=str(snapshot.workspace)
                        if provider == "local-workspace"
                        else None,
                        cache_path=str(self.git.cache_path_for(source))
                        if provider == "git"
                        else None,
                    )
            except RepositoryRegistryError:
                raise
            except (ValueError, OSError) as exc:
                raise RepositoryRegistryError(
                    "materialization_failure",
                    "Repository source could not be materialized",
                ) from exc
            if any(r.repository == record.repository for r in records):
                raise RepositoryRegistryError(
                    "ambiguous_repository",
                    "Canonical identity is already registered for another source",
                )
            self.registry.write((*records, record))
            return record

    def list(self) -> tuple[RegisteredRepository, ...]:
        return self._records()

    def inspect(self, reference: str) -> RegisteredRepository:
        validate_reference(reference)
        records = self._records()
        matches = [r for r in records if r.repository == reference]
        if not matches and "/" not in reference:
            matches = [r for r in records if r.repository.rsplit("/", 1)[-1] == reference]
        if not matches:
            raise RepositoryRegistryError("repository_not_found", "Repository is not registered")
        if len(matches) != 1:
            raise RepositoryRegistryError("ambiguous_repository", "Use the full canonical identity")
        self._require_materialization(matches[0])
        return matches[0]

    def _require_materialization(self, record: RegisteredRepository) -> None:
        normalize_source(record.source, record.provider, self.allowed_roots)
        if record.provider == "local-workspace":
            assert record.workspace is not None
            path = local_source_path(record.workspace, self.allowed_roots)
            if str(path) != record.workspace:
                raise RepositoryRegistryError(
                    "unsafe_source", "Registered workspace was redirected"
                )
        else:
            try:
                self.git.validate_cache_paths(record.source)
            except GitRepositorySourceError as exc:
                raise RepositoryRegistryError("unsafe_source", "Unsafe Git cache target") from exc
            path = self.git.cache_path_for(record.source)
        if not path.is_dir():
            raise RepositoryRegistryError(
                "missing_materialization",
                "Registered workspace or Git mirror is absent; add does not refresh",
            )

    def present(self, record: RegisteredRepository) -> dict[str, object]:
        """Same facts for human/JSON; presence is not a claim of revision currentness."""
        path = Path(record.workspace or record.cache_path or "")
        state = "present" if path.is_dir() and not path.is_symlink() else "absent"
        return {
            **record.to_mapping(),
            "registration_status": "registered",
            "materialization_state": state,
        }
