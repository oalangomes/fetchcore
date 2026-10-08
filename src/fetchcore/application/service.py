"""Backend-neutral application boundary for FetchCore retrieval serving."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from fetchcore.retrieval import RetrievalConfig
from .targeted import search_with_targeted
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
    ) -> None:
        if not bindings:
            raise ValueError("at least one repository binding is required")
        self._bindings = dict(bindings)
        for key, binding in self._bindings.items():
            if key != binding.repository:
                raise ValueError("repository binding key must equal binding.repository")
        self._backends = backends or BackendRegistry()
        self._limits = limits or ServingLimits()

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
        if request.revision is not None and request.revision != binding.revision:
            raise KnowledgeSearchError(
                "source revision does not match the authorized materialization",
                category="source_revision_mismatch",
            )
        root = binding.workspace.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise KnowledgeSearchError(
                "source_ref escapes the authorized repository workspace",
                category="unauthorized_source",
                metadata={"repository": repository, "source_ref": request.source_ref},
            ) from exc
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

        text = candidate.read_text(encoding="utf-8", errors="replace")
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
