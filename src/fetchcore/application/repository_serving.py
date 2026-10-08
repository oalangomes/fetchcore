"""Repository-registry composition for repository-scoped serving operations."""

from __future__ import annotations

from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import replace
from pathlib import Path
from collections.abc import Iterator
from urllib.parse import unquote

from fetchcore.repository.registry import RegisteredRepository, RepositoryRegistryError
from fetchcore.repository.sources import (
    LocalWorkspaceSourceProvider,
    MaterializedRepository,
    RepositorySourceRequest,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError, KnowledgeSearchResponse

from .composition import BackendRegistry, EmbeddingProviderRegistry
from .repositories import RepositoryApplicationService
from .runtime_config import (
    FetchCoreRuntimeConfig,
    load_runtime_config,
    service_from_runtime_config,
)
from .service import (
    RetrievalApplicationService,
    SearchRequest,
    SourceReadRequest,
    SourceReadResult,
    resolve_source_ref_repository_matches,
)


class RepositoryRetrievalApplicationService:
    """Compose registered repository identity with operator-owned serving config."""

    def __init__(
        self,
        repositories: RepositoryApplicationService,
        runtime: FetchCoreRuntimeConfig,
        *,
        backends: BackendRegistry | None = None,
        providers: EmbeddingProviderRegistry | None = None,
    ) -> None:
        self._repositories = repositories
        self._runtime = runtime
        self._backends = backends
        self._providers = providers

    @classmethod
    def from_sources(
        cls,
        *,
        config_path: Path | None = None,
        cache_root: Path | None = None,
        allowed_roots: tuple[Path, ...] | None = None,
    ) -> RepositoryRetrievalApplicationService:
        """Resolve #65 runtime config and the canonical #68 repository registry."""
        return cls(
            RepositoryApplicationService(
                cache_root=cache_root,
                allowed_roots=allowed_roots,
            ),
            load_runtime_config(path=config_path),
        )

    def search(self, request: SearchRequest) -> KnowledgeSearchResponse:
        if request.repository is None:
            raise KnowledgeSearchError(
                "repository must be specified for CLI search",
                category="repository_scope_required",
            )
        registered = self._resolve_repository(request.repository)
        needs_workspace = (
            registered.provider == "local-workspace"
            or self._runtime.backend == "local-semantic"
        )
        with self._retrieval_service(registered, materialize=needs_workspace) as service:
            return service.search(replace(request, repository=registered.repository))

    def resolve_repository(self, reference: str) -> RegisteredRepository:
        """Resolve a canonical identity or unique leaf through the existing registry."""
        return self._resolve_repository(reference)

    def get_source(self, request: SourceReadRequest) -> SourceReadResult:
        registered = self._resolve_source_repository(request)
        with self._retrieval_service(
            registered,
            materialize=True,
            expected_revision=request.revision,
        ) as service:
            return service.get_source(replace(request, repository=registered.repository))

    def _resolve_repository(self, reference: str) -> RegisteredRepository:
        registered = self._repositories.inspect(reference)
        configured_reference = self._runtime.repository
        if configured_reference is not None:
            configured = self._repositories.inspect(configured_reference)
            if configured.repository != registered.repository:
                raise KnowledgeSearchError(
                    "runtime repository conflicts with the selected repository",
                    category="unauthorized_repository",
                )
        return registered

    def _resolve_source_repository(
        self,
        request: SourceReadRequest,
    ) -> RegisteredRepository:
        if request.repository is not None:
            return self._resolve_repository(request.repository)

        records = self._repositories.list()
        matches = resolve_source_ref_repository_matches(
            request.source_ref,
            tuple(record.repository for record in records),
        )
        if not matches:
            if unquote(request.source_ref.strip()).startswith("repository://"):
                raise RepositoryRegistryError(
                    "repository_not_found",
                    "Repository in source_ref is not registered",
                )
            raise KnowledgeSearchError(
                "repository must be specified for a relative source_ref",
                category="repository_scope_required",
            )
        if len(matches) != 1:
            raise RepositoryRegistryError(
                "ambiguous_repository",
                "Source reference matches multiple registered repositories",
            )
        return self._resolve_repository(matches[0])

    @contextmanager
    def _retrieval_service(
        self,
        registered: RegisteredRepository,
        *,
        materialize: bool,
        expected_revision: str | None = None,
    ) -> Iterator[RetrievalApplicationService]:
        with ExitStack() as stack:
            if materialize:
                try:
                    source = stack.enter_context(
                        self._materialization(registered)
                    )
                except (OSError, ValueError) as exc:
                    raise RepositoryRegistryError(
                        "materialization_failure",
                        "Registered source could not be materialized",
                    ) from exc
                workspace = source.workspace
                revision = source.revision
                if source.repository != registered.repository:
                    raise RepositoryRegistryError(
                        "registry_persistence_failure",
                        "Materialized repository identity does not match registration",
                    )
            else:
                workspace = Path(registered.cache_path or registered.workspace or "")
                revision = registered.revision

            if expected_revision is not None and expected_revision != revision:
                raise KnowledgeSearchError(
                    "source revision does not match the authorized materialization",
                    category="source_revision_mismatch",
                )
            if self._runtime.revision is not None and self._runtime.revision != revision:
                raise KnowledgeSearchError(
                    "runtime revision conflicts with the registered source revision",
                    category="schema_mismatch",
                )

            runtime = replace(
                self._runtime,
                workspace=workspace,
                repository=registered.repository,
                revision=revision,
            )
            yield service_from_runtime_config(
                runtime,
                backends=self._backends,
                providers=self._providers,
            )

    def _materialization(
        self,
        registered: RegisteredRepository,
    ) -> AbstractContextManager[MaterializedRepository]:
        request: RepositorySourceRequest = registered.source_request()
        if registered.provider == "git":
            return self._repositories.git.materialize(request, refresh=False)
        return LocalWorkspaceSourceProvider().materialize(request)
