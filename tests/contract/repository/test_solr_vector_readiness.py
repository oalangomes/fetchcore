from __future__ import annotations

import copy
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import pytest

from fetchcore.application import indexing
from fetchcore.application.composition import BackendRegistry, EmbeddingProviderRegistry
from fetchcore.application.service import (
    RepositoryBinding, RetrievalApplicationService, SearchRequest, SourceReadRequest,
)
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.adapters.retrieval.solr.backend import (
    SOLR_VECTOR_DIMENSIONS,
    SolrBackend,
    SolrIndexStore,
)
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent, SearchScope
from memory_solr_transport import MemoryTransport


class CountingEmbeddingProvider:
    provider_id = "fixture"

    def __init__(
        self,
        *,
        model: str = "fixture-model",
        dimensions: int = SOLR_VECTOR_DIMENSIONS,
        failure: KnowledgeSearchError | None = None,
    ) -> None:
        self.model_id = model
        self.dimensions = dimensions
        self.failure = failure
        self.calls: list[tuple[str, ...]] = []

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(texts)
        if self.failure is not None:
            raise self.failure
        return tuple(tuple(0.25 for _ in range(self.dimensions)) for _ in texts)


class VectorLifecycleTransport(MemoryTransport):
    def __init__(self) -> None:
        super().__init__()
        self.committed: dict[str, dict[str, object]] = {}
        self.schema_require_vectors: list[bool] = []
        self.search_channels: list[str] = []
        self.dense_requests: list[dict[str, object]] = []

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        normalized = dict(params or {})
        if path == "get":
            ids = str(normalized["ids"]).split(",")
            return {
                "response": {
                    "docs": [
                        copy.deepcopy(self.documents[item])
                        for item in ids
                        if item in self.documents
                    ]
                }
            }
        if path == "select" and normalized.get("q") != "*:*":
            self.search_channels.append("lexical")
            docs = self._search_documents()
            return {"response": {"numFound": len(docs), "docs": docs}}
        return super().get(path, normalized)

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        if path == "select":
            self.search_channels.append("dense")
            assert isinstance(payload, dict)
            self.dense_requests.append(payload)
            docs = self._search_documents()
            return {"response": {"numFound": len(docs), "docs": docs}}
        if isinstance(payload, dict) and "rollback" in payload:
            self.post_calls.append((path, copy.deepcopy(payload), dict(params or {})))
            self.documents = copy.deepcopy(self.committed)
            return {}
        result = super().post(path, payload, params)
        if isinstance(payload, dict) and "commit" in payload:
            self.committed = copy.deepcopy(self.documents)
        return result

    def _search_documents(self) -> list[dict[str, object]]:
        docs = [
            copy.deepcopy(item)
            for item in self.documents.values()
            if item.get("record_kind") == "knowledge"
        ]
        docs.sort(key=lambda item: str(item["id"]))
        for rank, item in enumerate(docs, start=1):
            item["score"] = 1.0 / rank
        return docs


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()


def _commit(root: Path, message: str) -> str:
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", message)
    return _git(root, "rev-parse", "HEAD")


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Vector Readiness Test")
    _git(root, "config", "user.email", "test@example.test")
    _git(root, "remote", "add", "origin", "https://example.test/acme/vector-ready.git")
    (root / "a.py").write_text("def first():\n    return 1\n", encoding="utf-8")
    (root / "b.py").write_text("def second():\n    return 2\n", encoding="utf-8")
    _commit(root, "initial")
    return root


def _retrieval(*, model: str = "fixture-model", hybrid: bool = False) -> RetrievalConfig:
    return RetrievalConfig.from_mapping({
        "dense": {
            "enabled": True,
            "embedding": {
                "provider": "fixture",
                "model": model,
                "dimensions": SOLR_VECTOR_DIMENSIONS,
            },
        },
        "hybrid": {"enabled": hybrid},
    })


def _providers(provider: CountingEmbeddingProvider) -> EmbeddingProviderRegistry:
    return EmbeddingProviderRegistry(
        factories={"fixture": lambda embedding, settings: provider}
    )


def _service(
    workspace: Path,
    tmp_path: Path,
    transport: VectorLifecycleTransport,
    *,
    retrieval: RetrievalConfig | None,
    provider: CountingEmbeddingProvider | None = None,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[indexing.RepositoryIndexApplicationService, str]:
    monkeypatch.setattr(indexing, "UrllibSolrTransport", lambda settings: transport)
    monkeypatch.setattr(
        SolrIndexStore,
        "validate_schema",
        lambda self, *, require_vectors=False: transport.schema_require_vectors.append(
            require_vectors
        ),
    )
    repositories = RepositoryApplicationService(
        cache_root=tmp_path / "cache",
        allowed_roots=(tmp_path,),
    )
    registered = repositories.add(str(workspace))
    service = indexing.RepositoryIndexApplicationService(
        repositories,
        retrieval=retrieval,
        providers=None if provider is None else _providers(provider),
    )
    return service, registered.repository


def test_lexical_composition_never_resolves_or_calls_embedding_provider(
    workspace: Path,
) -> None:
    class ForbiddenRegistry(EmbeddingProviderRegistry):
        def create(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("lexical indexing must not construct an embedding provider")

    context = indexing.build_index_context(
        workspace=workspace,
        repository="acme/vector-ready",
        branch="main",
        revision=_git(workspace, "rev-parse", "HEAD"),
        backend="solr",
        backend_settings={},
        retrieval=RetrievalConfig.from_mapping({"dense": {"enabled": False}}),
        providers=ForbiddenRegistry(),
    )
    observed: list[bool] = []
    context.store.validate_schema = (  # type: ignore[method-assign]
        lambda *, require_vectors=False: observed.append(require_vectors)
    )

    result = context.legacy("bootstrap")

    assert observed == [False]
    assert result["retrieval_mode"] == "lexical"
    assert result["vectors_required"] is False
    assert "embedding_probe" not in result


@pytest.mark.parametrize("hybrid", [False, True])
def test_vector_composition_without_registered_provider_fails_explicitly(
    workspace: Path,
    hybrid: bool,
) -> None:
    with pytest.raises(KnowledgeSearchError) as captured:
        indexing.build_index_context(
            workspace=workspace,
            repository="acme/vector-ready",
            branch="main",
            revision=_git(workspace, "rev-parse", "HEAD"),
            backend="solr",
            backend_settings={},
            retrieval=_retrieval(hybrid=hybrid),
            providers=EmbeddingProviderRegistry(factories={}),
        )

    assert captured.value.category == "unsupported_capability"


def test_vector_bootstrap_requires_vector_schema_and_accounts_for_probe(
    workspace: Path,
) -> None:
    provider = CountingEmbeddingProvider()
    context = indexing.build_index_context(
        workspace=workspace,
        repository="acme/vector-ready",
        branch="main",
        revision=_git(workspace, "rev-parse", "HEAD"),
        backend="solr",
        backend_settings={},
        retrieval=_retrieval(),
        providers=_providers(provider),
    )
    observed: list[bool] = []
    context.store.validate_schema = (  # type: ignore[method-assign]
        lambda *, require_vectors=False: observed.append(require_vectors)
    )

    result = context.legacy("bootstrap")

    assert observed == [True]
    assert provider.calls == [("fetchcore embedding probe",)]
    assert result["embedding_probe"] == {
        "requests": 1,
        "vectors": 1,
        "provider": "fixture",
        "model": "fixture-model",
        "dimensions": SOLR_VECTOR_DIMENSIONS,
        "serializer_mode": "projection-v3",
        "serializer_version": "2",
        "serializer_fingerprint": context.store.embedding_serializer.serializer_fingerprint,
        "projection_fingerprint": context.store.projection_fingerprint,
    }


def test_non_768_provider_fails_before_committing_vector_snapshot(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = VectorLifecycleTransport()
    provider = CountingEmbeddingProvider(dimensions=16)
    service, repository = _service(
        workspace,
        tmp_path,
        transport,
        retrieval=_retrieval(),
        provider=provider,
        monkeypatch=monkeypatch,
    )

    with pytest.raises(KnowledgeSearchError) as captured:
        service.run("rebuild", repository)

    assert captured.value.category == "schema_mismatch"
    assert not any(item.get("record_kind") == "snapshot" for item in transport.documents.values())
    assert not any(item.get("record_kind") == "knowledge" for item in transport.documents.values())


def test_vector_rebuild_incremental_and_noop_preserve_embedding_contract(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = VectorLifecycleTransport()
    provider = CountingEmbeddingProvider()
    service, repository = _service(
        workspace,
        tmp_path,
        transport,
        retrieval=_retrieval(),
        provider=provider,
        monkeypatch=monkeypatch,
    )

    rebuilt = service.run("rebuild", repository)
    execution = cast(dict[str, object], rebuilt["execution"])
    snapshot = cast(dict[str, object], execution["snapshot"])
    telemetry = cast(dict[str, object], execution["telemetry"])
    knowledge = [
        item for item in transport.documents.values() if item.get("record_kind") == "knowledge"
    ]
    assert knowledge and all(len(cast(list[float], item["embedding_vector"])) == 768 for item in knowledge)
    assert snapshot["embedding_provider"] == "fixture"
    assert snapshot["embedding_model"] == "fixture-model"
    assert snapshot["vector_dimensions"] == 768
    assert snapshot["corpus_checksum"]
    assert snapshot["projection_version"] == "3"
    assert snapshot["serializer_mode"] == "projection-v3"
    assert snapshot["serializer_version"] == "2"
    assert telemetry["embedding_vectors"] == len(knowledge)
    assert telemetry["backend_writes"] == len(knowledge)

    provider.calls.clear()
    transport.post_calls.clear()
    noop = service.run("sync", repository)
    noop_execution = cast(dict[str, object], noop["execution"])
    noop_telemetry = cast(dict[str, object], noop_execution["telemetry"])
    assert noop["mode"] == "noop"
    assert provider.calls == []
    assert transport.post_calls == []
    assert noop_telemetry["embedding_requests"] == 0
    assert noop_telemetry["embedding_vectors"] == 0
    assert noop_telemetry["backend_writes"] == 0

    (workspace / "a.py").write_text("def first():\n    return 42\n", encoding="utf-8")
    _commit(workspace, "change one file")
    provider.calls.clear()
    changed = service.run("sync", repository)
    changed_execution = cast(dict[str, object], changed["execution"])
    changed_telemetry = cast(dict[str, object], changed_execution["telemetry"])
    embedded = sum(len(batch) for batch in provider.calls)
    assert embedded == changed_execution["added"] + changed_execution["updated"]
    assert embedded < len(knowledge)
    assert changed_telemetry["embedding_vectors"] == embedded
    assert changed_telemetry["backend_writes"] == embedded

    (workspace / "b.py").unlink()
    _commit(workspace, "delete one file")
    provider.calls.clear()
    deleted = service.run("sync", repository)
    deleted_execution = cast(dict[str, object], deleted["execution"])
    assert deleted_execution["deleted"] == 1
    assert provider.calls == []


def test_vector_snapshot_identity_changes_require_explicit_rebuild(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = VectorLifecycleTransport()
    original = CountingEmbeddingProvider(model="model-a")
    service, repository = _service(
        workspace,
        tmp_path,
        transport,
        retrieval=_retrieval(model="model-a"),
        provider=original,
        monkeypatch=monkeypatch,
    )
    service.run("rebuild", repository)

    changed = CountingEmbeddingProvider(model="model-b")
    changed_service = indexing.RepositoryIndexApplicationService(
        service.repositories,
        retrieval=_retrieval(model="model-b"),
        providers=_providers(changed),
    )
    assert changed_service.run("status", repository)["reason"] == "embedding_identity_changed"
    with pytest.raises(KnowledgeSearchError) as captured:
        changed_service.run("sync", repository)
    assert captured.value.category == "rebuild_required"

    snapshot = next(
        item for item in transport.documents.values() if item.get("record_kind") == "snapshot"
    )
    snapshot["embedding_serializer_fingerprint"] = "sha256:changed"
    transport.committed = copy.deepcopy(transport.documents)
    assert service.run("plan", repository)["reason"] == "embedding_identity_changed"


def test_lexical_snapshot_requires_rebuild_when_vector_mode_is_enabled(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = VectorLifecycleTransport()
    lexical, repository = _service(
        workspace,
        tmp_path,
        transport,
        retrieval=None,
        monkeypatch=monkeypatch,
    )
    lexical.run("rebuild", repository)
    assert lexical.run("status", repository)["currentness"] == "current"

    provider = CountingEmbeddingProvider()
    vector = indexing.RepositoryIndexApplicationService(
        lexical.repositories,
        retrieval=_retrieval(),
        providers=_providers(provider),
    )
    status = vector.run("status", repository)
    assert status["mode"] == "rebuild_required"
    assert status["reason"] == "embedding_identity_changed"
    assert provider.calls == []


def test_dense_and_hybrid_serving_use_query_embedding_without_fallback(
    workspace: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = VectorLifecycleTransport()
    provider = CountingEmbeddingProvider()
    service, repository = _service(
        workspace,
        tmp_path,
        transport,
        retrieval=_retrieval(),
        provider=provider,
        monkeypatch=monkeypatch,
    )
    service.run("rebuild", repository)
    provider.calls.clear()

    backend = SolrBackend(
        transport_factory=lambda settings: transport,
        embedding_factory=lambda retrieval: provider,
    )
    for hybrid in (False, True):
        transport.search_channels.clear()
        response = backend.search(
            SearchIntent(canonical_query="first function"),
            SearchScope(
                workspace=workspace,
                repository=repository,
                revision=_git(workspace, "rev-parse", "HEAD"),
                knowledge_types=(),
                max_results=5,
                settings={"diagnostic_candidate_window": 80, "ann_search_breadth": 160},
                retrieval=_retrieval(hybrid=hybrid),
            ),
            (),
        )
        assert response.results
        assert response.telemetry is not None
        assert response.telemetry.mode == ("hybrid" if hybrid else "dense")
        assert provider.calls[-1] == ("first function",)
        assert "dense" in transport.search_channels
        assert "topK=160" in str(transport.dense_requests[-1]["query"])
        assert transport.dense_requests[-1]["limit"] == 80
        assert response.metadata["ann_top_k_requested"] == 160
        assert response.metadata["dense_candidate_window"] == 80
        assert response.telemetry.metadata["dense_candidates_acquired"] == response.metadata[
            "dense_candidates_acquired"
        ]
        assert isinstance(response.metadata["dense_candidates_acquired"], int)
        assert response.metadata["dense_candidates_acquired"] <= 80
        assert len(response.telemetry.metadata["query_vector_sha256"]) == 64
        assert response.metadata["query_vector_sha256"] == response.telemetry.metadata[
            "query_vector_sha256"
        ]
        assert ("lexical" in transport.search_channels) is hybrid
        assert all(result.revision == _git(workspace, "rev-parse", "HEAD") for result in response.results)
        assert all(result.source_ref.startswith(f"repository://{repository}/") for result in response.results)
        if hybrid:
            assert response.metadata["fusion_strategy"] == "rrf"
            assert response.metadata["lexical_fallback"] is False


def test_operational_hybrid_revision_rebinding_preserves_search_and_source_provenance(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = VectorLifecycleTransport()
    provider = CountingEmbeddingProvider()
    service, repository = _service(
        workspace, tmp_path, transport, retrieval=_retrieval(hybrid=True),
        provider=provider, monkeypatch=monkeypatch,
    )
    revision_a = _git(workspace, "rev-parse", "HEAD")
    service.run("rebuild", repository)
    (workspace / "a.py").write_text("def first():\n    return 3\n", encoding="utf-8")
    revision_b = _commit(workspace, "update first")
    service.run("sync", repository)
    backend = SolrBackend(
        transport_factory=lambda settings: transport,
        embedding_factory=lambda retrieval: provider,
    )
    registry = BackendRegistry({"solr": lambda: backend})

    def serving(revision: str) -> RetrievalApplicationService:
        binding = RepositoryBinding(
            repository, workspace, "solr", revision=revision,
            retrieval=_retrieval(hybrid=True),
        )
        return RetrievalApplicationService({repository: binding}, backends=registry)

    with pytest.raises(KnowledgeSearchError) as mismatch:
        serving(revision_a).search(SearchRequest(query="first function"))
    assert mismatch.value.category == "snapshot_revision_mismatch"

    rebound = serving(revision_b)
    response = rebound.search(SearchRequest(query="first function"))
    assert response.results
    assert response.telemetry.mode == "hybrid"
    assert all(hit.revision == revision_b for hit in response.results)
    source = rebound.get_source(SourceReadRequest(
        source_ref=f"repository://{repository}/a.py", revision=revision_b,
        line_start=1, line_end=2,
    ))
    assert source.revision == revision_b
    assert "return 3" in source.content
    with pytest.raises(KnowledgeSearchError) as stale_source:
        rebound.get_source(SourceReadRequest(
            source_ref=f"repository://{repository}/a.py", revision=revision_a,
        ))
    assert stale_source.value.category == "source_revision_mismatch"


def test_provider_failure_never_downgrades_dense_or_hybrid_to_lexical(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = VectorLifecycleTransport()
    provider = CountingEmbeddingProvider()
    context = indexing.build_index_context(
        workspace=workspace,
        repository="acme/vector-ready",
        branch="main",
        revision=_git(workspace, "rev-parse", "HEAD"),
        backend="solr",
        backend_settings={},
        retrieval=_retrieval(),
        providers=_providers(provider),
    )
    result = context.runtime.indexer.index(workspace)
    context.store.transport = transport
    monkeypatch.setattr(
        context.store,
        "validate_schema",
        lambda *, require_vectors=False: transport.schema_require_vectors.append(
            require_vectors
        ),
    )
    context.store.full_rebuild(result)

    failing = CountingEmbeddingProvider(
        failure=KnowledgeSearchError("provider failed", category="embedding_failed")
    )
    backend = SolrBackend(
        transport_factory=lambda settings: transport,
        embedding_factory=lambda retrieval: failing,
    )
    for hybrid in (False, True):
        transport.search_channels.clear()
        with pytest.raises(KnowledgeSearchError) as captured:
            backend.search(
                SearchIntent(canonical_query="first function"),
                SearchScope(
                    workspace=workspace,
                    repository="acme/vector-ready",
                    revision=_git(workspace, "rev-parse", "HEAD"),
                    knowledge_types=(),
                    max_results=5,
                    settings={},
                    retrieval=_retrieval(hybrid=hybrid),
                ),
                (),
            )
        assert captured.value.category == "embedding_failed"
        assert transport.search_channels == []


def test_hybrid_windows_capture_and_legacy_equivalence(workspace, tmp_path, monkeypatch):
    from dataclasses import replace
    from fetchcore.retrieval.fusion import FusionInput, FusionKernel, FusionRequest
    from fetchcore.retrieval.hybrid import allocate_fusion_input
    transport = VectorLifecycleTransport()
    provider = CountingEmbeddingProvider()
    service, repository = _service(workspace, tmp_path, transport, retrieval=_retrieval(),
                                   provider=provider, monkeypatch=monkeypatch)
    service.run('rebuild', repository)
    backend = SolrBackend(transport_factory=lambda _: transport, embedding_factory=lambda _: provider)
    scope = SearchScope(workspace=workspace, repository=repository, revision=_git(workspace,'rev-parse','HEAD'),
                        knowledge_types=(), max_results=1, settings={'diagnostic_candidate_window':80},
                        retrieval=_retrieval(hybrid=True))
    intent = SearchIntent(canonical_query='function')
    control = backend.search(intent,scope,())
    captures = [];backend.fusion_observer = captures.append
    observed = backend.search(intent,scope,())
    assert [r.to_mapping() for r in control.results] == [r.to_mapping() for r in observed.results]
    assert control.metadata['fusion_output_fingerprint'] == observed.metadata['fusion_output_fingerprint']
    capture = captures[-1]
    assert len(capture.evidence) > 1
    assert all(r.checksum and r.revision == scope.revision and r.line_start for r in capture.evidence)
    wider = replace(scope,retrieval=replace(scope.retrieval,hybrid=replace(scope.retrieval.hybrid,
                               lexical_fusion_window=80,dense_fusion_window=80,rrf_k=10)))
    response = backend.search(intent,wider,())
    assert len(response.results) == 1
    full = capture.input
    admitted = allocate_fusion_input(lexical=tuple(sorted((c for c in full.candidates if c.lexical),key=lambda c:c.lexical.rank)),
              dense=tuple(sorted((c for c in full.candidates if c.dense),key=lambda c:c.dense.rank)),
              lexical_window=80,dense_window=80,max_results=1)
    assert FusionKernel().fuse(FusionRequest(admitted,parameters={'k':10})).output_fingerprint == response.metadata['fusion_output_fingerprint']
    assert len(FusionInput.from_mapping(response.metadata['fusion_input']).candidates) > 1
