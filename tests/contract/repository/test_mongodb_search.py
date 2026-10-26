"""MongoDB adapter contracts using an injectable transport, not a search emulator."""

from __future__ import annotations

import copy
import json
import subprocess
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from fetchcore.adapters.retrieval.mongodb.backend import MongoDBSearchBackend
from fetchcore.adapters.retrieval.mongodb.indexing import (
    MongoDBIndexStore, MongoDBOperationalIndex,
)
from fetchcore.adapters.retrieval.mongodb.query import (
    index_definitions, lexical_pipeline, scope_filter, vector_pipeline,
)
from fetchcore.adapters.retrieval.mongodb.settings import MongoDBSettings
from fetchcore.application.composition import BackendRegistry, EmbeddingProviderRegistry
from fetchcore.application.indexing import build_index_context
from fetchcore.application.runtime_config import load_runtime_config
from fetchcore.application.runtime_inspection import RuntimeInspectionService
from fetchcore.projection import ProjectionV3Compiler
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import (
    AllOf, Anchor, AnyOf, KnowledgeSearchError, SearchIntent, SearchScope, Term,
)


class MemoryMongo:
    def __init__(self, settings: MongoDBSettings, *, vectors: bool = True) -> None:
        self.records: dict[str, dict[str, object]] = {}
        self.definitions = {
            item["name"]: {
                **item, "latestDefinition": item["definition"], "status": "READY", "queryable": True,
            } for item in index_definitions(settings, vectors=vectors)
        }
        self.pipelines: list[list[dict[str, object]]] = []
        self.hits: dict[str, tuple[dict[str, object], ...]] = {"lexical": (), "dense": ()}
        self.writes: list[dict[str, object]] = []
        self.fail_write = False
        self.fail_channel: str | None = None
        self.closed = False
        self.search_snapshot_change = False

    def health(self) -> None:
        pass

    def find(self, query: Mapping[str, object]) -> tuple[dict[str, object], ...]:
        return tuple(copy.deepcopy(item) for item in self.records.values() if all(
            item.get(key) == value for key, value in query.items()
        ))

    def aggregate(self, pipeline: list[dict[str, object]]) -> tuple[dict[str, object], ...]:
        self.pipelines.append(pipeline)
        channel = "dense" if "$vectorSearch" in pipeline[0] else "lexical"
        if channel == self.fail_channel:
            raise KnowledgeSearchError("fixture unavailable", category="unavailable")
        if self.search_snapshot_change:
            for item in self.records.values():
                if item.get("record_kind") == "snapshot":
                    item["token"] = "changed-concurrently"
        return copy.deepcopy(self.hits[channel])

    def replace(self, record: dict[str, object], expected: Mapping[str, object] | None) -> bool:
        key = str(record["_id"])
        previous = self.records.get(key)
        if expected is not None:
            if expected.get("_absent"):
                if previous is not None:
                    raise KnowledgeSearchError("duplicate", category="concurrent_write")
            elif previous is None or any(previous.get(name) != value for name, value in expected.items()):
                return False
        if self.fail_write and record.get("record_kind") == "knowledge":
            raise KnowledgeSearchError("fixture write failed", category="unavailable")
        self.records[key] = copy.deepcopy(record)
        self.writes.append(copy.deepcopy(record))
        return True

    def delete(self, query: Mapping[str, object]) -> int:
        keys = [key for key, item in self.records.items() if all(
            item.get(name) == value for name, value in query.items()
        )]
        for key in keys:
            del self.records[key]
        return len(keys)

    def indexes(self) -> tuple[dict[str, object], ...]:
        return tuple(copy.deepcopy(item) for item in self.definitions.values())

    def create_index(self, definition: dict[str, object]) -> None:
        self.definitions[definition["name"]] = {
            **definition, "latestDefinition": definition["definition"],
            "status": "BUILDING", "queryable": False,
        }

    def close(self) -> None:
        self.closed = True


class FixtureEmbeddings:
    provider_id = "fixture"
    model_id = "fixture-v1"

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.dimensions = 3

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(texts)
        return tuple(tuple(1.0 for _ in range(self.dimensions)) for _ in texts)


def retrieval(mode: str) -> RetrievalConfig:
    return RetrievalConfig.from_mapping({
        "dense": {"enabled": mode in {"dense", "hybrid"}, "embedding": {
            "provider": "fixture", "model": "fixture-v1", "dimensions": 3,
        }},
        "hybrid": {"enabled": mode == "hybrid"},
        "targeted": "disabled",
    })


@pytest.fixture
def indexed(tmp_path: Path) -> tuple[MongoDBIndexStore, MemoryMongo, RepositoryIndexer, FixtureEmbeddings]:
    (tmp_path / "service.py").write_text("def acquire_evidence():\n    return 'traceable'\n")
    (tmp_path / "README.md").write_text("# Evidence\n\nAcquire traceable repository knowledge.\n")
    settings = MongoDBSettings(vector_dimensions=3)
    memory = MemoryMongo(settings)
    provider = FixtureEmbeddings()
    store = MongoDBIndexStore(memory, settings, embedding_provider=provider)
    indexer = RepositoryIndexer(RepositoryIndexConfig(repository="owner/repo", revision="rev-1"))
    result = indexer.index(tmp_path)
    store.full_rebuild(result)
    hits = tuple({
        **ProjectionV3Compiler().compile(document).to_mapping(), "score": 1.0,
    } for document in result.documents)
    memory.hits.update(lexical=hits, dense=hits)
    return store, memory, indexer, provider


@pytest.mark.parametrize("data", [
    {"uri": "https://example.com"}, {"uri": "mongodb://"}, {"uri": "mongodb://bad host"},
    {"database": "bad.name"}, {"collection": "system.private"},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": True},
    {"batch_size": 0}, {"batch_size": "3"}, {"vector_dimensions": 0},
    {"vector_dimensions": True}, {"search_index": "same", "vector_index": "same"},
    {"unknown": 1},
])
def test_invalid_settings_fail_closed(data: dict[str, object]) -> None:
    with pytest.raises(KnowledgeSearchError):
        MongoDBSettings.from_mapping(data)


def test_runtime_registry_environment_and_secret_redaction(tmp_path: Path) -> None:
    config = tmp_path / "operator.yaml"
    config.write_text(
        "version: 1\nruntime:\n  backend: mongodb-search\nbackends:\n"
        "  mongodb-search:\n    database: file_database\n    vector_dimensions: 3\n"
    )
    runtime = load_runtime_config(path=config, environ={
        "FETCHCORE_MONGODB_URI": "mongodb+srv://user:private@cluster.example/",
        "FETCHCORE_MONGODB_DATABASE": "env_database",
    }, overrides={"backends": {"mongodb-search": {"collection": "explicit_collection"}}})
    assert runtime.backend_settings["database"] == "env_database"
    assert runtime.backend_settings["collection"] == "explicit_collection"
    assert isinstance(BackendRegistry().create(runtime.backend), MongoDBSearchBackend)
    rendered = json.dumps(RuntimeInspectionService(runtime).resolved_config())
    assert "private" not in rendered
    assert "mongodb+srv" not in rendered
    assert "********" in rendered
    assert "config_file" in rendered and "environment" in rendered and "explicit" in rendered


def test_indexing_composition_uses_neutral_embedding_port(
    indexed: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, memory, _, provider = indexed
    from fetchcore.application import indexing
    monkeypatch.setattr(indexing, "PyMongoTransport", lambda settings: memory)
    providers = EmbeddingProviderRegistry({"fixture": lambda config, settings: provider})
    context = build_index_context(
        workspace=tmp_path, repository="owner/repo", branch=None, revision="rev-1",
        backend="mongodb-search", backend_settings={"vector_dimensions": 3},
        retrieval=retrieval("hybrid"), providers=providers,
    )
    assert isinstance(context.store, MongoDBIndexStore)
    assert isinstance(context.driver, MongoDBOperationalIndex)
    assert context.store.embedding_provider is provider
    assert store.embedding_serializer.serializer_fingerprint == context.store.embedding_serializer.serializer_fingerprint


def test_incremental_noop_updates_deletes_and_repository_isolation(indexed: tuple, tmp_path: Path) -> None:
    store, memory, indexer, provider = indexed
    before = store.load_documents("owner/repo")
    calls, writes = len(provider.calls), len(memory.writes)
    noop = store.incremental_sync(indexer.index(tmp_path, previous_documents=before))
    assert noop.mode == "noop"
    assert len(provider.calls) == calls and len(memory.writes) == writes
    (tmp_path / "service.py").write_text("def acquire_evidence():\n    return 'changed'\n")
    (tmp_path / "README.md").unlink()
    memory.records["other"] = {"_id": "other", "repository": "other/repo", "record_kind": "knowledge"}
    sync = store.incremental_sync(indexer.index(tmp_path, previous_documents=before))
    assert sync.updated == 1 and sync.deleted == 1 and sync.unchanged == 0
    assert sync.telemetry.embedding_vectors == 1
    assert "other" in memory.records
    assert len(store.load_documents("owner/repo")) == 1


def test_metadata_change_reprojects_without_reembedding_unchanged(indexed: tuple, tmp_path: Path) -> None:
    store, _, indexer, provider = indexed
    previous = store.load_documents("owner/repo")
    updated = indexer.index(tmp_path, previous_documents=previous)
    doc = replace(updated.documents[0], technical_role="adapter")
    updated = replace(updated, documents=(doc, *updated.documents[1:]))
    calls = len(provider.calls)
    result = store.incremental_sync(updated)
    assert result.updated == 1
    assert len(provider.calls) == calls + 1
    assert result.telemetry.embedding_vectors == 1


@pytest.mark.parametrize("mode", ["lexical", "dense", "hybrid"])
def test_modes_provenance_scope_telemetry_and_fusion(indexed: tuple, tmp_path: Path, mode: str) -> None:
    store, memory, _, provider = indexed
    backend = MongoDBSearchBackend(
        transport_factory=lambda settings: memory, embedding_factory=lambda config: provider,
    )
    scope = SearchScope(
        tmp_path, "rev-1", "owner/repo", (), 1, settings={"vector_dimensions": 3},
        retrieval=retrieval(mode),
    )
    response = backend.search(SearchIntent(canonical_query="traceable evidence"), scope, ())
    assert response.status == "ok" and len(response.results) == 1
    hit = response.results[0]
    assert hit.revision == "rev-1" and hit.checksum and hit.source_ref
    assert hit.metadata["repository"] == "owner/repo"
    assert hit.metadata["projection_fingerprint"] == store.projection_fingerprint
    assert response.metadata["output_truncated"] is True
    assert response.metadata["search_consistency"] == "eventual"
    assert response.telemetry and response.telemetry.candidates_returned == 1
    assert memory.closed
    if mode == "lexical":
        assert all("$search" in pipeline[0] for pipeline in memory.pipelines)
        assert len(provider.calls) == 1  # Indexing only, no lexical query embedding.
    elif mode == "dense":
        assert all("$vectorSearch" in pipeline[0] for pipeline in memory.pipelines)
    else:
        assert hit.metadata["acquisition_channels"] == ["lexical", "dense"]
        assert response.metadata["fusion"]["strategy"] == "rrf"


def test_query_preserves_boolean_anchors_and_optional_hints(tmp_path: Path) -> None:
    settings = MongoDBSettings()
    scope = SearchScope(tmp_path, None, "owner/repo", ("code",), 2, repository_paths=("src/A.py",))
    filters = scope_filter(scope, "owner/repo", settings)
    intent = SearchIntent(
        canonical_query="acquire", hints=("optional",),
        anchors=(Anchor("identifier", "Acquire"),),
        expression=AllOf((Term("evidence"), AnyOf((Term("typed"), Term("traceable"))))),
    )
    pipeline = lexical_pipeline(intent, filters, settings, 4)
    compound = pipeline[0]["$search"]["compound"]
    assert compound["minimumShouldMatch"] == 0
    required = compound["must"][0]["compound"]
    rendered = json.dumps(required)
    assert "repository" in rendered and "owner/repo" in rendered
    assert "src/A.py" in rendered and "knowledge_type" in rendered
    assert "optional" not in rendered
    assert "must" in rendered and "minimumShouldMatch" in rendered
    assert "acquire" in rendered
    dense = vector_pipeline((1.0, 0.0, 0.0), filters, settings, 4)[0]["$vectorSearch"]
    assert dense["filter"] == filters and dense["numCandidates"] == 80


@pytest.mark.parametrize("mode", ["dense", "hybrid"])
def test_required_dense_semantics_rejected_before_io(tmp_path: Path, mode: str) -> None:
    def unexpected(settings: MongoDBSettings) -> MemoryMongo:
        pytest.fail("Unsupported semantics must fail before I/O")
    backend = MongoDBSearchBackend(transport_factory=unexpected)
    with pytest.raises(KnowledgeSearchError, match="cannot preserve"):
        backend.search(
            SearchIntent(anchors=(Anchor("path", "src/A.py"),)),
            SearchScope(tmp_path, None, "owner/repo", (), 1, retrieval=retrieval(mode)), (),
        )


def test_async_indexes_and_schema_mismatch_are_explicit() -> None:
    settings = MongoDBSettings()
    memory = MemoryMongo(settings, vectors=False)
    memory.definitions.clear()
    store = MongoDBIndexStore(memory, settings)
    assert store.initialize()["status"] == "building"
    with pytest.raises(KnowledgeSearchError) as error:
        store.validate_schema()
    assert error.value.category == "index_not_ready"
    memory.definitions[settings.search_index]["latestDefinition"] = {}
    with pytest.raises(KnowledgeSearchError) as error:
        store.validate_schema()
    assert error.value.category == "schema_mismatch"


def test_failed_write_leaves_unservable_snapshot_and_rebuild_recovers(indexed: tuple, tmp_path: Path) -> None:
    store, memory, indexer, _ = indexed
    (tmp_path / "service.py").write_text("def changed():\n    pass\n")
    memory.fail_write = True
    with pytest.raises(KnowledgeSearchError):
        store.incremental_sync(indexer.index(tmp_path))
    with pytest.raises(KnowledgeSearchError) as error:
        store.load_snapshot("owner/repo")
    assert error.value.category == "index_not_ready"
    assert error.value.metadata["rebuild_required"] is True
    memory.fail_write = False
    store.full_rebuild(indexer.index(tmp_path))
    assert store.load_snapshot("owner/repo").status == "ready"


@pytest.mark.parametrize("failure", ["revision", "projection", "dimensions", "channel", "snapshot", "score", "repository"])
def test_failures_never_downgrade_or_return_success(indexed: tuple, tmp_path: Path, failure: str) -> None:
    store, memory, _, provider = indexed
    revision = "rev-1"
    if failure == "revision":
        revision = "wrong"
    elif failure == "projection":
        snapshot_key = store.raw_snapshot("owner/repo")["_id"]
        memory.records[snapshot_key]["projection_fingerprint"] = "wrong"
    elif failure == "dimensions":
        provider.dimensions = 2
    elif failure == "channel":
        memory.fail_channel = "dense"
    elif failure == "snapshot":
        memory.search_snapshot_change = True
    elif failure in {"score", "repository"}:
        memory.hits["dense"] = ({**memory.hits["dense"][0], failure: float("nan") if failure == "score" else "other"},)
    backend = MongoDBSearchBackend(
        transport_factory=lambda settings: memory, embedding_factory=lambda config: provider,
    )
    with pytest.raises(KnowledgeSearchError):
        backend.search(
            SearchIntent(canonical_query="traceable"),
            SearchScope(tmp_path, revision, "owner/repo", (), 2,
                        settings={"vector_dimensions": 3}, retrieval=retrieval("hybrid")), (),
        )
    assert memory.closed


def test_exact_lookup_uses_lexical_without_embeddings(indexed: tuple, tmp_path: Path) -> None:
    _, memory, _, _ = indexed
    response = MongoDBSearchBackend(transport_factory=lambda settings: memory).lookup_exact(
        Anchor("identifier", "acquire_evidence"),
        SearchScope(tmp_path, "rev-1", "owner/repo", (), 10, settings={"vector_dimensions": 3}),
    )
    assert response.metadata["acquisition"] == "targeted"
    assert len(response.results) <= 2
    assert all("$search" in pipeline[0] for pipeline in memory.pipelines)


def test_canonical_metadata_update_reuses_compatible_vector(indexed: tuple, tmp_path: Path) -> None:
    store, _, indexer, provider = indexed
    result = indexer.index(tmp_path)
    updated = replace(result.documents[0], metadata={**result.documents[0].metadata, "new": "evidence"})
    result = replace(result, documents=(updated, *result.documents[1:]))
    calls = len(provider.calls)
    sync = store.incremental_sync(result)
    assert sync.updated == 1 and sync.telemetry.embedding_vectors == 0
    assert len(provider.calls) == calls
    assert store.load_documents("owner/repo")[0].metadata.get("new") == "evidence"


def test_incomplete_scan_cannot_delete_persisted_evidence(indexed: tuple, tmp_path: Path) -> None:
    store, memory, indexer, _ = indexed
    result = indexer.index(tmp_path)
    incomplete = replace(result, documents=(), telemetry=replace(
        result.telemetry, removal_detection_complete=False,
    ))
    writes = len(memory.writes)
    with pytest.raises(KnowledgeSearchError):
        store.incremental_sync(incomplete)
    assert len(memory.writes) == writes
    assert store.load_documents("owner/repo")


@pytest.mark.parametrize("vectors", [((float("nan"), 0.0, 1.0),), ((0.0, 0.0, 0.0),), ((True, 1.0, 0.0),)])
def test_invalid_cosine_vectors_fail_closed(vectors: tuple) -> None:
    from fetchcore.adapters.retrieval.mongodb.query import validate_vectors
    with pytest.raises(KnowledgeSearchError):
        validate_vectors(vectors, 3)


def test_driver_uses_git_delta_and_only_embeds_changed_content(
    indexed: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, indexer, _ = indexed
    from fetchcore.application.index_operations import prepare_repository_index_runtime
    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True, capture_output=True, text=True,
        ).stdout.strip()
    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.com")
    git("add", ".")
    git("commit", "-m", "initial")
    first = git("rev-parse", "HEAD")
    initial_indexer = RepositoryIndexer(replace(indexer.config, revision=first))
    (tmp_path / "service.py").write_text("def acquire_evidence():\n    return 'changed'\n")
    git("add", ".")
    git("commit", "-m", "change")
    second = git("rev-parse", "HEAD")
    runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/repo", branch=None, revision=second,
    )
    store.indexing_fingerprint = runtime.fingerprint
    (tmp_path / "service.py").write_text("def acquire_evidence():\n    return 'traceable'\n")
    store.full_rebuild(initial_indexer.index(tmp_path))
    (tmp_path / "service.py").write_text("def acquire_evidence():\n    return 'changed'\n")
    driver = MongoDBOperationalIndex(store, runtime)
    def no_full_scan(*args: object, **kwargs: object) -> None:
        pytest.fail("Git delta must not fall back to a full index scan")
    monkeypatch.setattr(runtime.indexer, "index", no_full_scan)
    assert driver.run("status")["indexed_revision"] == first
    execution = driver.run("sync")
    assert execution["mode"] == "incremental"
    assert execution["updated"] == 1 and execution["unchanged"] == 1
    assert execution["telemetry"]["embedding_vectors"] == 1
    assert driver.run("status")["currentness"] == "current"


def test_missing_optional_driver_reports_installation_action(monkeypatch: pytest.MonkeyPatch) -> None:
    from fetchcore.adapters.retrieval.mongodb import transport
    def missing(name: str) -> None:
        raise ModuleNotFoundError("missing optional driver", name="pymongo")
    monkeypatch.setattr(transport.importlib, "import_module", missing)
    with pytest.raises(KnowledgeSearchError, match=r"fetchcore\[mongodb\]") as captured:
        transport.PyMongoTransport(MongoDBSettings())
    assert captured.value.category == "unsupported_capability"


def test_doctor_and_readiness_are_read_only(indexed: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, memory, indexer, provider = indexed
    from fetchcore.adapters.retrieval.mongodb import transport
    from fetchcore.application import runtime_inspection
    from fetchcore.application.index_operations import prepare_repository_index_runtime
    runtime = prepare_repository_index_runtime(
        tmp_path, repository="owner/repo", branch=None, revision="rev-1",
    )
    store.indexing_fingerprint = runtime.fingerprint
    store.full_rebuild(indexer.index(tmp_path))
    config = load_runtime_config(environ={}, overrides={
        "runtime": {"backend": "mongodb-search", "workspace": str(tmp_path),
                    "repository": "owner/repo", "revision": "rev-1"},
        "backends": {"mongodb-search": {"vector_dimensions": 3}},
        "retrieval": retrieval("hybrid").to_mapping(),
    })
    monkeypatch.setattr(transport, "PyMongoTransport", lambda settings: memory)
    monkeypatch.setattr(runtime_inspection, "PyMongoTransport", lambda settings: memory)
    inspector = RuntimeInspectionService(
        config, providers=EmbeddingProviderRegistry({"fixture": lambda config, settings: provider}),
    )
    writes = len(memory.writes)
    assert inspector.doctor().healthy
    report = inspector.readiness()
    assert report["checks"]["snapshot"]["state"] == "ready"
    assert report["checks"]["embedding"]["state"] == "ready"
    assert report["observations"]["search_consistency"] == "eventual"
    assert len(memory.writes) == writes
