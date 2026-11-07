"""Backend-neutral application boundary for FetchCore retrieval serving."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from fetchcore.retrieval import RetrievalConfig
from fetchcore.repository.api_artifact_policy import blocked_api_artifact_kind
from .targeted import search_with_targeted
from .navigation import (
    FindSymbolRequest, GetRelatedRequest, NavigationResult,
    RELATION_CAPABILITIES, RepositoryKnowledgeNavigationService,
    navigation_result, validate_navigation_request,
)
from fetchcore.repository.navigation import RelatedEvidence, StructuralNavigationError, SymbolDefinition
from fetchcore.knowledge.structure import StructuralCapability, StructuralCoverage
from fetchcore.application.index_store import OperationalIndexStore
from fetchcore.repository.sources import LocalWorkspaceSourceProvider, RepositorySourceRequest
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchMode,
    SearchScope,
)


MAX_SEARCH_LIMIT = 100
MAX_SOURCE_LINES = 400
MAX_SOURCE_BYTES = 1_000_000


def resolve_source_ref_repository_matches(
    source_ref: str,
    repositories: tuple[str, ...],
) -> tuple[str, ...]:
    """Match a canonical repository source_ref against authorized identities."""
    raw = unquote(source_ref.strip()).split("#", 1)[0]
    if not raw.startswith("repository://"):
        return ()
    return tuple(
        repository
        for repository in repositories
        if raw.startswith(f"repository://{repository}/")
    )


@dataclass(frozen=True)
class ServingLimits:
    """Caller-selected safety bounds for retrieval and source serving."""

    search_results: int = MAX_SEARCH_LIMIT
    source_lines: int = MAX_SOURCE_LINES
    source_bytes: int = MAX_SOURCE_BYTES
    context_lines: int = 100

    def __post_init__(self) -> None:
        if min(self.search_results, self.source_lines, self.source_bytes) < 1:
            raise ValueError("serving limits must be positive")
        if self.context_lines < 0:
            raise ValueError("serving context_lines must be non-negative")

from .composition import BackendRegistry


@dataclass(frozen=True)
class RepositoryBinding:
    """Authorized repository scope exposed by a serving application."""

    repository: str
    workspace: Path
    backend: str
    revision: str | None = None
    settings: dict[str, object] = field(default_factory=dict)
    retrieval: RetrievalConfig | None = None
    knowledge_types: tuple[str, ...] = ()
    repository_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.repository.strip():
            raise ValueError("repository binding requires repository")
        if not self.backend.strip():
            raise ValueError("repository binding requires backend")


@dataclass(frozen=True)
class SearchRequest:
    """Consumer-neutral request accepted by the retrieval application service."""

    query: str = ""
    repository: str | None = None
    limit: int = 10
    exact_terms: tuple[str, ...] = ()
    knowledge_types: tuple[str, ...] = ()
    mode: SearchMode = "discovery"
    intent: SearchIntent | None = None


@dataclass(frozen=True)
class SourceReadRequest:
    """Bounded source read; optional revision requires an exact materialized match."""

    source_ref: str
    repository: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    context_lines: int = 0
    revision: str | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class SourceReadResult:
    """Bounded source payload returned to CLI/MCP/API adapters."""

    repository: str
    source_ref: str
    path: str
    revision: str | None
    content: str
    line_start: int
    line_end: int
    total_lines: int
    truncated: bool

    def to_mapping(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "source_ref": self.source_ref,
            "path": self.path,
            "revision": self.revision,
            "content": self.content,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "total_lines": self.total_lines,
            "truncated": self.truncated,
        }


class RetrievalApplicationService:
    """Serve retrieval and source access under explicit repository authority."""

    def __init__(
        self,
        bindings: Mapping[str, RepositoryBinding],
        *,
        backends: BackendRegistry | None = None,
        limits: ServingLimits | None = None,
        navigation_store: Callable[[RepositoryBinding], OperationalIndexStore] | None = None,
    ) -> None:
        if not bindings:
            raise ValueError("at least one repository binding is required")
        self._bindings = dict(bindings)
        for key, binding in self._bindings.items():
            if key != binding.repository:
                raise ValueError("repository binding key must equal binding.repository")
        self._backends = backends or BackendRegistry()
        self._limits = limits or ServingLimits()
        self._navigation_store = navigation_store

    @property
    def repositories(self) -> tuple[str, ...]:
        return tuple(sorted(self._bindings))

    def search(self, request: SearchRequest) -> KnowledgeSearchResponse:
        query = request.query.strip()
        exact_terms = tuple(item.strip() for item in request.exact_terms if item.strip())
        if request.intent is not None and (query or exact_terms):
            raise KnowledgeSearchError("intent cannot be combined with query/exact_terms",
                                       category="schema_mismatch")
        if not query and not exact_terms and request.intent is None:
            raise KnowledgeSearchError(
                "search requires query or exact_terms",
                category="schema_mismatch",
            )
        if not 1 <= request.limit <= self._limits.search_results:
            raise KnowledgeSearchError(
                f"search limit must be between 1 and {self._limits.search_results}",
                category="schema_mismatch",
                metadata={"limit": request.limit},
            )
        binding = self._binding(request.repository)
        backend = self._backends.create(binding.backend)
        knowledge_types = request.knowledge_types or binding.knowledge_types
        if binding.knowledge_types and not set(knowledge_types) <= set(binding.knowledge_types):
            raise KnowledgeSearchError("knowledge types exceed repository binding",
                                       category="unauthorized_source")
        intent = request.intent or SearchIntent(
            exact_terms=exact_terms,
            canonical_query=request.query,
        )
        scope = SearchScope(
            workspace=binding.workspace,
            revision=binding.revision,
            repository=binding.repository,
            knowledge_types=knowledge_types,
            max_results=request.limit,
            mode=request.mode,
            repository_paths=binding.repository_paths,
            result_namespace="fetchcore-serving",
            settings=dict(binding.settings),
            retrieval=binding.retrieval,
        )
        if not (intent.canonical_query.strip() or intent.terms or intent.anchors
                or intent.expression or intent.signals):
            raise KnowledgeSearchError("search intent is empty", category="schema_mismatch")
        if any(anchor.kind in ("path", "source_ref") for anchor in intent.anchors):
            scope = replace(scope, settings={**scope.settings,
                "targeted_max_lines": min(self._limits.source_lines,
                    int(str(scope.settings.get("targeted_max_lines", self._limits.source_lines)))),
                "targeted_max_bytes": self._limits.source_bytes,
            })
        return search_with_targeted(backend, intent, scope)

    def find_symbol(self, request: FindSymbolRequest) -> NavigationResult:
        """Find exact declarations without invoking a retrieval backend."""
        return self._navigate(request)

    def get_related(self, request: GetRelatedRequest) -> NavigationResult:
        """Read bounded directed observations by logical identity or source_ref."""
        return self._navigate(request)

    def _navigate(self, request: FindSymbolRequest | GetRelatedRequest) -> NavigationResult:
        from .index_operations import prepare_repository_index_runtime

        try:
            validate_navigation_request(request)
            binding = self._binding(request.repository)
            path = request.path if isinstance(request, FindSymbolRequest) else None
            source = request.source if isinstance(request, GetRelatedRequest) else None
            if source is not None and not source.startswith("symbol:v1:sha256:"):
                _, path = self._resolve_source_ref(source, requested_repository=binding.repository)
                source = f"repository://{binding.repository}/{path}"
            if path is not None:
                _, path = self._resolve_source_ref(path, requested_repository=binding.repository)
            runtime = prepare_repository_index_runtime(
                binding.workspace, repository=binding.repository, branch=None, revision=None,
            )
            store = self._navigation_store(binding) if self._navigation_store else self._default_navigation_store(binding)
            snapshot = store.load_snapshot(binding.repository)
            observed_fingerprint = getattr(snapshot, "indexing_fingerprint", None)
            if observed_fingerprint != runtime.fingerprint:
                raise KnowledgeSearchError(
                    "navigation snapshot indexing contract is incompatible",
                    category="schema_mismatch",
                    metadata={"rebuild_required": True},
                )
            for expected in (binding.revision, request.revision):
                if expected is not None and expected != snapshot.revision:
                    raise KnowledgeSearchError(
                        "navigation revision does not match the materialized source",
                        category="source_revision_mismatch",
                    )
            materialized = store.load_documents(binding.repository)
            from fetchcore.projection.core import corpus_checksum
            if (len(materialized) != snapshot.document_count
                    or corpus_checksum(materialized) != snapshot.corpus_checksum):
                raise KnowledgeSearchError(
                    "navigation documents do not match the active snapshot checksum",
                    category="schema_mismatch",
                )
            if any(doc.repository != binding.repository or doc.revision != snapshot.revision
                   for doc in materialized):
                raise KnowledgeSearchError(
                    "navigation documents do not match the active snapshot",
                    category="schema_mismatch",
                )
            documents = tuple(doc for doc in materialized if (
                (not binding.knowledge_types or doc.knowledge_type in binding.knowledge_types)
                and (not binding.repository_paths or any(
                    doc.path == prefix or doc.path.startswith(prefix.rstrip("/") + "/")
                    for prefix in binding.repository_paths
                ))
            ))
            navigation = RepositoryKnowledgeNavigationService(documents)
            results: tuple[SymbolDefinition, ...] | tuple[RelatedEvidence, ...] = ()
            if isinstance(request, FindSymbolRequest):
                coverage: tuple[StructuralCoverage, ...] = (
                    navigation.coverage(language=request.language, path=path),
                )
                try:
                    results = navigation.find_symbol(
                        request.name, language=request.language, kind=request.kind,
                        path=path, limit=request.limit,
                    )
                except StructuralNavigationError as exc:
                    if exc.category != "unsupported_capability":
                        raise
            else:
                assert source is not None
                selected = None
                if source.startswith("symbol:v1:sha256:"):
                    definitions = navigation.find_symbol(source, limit=2)
                    if not definitions:
                        raise StructuralNavigationError(
                            "symbol is absent from the selected snapshot", category="source_missing",
                        )
                    selected = definitions[0]
                    path = selected.path
                capabilities: tuple[StructuralCapability, ...] = tuple(dict.fromkeys(
                    RELATION_CAPABILITIES[kind] for kind in request.relation_types
                )) if request.relation_types else (
                    ("calls_observed", "references_observed", "tests_observed")
                    if selected else ("imports",)
                )
                if request.require_resolution:
                    capabilities += ("cross_file_resolution",)
                coverage = tuple(navigation.coverage(capability, path=path)
                                 for capability in capabilities)
                try:
                    results = navigation.get_related(
                        selected or source, relation_types=request.relation_types,
                        limit=request.limit, require_resolution=request.require_resolution,
                    )
                except StructuralNavigationError as exc:
                    if exc.category != "unsupported_capability":
                        raise
            return navigation_result(
                binding.repository, snapshot.revision, results, coverage, request.limit,
            )
        except StructuralNavigationError as exc:
            raise KnowledgeSearchError(str(exc), category=exc.category) from exc
        except KnowledgeSearchError:
            raise
        except ValueError as exc:
            raise KnowledgeSearchError(str(exc), category="schema_mismatch") from exc
        except OSError as exc:
            raise KnowledgeSearchError("canonical source could not be read",
                                       category="source_missing") from exc

    @staticmethod
    def _default_navigation_store(binding: RepositoryBinding) -> OperationalIndexStore:
        from .indexing import build_index_context
        from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store
        from fetchcore.adapters.retrieval.solr.backend import SolrIndexStore

        context = build_index_context(
            workspace=binding.workspace, repository=binding.repository,
            branch=None, revision=None, backend=binding.backend,
            backend_settings=binding.settings, retrieval=binding.retrieval,
        )
        if isinstance(context.store, SolrIndexStore):
            return bind_indexing_contract_store(context.store, context.runtime.indexer)
        return context.store

    def get_source(self, request: SourceReadRequest) -> SourceReadResult:
        if not request.source_ref.strip():
            raise KnowledgeSearchError(
                "source_ref must not be empty",
                category="schema_mismatch",
            )
        if not 0 <= request.context_lines <= self._limits.context_lines:
            raise KnowledgeSearchError(
                f"context_lines must be between 0 and {self._limits.context_lines}",
                category="schema_mismatch",
            )

        repository, relative_path = self._resolve_source_ref(
            request.source_ref,
            requested_repository=request.repository,
        )
        binding = self._bindings[repository]
        authorized_checksums: set[str] | None = None
        if binding.repository_paths and not any(
            relative_path == prefix or relative_path.startswith(prefix.rstrip("/") + "/")
            for prefix in binding.repository_paths
        ):
            raise KnowledgeSearchError(
                "source path is outside the authorized repository scope",
                category="unauthorized_source",
            )
        if binding.knowledge_types:
            store = self._navigation_store(binding) if self._navigation_store else self._default_navigation_store(binding)
            snapshot = store.load_snapshot(repository)
            authorized_checksums = {
                document.provenance.source_checksum
                for document in store.load_documents(repository)
                if (
                document.repository == repository
                and document.revision == snapshot.revision
                and document.path == relative_path
                and document.knowledge_type in binding.knowledge_types
                )
            }
            if not authorized_checksums:
                raise KnowledgeSearchError(
                    "source type is outside the authorized repository scope",
                    category="unauthorized_source",
                )
        if binding.revision is None and request.revision is not None:
            with LocalWorkspaceSourceProvider().materialize(
                RepositorySourceRequest(binding.workspace, repository=repository)
            ) as source:
                revision = source.revision
            if revision is None:
                store = self._navigation_store(binding) if self._navigation_store else self._default_navigation_store(binding)
                revision = store.load_snapshot(repository).revision
            binding = replace(binding, revision=revision)
        if blocked_api_artifact_kind(relative_path) is not None:
            raise KnowledgeSearchError(
                "raw API artifact serving is blocked pending sanitized extraction",
                category="unsafe_source",
            )
        if request.revision is not None and request.revision != binding.revision:
            raise KnowledgeSearchError(
                "source revision does not match the authorized materialization",
                category="source_revision_mismatch",
            )
        root = binding.workspace.resolve()
        candidate = (root / relative_path).resolve()
        try:
            resolved_relative = candidate.relative_to(root).as_posix()
        except ValueError as exc:
            raise KnowledgeSearchError(
                "source_ref escapes the authorized repository workspace",
                category="unauthorized_source",
                metadata={"repository": repository, "source_ref": request.source_ref},
            ) from exc
        if binding.repository_paths and not any(
            resolved_relative == prefix or resolved_relative.startswith(prefix.rstrip("/") + "/")
            for prefix in binding.repository_paths
        ):
            raise KnowledgeSearchError(
                "resolved source path is outside the authorized repository scope",
                category="unauthorized_source",
            )
        if not candidate.is_file():
            raise KnowledgeSearchError(
                "source_ref does not resolve to a readable file",
                category="source_missing",
                metadata={"repository": repository, "path": relative_path},
            )
        size = candidate.stat().st_size
        if size > self._limits.source_bytes:
            raise KnowledgeSearchError(
                "source file exceeds the serving byte limit",
                category="source_too_large",
                metadata={
                    "repository": repository,
                    "path": relative_path,
                    "size_bytes": size,
                    "max_bytes": self._limits.source_bytes,
                },
            )

        with candidate.open("rb") as stream:
            payload = stream.read(self._limits.source_bytes + 1)
        if len(payload) > self._limits.source_bytes:
            raise KnowledgeSearchError(
                "source file exceeds the serving byte limit",
                category="source_too_large",
                metadata={"repository": repository, "path": relative_path,
                          "max_bytes": self._limits.source_bytes},
            )
        actual_checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
        if authorized_checksums is not None and actual_checksum not in authorized_checksums:
            raise KnowledgeSearchError(
                "source bytes no longer match authorized knowledge",
                category="source_revision_mismatch",
            )
        if request.checksum is not None and request.checksum != actual_checksum:
            raise KnowledgeSearchError(
                "source checksum does not match the requested evidence",
                category="source_revision_mismatch",
                metadata={"repository": repository, "path": relative_path},
            )
        text = payload.decode("utf-8", errors="replace")
        if blocked_api_artifact_kind(relative_path, text) is not None:
            raise KnowledgeSearchError(
                "raw API artifact serving is blocked pending sanitized extraction",
                category="unsafe_source",
            )
        lines = text.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return SourceReadResult(
                repository=repository,
                source_ref=request.source_ref,
                path=relative_path,
                revision=binding.revision,
                content="",
                line_start=1,
                line_end=0,
                total_lines=0,
                truncated=False,
            )

        start, end = _source_window(
            total_lines,
            line_start=request.line_start,
            line_end=request.line_end,
            context_lines=request.context_lines,
            max_source_lines=self._limits.source_lines,
        )
        bounded_end = min(end, start + self._limits.source_lines - 1)
        truncated = bounded_end < end or start > 1 or bounded_end < total_lines
        return SourceReadResult(
            repository=repository,
            source_ref=request.source_ref,
            path=relative_path,
            revision=binding.revision,
            content="\n".join(lines[start - 1 : bounded_end]),
            line_start=start,
            line_end=bounded_end,
            total_lines=total_lines,
            truncated=truncated,
        )

    def _binding(self, repository: str | None) -> RepositoryBinding:
        if repository is None:
            if len(self._bindings) != 1:
                raise KnowledgeSearchError(
                    "repository must be specified when multiple repositories are configured",
                    category="repository_scope_required",
                    metadata={"repositories": sorted(self._bindings)},
                )
            return next(iter(self._bindings.values()))
        binding = self._bindings.get(repository)
        if binding is None:
            raise KnowledgeSearchError(
                "repository is outside the authorized serving scope",
                category="unauthorized_repository",
                metadata={
                    "repository": repository,
                    "repositories": sorted(self._bindings),
                },
            )
        return binding

    def _resolve_source_ref(
        self,
        source_ref: str,
        *,
        requested_repository: str | None,
    ) -> tuple[str, str]:
        raw = unquote(source_ref.strip()).split("#", 1)[0]
        if raw.startswith("repository://"):
            matches = resolve_source_ref_repository_matches(
                source_ref,
                tuple(self._bindings),
            )
            if len(matches) != 1:
                raise KnowledgeSearchError(
                    "repository source_ref is outside the authorized serving scope",
                    category="unauthorized_repository",
                    metadata={"source_ref": source_ref},
                )
            repository = matches[0]
            if requested_repository is not None and requested_repository != repository:
                raise KnowledgeSearchError(
                    "requested repository does not match source_ref",
                    category="unauthorized_repository",
                    metadata={
                        "requested_repository": requested_repository,
                        "source_repository": repository,
                    },
                )
            relative_path = raw.removeprefix(f"repository://{repository}/")
        else:
            binding = self._binding(requested_repository)
            repository = binding.repository
            relative_path = raw

        normalized = PurePosixPath(relative_path)
        if (
            not relative_path.strip()
            or normalized.is_absolute()
            or ".." in normalized.parts
        ):
            raise KnowledgeSearchError(
                "source_ref contains an invalid repository path",
                category="unauthorized_source",
                metadata={"source_ref": source_ref},
            )
        return repository, normalized.as_posix()


def _source_window(
    total_lines: int,
    *,
    line_start: int | None,
    line_end: int | None,
    context_lines: int,
    max_source_lines: int = MAX_SOURCE_LINES,
) -> tuple[int, int]:
    if line_start is None and line_end is not None:
        raise KnowledgeSearchError(
            "line_end requires line_start",
            category="schema_mismatch",
        )
    if line_start is None:
        return 1, min(total_lines, max_source_lines)
    if line_start < 1 or line_start > total_lines:
        raise KnowledgeSearchError(
            "line_start is outside the source file",
            category="schema_mismatch",
            metadata={"line_start": line_start, "total_lines": total_lines},
        )
    effective_end = line_start if line_end is None else line_end
    if effective_end < line_start:
        raise KnowledgeSearchError(
            "line_end must be greater than or equal to line_start",
            category="schema_mismatch",
        )
    effective_end = min(effective_end, total_lines)
    return max(1, line_start - context_lines), min(
        total_lines,
        effective_end + context_lines,
    )
