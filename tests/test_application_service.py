from __future__ import annotations

from pathlib import Path

import pytest

from fetchcore.application import (
    BackendFactory,
    BackendRegistry,
    RepositoryBinding,
    RetrievalApplicationService,
    SearchRequest,
    ServingLimits,
    SourceReadRequest,
)
from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.search import (
    KnowledgeSearchBackend,
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchRequirement,
    SearchResult,
    SearchScope,
)


class FixtureBackend:
    backend_id = "fixture"
    retrieval_capabilities = RetrievalBackendCapabilities(lexical=True, canonical_query=True)

    def __init__(self) -> None:
        self.calls: list[tuple[SearchIntent, SearchScope, tuple[SearchRequirement, ...]]] = []

    def search(
        self,
        intent: SearchIntent,
        scope: SearchScope,
        requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        self.calls.append((intent, scope, requirements))
        return KnowledgeSearchResponse(
            backend=self.backend_id,
            status="success",
            results=(
                SearchResult(
                    id="hit-1",
                    source="src/demo.py",
                    source_ref="repository://owner/repo/src/demo.py",
                    knowledge_type="code",
                    content="def demo(): pass",
                    score=0.9,
                    revision=scope.revision,
                    checksum="sha256:fixture",
                ),
            ),
        )


def _service(tmp_path: Path, backend: FixtureBackend) -> RetrievalApplicationService:
    factories: dict[str, BackendFactory] = {"fixture": lambda: backend}
    return RetrievalApplicationService(
        {
            "owner/repo": RepositoryBinding(
                repository="owner/repo",
                workspace=tmp_path,
                backend="fixture",
                revision="rev-1",
                settings={"collection": "fixture"},
                knowledge_types=("code",),
            )
        },
        backends=BackendRegistry(factories),
    )


def test_search_routes_through_backend_neutral_application_boundary(tmp_path: Path) -> None:
    backend = FixtureBackend()
    service = _service(tmp_path, backend)

    response = service.search(
        SearchRequest(
            query="medication history",
            repository="owner/repo",
            limit=7,
        )
    )

    assert response.status == "success"
    assert response.results[0].source_ref == "repository://owner/repo/src/demo.py"
    intent, scope, requirements = backend.calls[0]
    assert intent.canonical_query == "medication history"
    assert intent.semantic_terms == ()
    assert scope.repository == "owner/repo"
    assert scope.max_results == 7
    assert scope.knowledge_types == ("code",)
    assert scope.settings == {"collection": "fixture"}
    assert requirements == ()


def test_serving_limits_are_explicit_and_fail_closed(tmp_path: Path) -> None:
    backend = FixtureBackend()
    binding = RepositoryBinding(
        repository="owner/repo", workspace=tmp_path, backend="fixture"
    )
    service = RetrievalApplicationService(
        {binding.repository: binding},
        backends=BackendRegistry({"fixture": lambda: backend}),
        limits=ServingLimits(search_results=2, source_lines=2, source_bytes=100),
    )
    with pytest.raises(KnowledgeSearchError) as exc:
        service.search(SearchRequest(query="needle", limit=3))
    assert exc.value.category == "schema_mismatch"

    source = tmp_path / "source.txt"
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    result = service.get_source(SourceReadRequest(source_ref="source.txt"))
    assert result.content == "one\ntwo"
    assert result.truncated is True

    with pytest.raises(ValueError):
        ServingLimits(source_bytes=0)


def test_search_fails_closed_outside_authorized_repository(tmp_path: Path) -> None:
    service = _service(tmp_path, FixtureBackend())

    with pytest.raises(KnowledgeSearchError) as exc:
        service.search(SearchRequest(query="needle", repository="other/repo"))

    assert exc.value.category == "unauthorized_repository"


def test_get_source_reads_bounded_range_from_repository_source_ref(tmp_path: Path) -> None:
    source = tmp_path / "src" / "demo.py"
    source.parent.mkdir()
    source.write_text("one\ntwo\nthree\nfour\nfive\n", encoding="utf-8")
    service = _service(tmp_path, FixtureBackend())

    result = service.get_source(
        SourceReadRequest(
            source_ref="repository://owner/repo/src/demo.py",
            line_start=3,
            line_end=3,
            context_lines=1,
        )
    )

    assert result.repository == "owner/repo"
    assert result.path == "src/demo.py"
    assert result.content == "two\nthree\nfour"
    assert result.line_start == 2
    assert result.line_end == 4
    assert result.total_lines == 5


def test_get_source_rejects_path_escape(tmp_path: Path) -> None:
    service = _service(tmp_path, FixtureBackend())

    with pytest.raises(KnowledgeSearchError) as exc:
        service.get_source(
            SourceReadRequest(
                source_ref="../outside.txt",
                repository="owner/repo",
            )
        )

    assert exc.value.category == "unauthorized_source"


def test_get_source_rejects_repository_mismatch(tmp_path: Path) -> None:
    source = tmp_path / "src" / "demo.py"
    source.parent.mkdir()
    source.write_text("content\n", encoding="utf-8")
    service = _service(tmp_path, FixtureBackend())

    with pytest.raises(KnowledgeSearchError) as exc:
        service.get_source(
            SourceReadRequest(
                source_ref="repository://owner/repo/src/demo.py",
                repository="other/repo",
            )
        )

    assert exc.value.category == "unauthorized_repository"


def test_backend_registry_fails_closed_for_unknown_backend() -> None:
    registry = BackendRegistry({})

    with pytest.raises(KnowledgeSearchError) as exc:
        registry.create("missing")

    assert exc.value.category == "unsupported_backend"


def test_fixture_backend_satisfies_search_protocol() -> None:
    backend: KnowledgeSearchBackend = FixtureBackend()

    assert backend.backend_id == "fixture"


def test_serving_preserves_unmodified_canonical_query_and_explicit_legacy_inputs(tmp_path: Path) -> None:
    backend = FixtureBackend()
    query = "  Where is the repository-local .fetchcore/indexing.yaml policy loaded and validated?\n"
    _service(tmp_path, backend).search(SearchRequest(query=query, exact_terms=(" IndexPolicy ",)))
    intent, _, _ = backend.calls[0]
    assert intent.canonical_query == query
    assert intent.semantic_terms == ()
    assert intent.exact_terms == ("IndexPolicy",)
    assert intent.anchors == () and intent.expression is None


def test_serving_rejects_backend_that_cannot_consume_canonical_query(tmp_path: Path) -> None:
    backend = FixtureBackend()
    backend.retrieval_capabilities = RetrievalBackendCapabilities(lexical=True)
    with pytest.raises(KnowledgeSearchError) as caught:
        _service(tmp_path, backend).search(SearchRequest(query="canonical query"))
    assert caught.value.category == "unsupported_semantics"
    assert backend.calls == []
