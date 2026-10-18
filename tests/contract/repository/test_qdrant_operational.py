from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
import subprocess
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import unquote

import pytest

from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorBackend,
    QdrantVectorIndexStore,
    QdrantVectorKnowledgeIndex,
    QdrantVectorSettings,
)
from fetchcore.application import indexing as indexing_application
from fetchcore.application.composition import EmbeddingProviderRegistry
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.adapters.retrieval.qdrant.operational import QdrantOperationalIndex
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexConfig,
    RepositoryIndexer,
    RepositoryIndexResult,
)
from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent, SearchScope


_REPOSITORY = "acme/widgets"


class SharedLock(Protocol):
    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool: ...

    def release(self) -> None: ...


class SharedState(Protocol):
    def __contains__(self, key: object) -> bool: ...

    def __getitem__(self, key: str) -> str: ...

    def __setitem__(self, key: str, value: str) -> None: ...

    def __delitem__(self, key: str) -> None: ...


class OutcomeQueue(Protocol):
    def put(self, value: str) -> None: ...


class MemoryQdrant:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, object]] = {}
        self.aliases: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []
        self.fail_partial_knowledge_upsert = False
        self.fail_partial_delete = False
        self.fail_alias_after_apply = False
        self.flip_alias_after_snapshot_read: tuple[str, str] | None = None
        self.query_collections: list[str] = []
        self._collection_create_lock = threading.Lock()

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        if method in {"PUT", "DELETE"} or (
            method == "POST" and path == "collections/aliases"
        ):
            self.writes.append((method, path))

        if method == "GET" and path == "collections":
            return {
                "result": {
                    "collections": [
                        {"name": name} for name in sorted(self.collections)
                    ]
                }
            }
        if method == "GET" and path == "aliases":
            return {
                "result": {
                    "aliases": [
                        {"alias_name": alias, "collection_name": collection}
                        for alias, collection in sorted(self.aliases.items())
                    ]
                }
            }
        if method == "POST" and path == "collections/aliases":
            assert isinstance(payload, dict)
            for action in payload["actions"]:
                if "delete_alias" in action:
                    self.aliases.pop(action["delete_alias"]["alias_name"], None)
                else:
                    item = action["create_alias"]
                    self.aliases[item["alias_name"]] = item["collection_name"]
            if self.fail_alias_after_apply:
                self.fail_alias_after_apply = False
                raise KnowledgeSearchError(
                    "alias response lost after update",
                    category="unavailable",
                )
            return {"result": True}

        parts = path.split("/")
        if len(parts) >= 2 and parts[0] == "collections":
            name = unquote(parts[1].split("?", 1)[0])
            collection = self.aliases.get(name, name)
            if method == "GET" and len(parts) == 2:
                item = self.collections.get(collection)
                if item is None:
                    raise KnowledgeSearchError("collection missing", category="index_missing")
                return {
                    "result": {
                        "config": {
                            "params": {
                                "vectors": copy.deepcopy(item["vectors"]),
                                "sparse_vectors": copy.deepcopy(item["sparse_vectors"]),
                            }
                        }
                    }
                }
            if method == "PUT" and len(parts) == 2:
                assert isinstance(payload, dict)
                with self._collection_create_lock:
                    if name in self.collections:
                        raise KnowledgeSearchError("collection exists", category="schema_mismatch")
                    self.collections[name] = {
                        "vectors": copy.deepcopy(payload["vectors"]),
                        "sparse_vectors": copy.deepcopy(payload.get("sparse_vectors", {})),
                        "points": {},
                    }
                return {"result": True}
            if method == "DELETE" and len(parts) == 2:
                self.collections.pop(name, None)
                return {"result": True}
            item = self.collections.get(collection)
            if item is None:
                raise KnowledgeSearchError("collection missing", category="index_missing")
            points = cast(dict[str, dict[str, object]], item["points"])
            suffix = "/".join(parts[2:])

            if method == "POST" and suffix == "points/count":
                assert isinstance(payload, dict)
                return {"result": {"count": len(self._filtered(points, payload))}}
            if method == "POST" and suffix == "points/scroll":
                assert isinstance(payload, dict)
                rows = self._filtered(points, payload)
                offset = payload.get("offset")
                if offset is not None:
                    rows = [row for row in rows if str(row["id"]) > str(offset)]
                limit = int(payload["limit"])
                page = rows[:limit]
                result_points = [
                    self._render_point(row, with_vector=payload.get("with_vector") is True)
                    for row in page
                ]
                next_offset = page[-1]["id"] if len(rows) > limit else None
                return {
                    "result": {
                        "points": result_points,
                        "next_page_offset": next_offset,
                    }
                }
            if method == "POST" and suffix == "points":
                assert isinstance(payload, dict)
                identifiers = {str(value) for value in payload["ids"]}
                selected = [points[key] for key in sorted(identifiers) if key in points]
                response: dict[str, object] = {
                    "result": [
                        self._render_point(
                            row,
                            with_vector=payload.get("with_vector") is True,
                        )
                        for row in selected
                    ]
                }
                flip = self.flip_alias_after_snapshot_read
                if flip is not None and any(
                    cast(dict[str, object], row["payload"]).get("record_kind") == "snapshot"
                    for row in selected
                ):
                    alias, target = flip
                    self.aliases[alias] = target
                    self.flip_alias_after_snapshot_read = None
                return response
            if method == "POST" and suffix == "points/query":
                assert isinstance(payload, dict)
                self.query_collections.append(collection)
                rows = self._filtered(points, payload)[: int(payload["limit"])]
                return {
                    "result": {
                        "points": [
                            {
                                "id": point["id"],
                                "payload": copy.deepcopy(point["payload"]),
                                "score": 0.75,
                            }
                            for point in rows
                        ]
                    }
                }
            if method == "PUT" and suffix.startswith("points"):
                assert isinstance(payload, dict)
                raw_points = cast(list[dict[str, object]], payload["points"])
                if self.fail_partial_knowledge_upsert and any(
                    cast(dict[str, object], point["payload"]).get("record_kind")
                    == "knowledge"
                    for point in raw_points
                ):
                    self.fail_partial_knowledge_upsert = False
                    if raw_points:
                        first = raw_points[0]
                        points[str(first["id"])] = copy.deepcopy(first)
                    raise KnowledgeSearchError(
                        "injected partial point upsert",
                        category="index_update_failure",
                    )
                for point in raw_points:
                    points[str(point["id"])] = copy.deepcopy(point)
                return {"result": {"status": "completed"}}
            if method == "DELETE" and suffix.startswith("points"):
                assert isinstance(payload, dict)
                if self.fail_partial_delete and payload["points"]:
                    self.fail_partial_delete = False
                    points.pop(str(payload["points"][0]), None)
                    raise KnowledgeSearchError(
                        "injected partial point delete",
                        category="index_update_failure",
                    )
                for identifier in payload["points"]:
                    points.pop(str(identifier), None)
                return {"result": {"status": "completed"}}

        if method == "POST" and path == "collections/aliases":
            assert isinstance(payload, dict)
            for action in payload["actions"]:
                if "delete_alias" in action:
                    self.aliases.pop(action["delete_alias"]["alias_name"], None)
                else:
                    item = action["create_alias"]
                    self.aliases[item["alias_name"]] = item["collection_name"]
            if self.fail_alias_after_apply:
                self.fail_alias_after_apply = False
                raise KnowledgeSearchError(
                    "alias response lost after update",
                    category="unavailable",
                )
            return {"result": True}
        raise AssertionError((method, path, payload))

    @staticmethod
    def _filtered(
        points: dict[str, dict[str, object]],
        payload: dict[str, object],
    ) -> list[dict[str, object]]:
        raw_filter = payload.get("filter")
        must = raw_filter.get("must", []) if isinstance(raw_filter, dict) else []
        filters = {
            str(item["key"]): item["match"]["value"]
            for item in must
            if isinstance(item, dict)
            and isinstance(item.get("match"), dict)
            and "value" in item["match"]
        }
        return sorted(
            (
                point
                for point in points.values()
                if all(
                    cast(dict[str, object], point["payload"]).get(key) == value
                    for key, value in filters.items()
                )
            ),
            key=lambda point: str(point["id"]),
        )

    @staticmethod
    def _render_point(
        point: dict[str, object],
        *,
        with_vector: bool,
    ) -> dict[str, object]:
        output: dict[str, object] = {
            "id": point["id"],
            "payload": copy.deepcopy(point["payload"]),
        }
        if with_vector:
            output["vector"] = copy.deepcopy(point["vector"])
        return output


class DeterministicEmbedder:
    provider_id = "ollama"
    model_id = "embeddinggemma"

    def __init__(self, dimensions: int = 4) -> None:
        self.dimensions = dimensions
        self.texts: list[str] = []

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.texts.extend(texts)
        vectors: list[tuple[float, ...]] = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            vectors.append(
                tuple((digest[index] + 1) / 256 for index in range(self.dimensions))
            )
        return tuple(vectors)


class ProcessSharedLockTransport:
    def __init__(self, state: SharedState, guard: SharedLock) -> None:
        self.state = state
        self.guard = guard

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        parts = path.split("/")
        if len(parts) < 2 or parts[0] != "collections":
            raise AssertionError((method, path))
        collection = unquote(parts[1].split("?", 1)[0])
        suffix = "/".join(parts[2:])
        self.guard.acquire()
        try:
            if method == "PUT" and len(parts) == 2:
                state = self.state
                if collection in state:
                    raise KnowledgeSearchError("collection exists", category="schema_mismatch")
                state[collection] = json.dumps({"points": {}})
                return {"result": True}
            if method == "PUT" and suffix.startswith("points"):
                assert isinstance(payload, dict)
                state = self.state
                current = json.loads(state[collection])
                points = current["points"]
                for point in payload["points"]:
                    points[str(point["id"])] = point
                state[collection] = json.dumps(current)
                return {"result": {"status": "completed"}}
            if method == "POST" and suffix == "points":
                assert isinstance(payload, dict)
                state = self.state
                points = json.loads(state[collection])["points"]
                selected = [points[key] for key in payload["ids"] if key in points]
                return {
                    "result": [
                        {"id": point["id"], "payload": point["payload"]}
                        for point in selected
                    ]
                }
            if method == "DELETE" and len(parts) == 2:
                if collection in self.state:
                    del self.state[collection]
                return {"result": True}
            raise AssertionError((method, path, payload))
        finally:
            self.guard.release()


def _competing_process_lock(
    state: SharedState,
    guard: SharedLock,
    settings: QdrantVectorSettings,
    outcomes: OutcomeQueue,
) -> None:
    store = QdrantVectorIndexStore(
        ProcessSharedLockTransport(state, guard),
        settings,
        DeterministicEmbedder(),
        clock=lambda: "process-test",
    )
    try:
        with store._writer_lock(_REPOSITORY):
            outcomes.put("acquired")
    except KnowledgeSearchError as exc:
        outcomes.put(exc.category)


def store(
    transport: MemoryQdrant,
    *,
    dimensions: int = 4,
    model: str = "embeddinggemma",
    repository: str = _REPOSITORY,
) -> tuple[QdrantVectorIndexStore, DeterministicEmbedder]:
    embedder = DeterministicEmbedder(dimensions)
    settings = QdrantVectorSettings.from_mapping(
        {
            "url": "http://qdrant.test",
            "collection": "fetchcore-test",
            "batch_size": 2,
            "embedding_model": model,
            "vector_dimensions": dimensions,
        }
    )
    result = QdrantVectorIndexStore(
        transport,
        settings,
        embedder,
        clock=lambda: "2026-10-17T00:00:00+00:00",
    )
    result.repository = repository
    result.indexing_fingerprint = "indexing-contract-v1"
    return result, embedder


def index_result(
    root: Path,
    revision: str = "rev-1",
    previous: tuple[KnowledgeDocument, ...] = (),
    repository: str = _REPOSITORY,
) -> RepositoryIndexResult:
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(repository=repository, revision=revision)
    )
    return indexer.index(root, previous_documents=previous)


def test_bootstrap_schema_and_missing_snapshot_are_typed(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    with pytest.raises(KnowledgeSearchError) as missing_alias:
        index.validate_schema()
    assert missing_alias.value.category == "index_missing"
    assert not transport.writes

    (tmp_path / "service.py").write_text("value = 1\n")
    index.full_rebuild(index_result(tmp_path))
    index.validate_schema(require_vectors=True)
    assert index.load_snapshot(_REPOSITORY).document_count == 1
    assert index.load_documents(_REPOSITORY)[0].provenance.repository == _REPOSITORY

    second_transport = MemoryQdrant()
    second = store(second_transport)[0]
    second_collection = "fetchcore-test-empty"
    second_transport.collections[second_collection] = {
        "vectors": {"size": 4, "distance": "Cosine"},
        "sparse_vectors": {"text": {"modifier": "idf"}},
        "points": {},
    }
    second_alias = (
        "fetchcore-test-empty-"
        + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    )
    second_transport.aliases[second_alias] = second_collection
    second.settings = QdrantVectorSettings.from_mapping(
        {"url": "http://qdrant.test", "collection": "fetchcore-test-empty"}
    )
    second.repository = _REPOSITORY
    with pytest.raises(KnowledgeSearchError) as missing_snapshot:
        second.load_snapshot(_REPOSITORY)
    assert missing_snapshot.value.category == "snapshot_missing"
    second_transport.collections[second_collection]["sparse_vectors"] = {}
    with pytest.raises(KnowledgeSearchError) as sparse_mismatch:
        second.validate_schema()
    assert sparse_mismatch.value.category == "schema_mismatch"


def test_full_rebuild_is_explicit_and_keeps_prior_generation_on_failure(
    tmp_path: Path,
) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    first = index.full_rebuild(index_result(tmp_path, "rev-1"))
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    prior_collection = transport.aliases[alias]
    writes_before_validation = len(transport.writes)
    index.validate_schema(require_vectors=True)
    assert len(transport.writes) == writes_before_validation

    source.write_text("value = 2\n")
    transport.fail_partial_knowledge_upsert = True
    with pytest.raises(KnowledgeSearchError):
        index.full_rebuild(index_result(tmp_path, "rev-2"))
    assert transport.aliases[alias] == prior_collection
    assert index.load_snapshot(_REPOSITORY) == first.snapshot
    assert prior_collection in transport.collections

    transport.fail_alias_after_apply = True
    with pytest.raises(KnowledgeSearchError):
        index.full_rebuild(index_result(tmp_path, "rev-2"))
    assert transport.aliases[alias] == prior_collection
    assert index.load_snapshot(_REPOSITORY) == first.snapshot

    complete = index.full_rebuild(index_result(tmp_path, "rev-2"))
    assert complete.snapshot.revision == "rev-2"
    assert transport.aliases[alias] != prior_collection
    assert prior_collection in transport.collections


def test_explicit_rebuild_accepts_owned_empty_prepared_resource(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    concrete = f"{alias}-infra-v3"
    transport.collections[concrete] = {
        "vectors": {"size": 4, "distance": "Cosine"},
        "sparse_vectors": {"text": {"modifier": "idf"}},
        "points": {},
    }
    transport.aliases[alias] = concrete

    rebuilt = index.full_rebuild(index_result(tmp_path))
    assert rebuilt.snapshot.revision == "rev-1"
    assert transport.aliases[alias] != concrete
    assert concrete in transport.collections


def test_explicit_rebuild_fails_on_unaliased_repository_collection_collision(
    tmp_path: Path,
) -> None:
    transport = MemoryQdrant()
    index, embedder = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    orphan = f"{alias}-infra-v3"
    transport.collections[orphan] = {
        "vectors": {"size": 4, "distance": "Cosine"},
        "sparse_vectors": {"text": {"modifier": "idf"}},
        "points": {},
    }

    with pytest.raises(KnowledgeSearchError) as collision:
        index.full_rebuild(index_result(tmp_path))
    assert collision.value.category == "schema_mismatch"
    assert orphan in transport.collections
    assert not transport.writes
    assert not embedder.texts


def test_incremental_update_delete_rename_noop_and_provenance(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    index, embedder = store(transport)
    changed = tmp_path / "changed.py"
    renamed = tmp_path / "rename.py"
    removed = tmp_path / "removed.py"
    stable = tmp_path / "stable.py"
    changed.write_text("value = 1\n")
    renamed.write_text("renamed_value = 3\n")
    removed.write_text("removed_value = 5\n")
    stable.write_text("stable_value = 7\n")
    initial = index.full_rebuild(index_result(tmp_path))
    previous = index.load_documents(_REPOSITORY)
    embeddings_before_sync = len(embedder.texts)

    changed.write_text("value = 2\n")
    renamed.rename(tmp_path / "renamed.py")
    removed.unlink()
    result = index_result(tmp_path, previous=previous)
    outcome = index.incremental_sync(result)
    assert outcome.mode == "incremental"
    assert outcome.added >= 1
    assert outcome.updated >= 1
    assert outcome.deleted >= 2
    assert outcome.snapshot.revision == "rev-1"
    assert outcome.telemetry is not None
    assert outcome.telemetry.backend_deletes == outcome.deleted
    assert len(embedder.texts) > embeddings_before_sync
    documents = index.load_documents(_REPOSITORY)
    assert {document.path for document in documents} == {
        "changed.py",
        "renamed.py",
        "stable.py",
    }
    assert all(document.provenance.repository == _REPOSITORY for document in documents)
    assert all(document.revision == outcome.snapshot.revision for document in documents)
    assert index.load_snapshot(_REPOSITORY) == outcome.snapshot
    assert initial.snapshot.corpus_checksum != outcome.snapshot.corpus_checksum

    writes_after_sync = len(transport.writes)
    embeddings_after_sync = len(embedder.texts)
    noop = index.incremental_sync(index_result(tmp_path, previous=documents))
    assert noop.mode == "noop"
    assert noop.telemetry is not None
    assert noop.telemetry.backend_writes == noop.telemetry.backend_deletes == 0
    assert len(transport.writes) == writes_after_sync
    assert len(embedder.texts) == embeddings_after_sync


def test_incremental_partial_point_failure_rolls_back_snapshot_and_data(
    tmp_path: Path,
) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    initial = index.full_rebuild(index_result(tmp_path))
    before = index.load_documents(_REPOSITORY)
    source.write_text("value = 2\n")
    result = index_result(tmp_path, previous=before)

    transport.fail_partial_knowledge_upsert = True
    with pytest.raises(KnowledgeSearchError):
        index.incremental_sync(result)
    assert index.load_snapshot(_REPOSITORY) == initial.snapshot
    assert index.load_documents(_REPOSITORY) == before


def test_incremental_partial_delete_rolls_back_snapshot_and_data(
    tmp_path: Path,
) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    (tmp_path / "keep.py").write_text("value = 1\n")
    (tmp_path / "remove.py").write_text("value = 2\n")
    initial = index.full_rebuild(index_result(tmp_path))
    before = index.load_documents(_REPOSITORY)
    (tmp_path / "remove.py").unlink()
    result = index_result(tmp_path, previous=before)

    transport.fail_partial_delete = True
    with pytest.raises(KnowledgeSearchError):
        index.incremental_sync(result)
    assert index.load_snapshot(_REPOSITORY) == initial.snapshot
    assert index.load_documents(_REPOSITORY) == before


def test_incremental_alias_promotion_failure_restores_prior_generation(
    tmp_path: Path,
) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    initial = index.full_rebuild(index_result(tmp_path))
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    prior_collection = transport.aliases[alias]
    previous = index.load_documents(_REPOSITORY)
    source.write_text("value = 2\n")
    result = index_result(tmp_path, previous=previous)

    transport.fail_alias_after_apply = True
    with pytest.raises(KnowledgeSearchError):
        index.incremental_sync(result)
    assert transport.aliases[alias] == prior_collection
    assert index.load_snapshot(_REPOSITORY) == initial.snapshot
    assert index.load_documents(_REPOSITORY) == previous


def test_qdrant_writer_lock_rejects_competing_adapter_instance(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    first, _ = store(transport)
    second, _ = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    result = index_result(tmp_path, "rev-1")

    with first._writer_lock(_REPOSITORY):
        with pytest.raises(KnowledgeSearchError) as competing:
            second.full_rebuild(result)
        assert competing.value.category == "concurrent_write"
        assert not transport.aliases
        assert all(
            cast(dict[str, object], point["payload"]).get("record_kind") != "knowledge"
            for collection in transport.collections.values()
            for point in cast(dict[str, dict[str, object]], collection["points"]).values()
        )
    assert not any(name.startswith("fetchcore-qdrant-writer-lock-") for name in transport.collections)
    first.full_rebuild(result)
    assert first.load_snapshot(_REPOSITORY).revision == "rev-1"


def test_qdrant_writer_lock_is_shared_across_processes() -> None:
    context = multiprocessing.get_context("spawn")
    with context.Manager() as manager:
        state = manager.dict()
        guard = manager.RLock()
        outcomes = manager.Queue()
        transport = ProcessSharedLockTransport(state, guard)
        settings = QdrantVectorSettings.from_mapping(
            {
                "url": "http://qdrant.test",
                "collection": "fetchcore-test",
                "vector_dimensions": 4,
            }
        )
        owner = QdrantVectorIndexStore(
            transport,
            settings,
            DeterministicEmbedder(),
            clock=lambda: "process-test",
        )
        with owner._writer_lock(_REPOSITORY):
            process = context.Process(
                target=_competing_process_lock,
                args=(state, guard, settings, outcomes),
            )
            process.start()
            process.join(timeout=10)
            assert process.exitcode == 0
            assert outcomes.get(timeout=3) == "concurrent_write"
        assert not state


def test_incompatible_embedding_dimensions_fail_closed(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport, dimensions=3)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    index.full_rebuild(index_result(tmp_path))

    incompatible, _ = store(transport, dimensions=5)
    incompatible.repository = _REPOSITORY
    with pytest.raises(KnowledgeSearchError) as schema_mismatch:
        incompatible.validate_schema(require_vectors=True)
    assert schema_mismatch.value.category == "schema_mismatch"
    with pytest.raises(KnowledgeSearchError) as mismatch:
        incompatible.load_snapshot(_REPOSITORY)
    assert mismatch.value.category == "schema_mismatch"
    with pytest.raises(KnowledgeSearchError) as rebuild_required:
        incompatible.incremental_sync(index_result(tmp_path, "rev-2"))
    assert rebuild_required.value.category in {"schema_mismatch", "rebuild_required"}


def test_snapshot_pins_physical_collection_without_leaking_it_in_mapping(
    tmp_path: Path,
) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    (tmp_path / "service.py").write_text("value = 1\n")
    index.full_rebuild(index_result(tmp_path))
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    snapshot = index.load_snapshot(_REPOSITORY)
    assert snapshot.active_collection == transport.aliases[alias]
    assert "active_collection" not in snapshot.to_mapping()


@pytest.mark.parametrize("mode", ("dense", "hybrid"))
def test_search_pins_generation_across_alias_promotion(
    tmp_path: Path,
    mode: str,
) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    first = index.full_rebuild(index_result(tmp_path, "rev-1"))
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    first_collection = first.snapshot.active_collection
    assert first_collection is not None

    source.write_text("value = 2\n")
    second = index.full_rebuild(index_result(tmp_path, "rev-2"))
    second_collection = second.snapshot.active_collection
    assert second_collection is not None
    transport.aliases[alias] = first_collection
    transport.flip_alias_after_snapshot_read = (alias, second_collection)

    retrieval_mapping: dict[str, object] = {
        "dense": {
            "enabled": True,
            "embedding": {
                "provider": "ollama",
                "model": "embeddinggemma",
                "dimensions": 4,
            },
        }
    }
    if mode == "hybrid":
        retrieval_mapping["hybrid"] = {"enabled": True}
    retrieval = RetrievalConfig.from_mapping(retrieval_mapping)
    backend = QdrantVectorBackend(
        transport_factory=lambda _settings: transport,
        embedding_factory=lambda _settings, _embedding: DeterministicEmbedder(),
    )
    response = backend.search(
        SearchIntent(canonical_query="value"),
        SearchScope(
            workspace=tmp_path,
            revision=None,
            repository=_REPOSITORY,
            knowledge_types=(),
            max_results=3,
            settings={
                "url": "http://qdrant.test",
                "collection": "fetchcore-test",
                "vector_dimensions": 4,
            },
            retrieval=retrieval,
        ),
        (),
    )

    assert response.results
    assert {result.revision for result in response.results} == {"rev-1"}
    assert transport.aliases[alias] == second_collection
    assert transport.query_collections
    assert set(transport.query_collections) == {first_collection}


def test_repository_isolation_and_capabilities(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    first_workspace = tmp_path / "first"
    second_workspace = tmp_path / "second"
    first_workspace.mkdir()
    second_workspace.mkdir()
    (first_workspace / "source.py").write_text("first_value = 1\n")
    (second_workspace / "source.py").write_text("second_value = 2\n")
    first, _ = store(transport, repository="acme/first")
    second, _ = store(transport, repository="acme/second")
    first.full_rebuild(index_result(first_workspace, repository="acme/first"))
    second.full_rebuild(index_result(second_workspace, repository="acme/second"))

    assert first.load_documents("acme/first")[0].repository == "acme/first"
    assert second.load_documents("acme/second")[0].repository == "acme/second"
    assert len(transport.aliases) == 2
    assert QdrantVectorBackend.retrieval_capabilities.dense
    assert QdrantVectorBackend.retrieval_capabilities.hybrid
    assert not QdrantVectorBackend.retrieval_capabilities.targeted_lookup


def test_incremental_sync_does_not_turn_missing_snapshot_into_rebuild(
    tmp_path: Path,
) -> None:
    transport = MemoryQdrant()
    index, embedder = store(transport)
    (tmp_path / "service.py").write_text("value = 1\n")
    lifecycle = QdrantVectorKnowledgeIndex(
        index,
        RepositoryIndexer(
            RepositoryIndexConfig(repository=_REPOSITORY, revision="rev-1")
        ),
    )
    with pytest.raises(KnowledgeSearchError) as missing:
        lifecycle.sync(tmp_path)
    assert missing.value.category == "snapshot_missing"
    assert not transport.writes
    assert not embedder.texts


def test_application_driver_bootstrap_plan_rebuild_sync_and_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    source = workspace / "service.py"
    source.write_text("value = 1\n")
    git = ["git", "-C", str(workspace)]
    subprocess.run([*git, "init", "-b", "main"], check=True, capture_output=True)
    subprocess.run([*git, "config", "user.name", "FetchCore Test"], check=True)
    subprocess.run([*git, "config", "user.email", "fetchcore@example.test"], check=True)
    subprocess.run([*git, "remote", "add", "origin", "https://example.test/acme/widgets.git"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "first"], check=True)

    repositories = RepositoryApplicationService(
        cache_root=tmp_path / "cache",
        allowed_roots=(tmp_path,),
    )
    registered = repositories.add(str(workspace), provider="local-workspace")
    transport = MemoryQdrant()
    embedding_texts: list[str] = []

    def make_embedder(
        _embedding: EmbeddingConfig,
        _settings: Mapping[str, object],
    ) -> DeterministicEmbedder:
        result = DeterministicEmbedder()
        result.texts = embedding_texts
        return result

    monkeypatch.setattr(
        indexing_application,
        "UrllibQdrantTransport",
        lambda _settings: transport,
    )
    providers = EmbeddingProviderRegistry(factories={"ollama": make_embedder})
    backend_settings: dict[str, object] = {
        "url": "http://qdrant.test",
        "collection": "fetchcore-test",
        "embedding_model": "embeddinggemma",
        "vector_dimensions": 4,
    }
    retrieval = RetrievalConfig.from_mapping(
        {
            "dense": {
                "enabled": True,
                "embedding": {
                    "provider": "ollama",
                    "model": "embeddinggemma",
                    "dimensions": 4,
                },
            }
        }
    )
    service = indexing_application.RepositoryIndexApplicationService(
        repositories,
        backend="qdrant-vector",
        backend_settings=backend_settings,
        retrieval=retrieval,
        providers=providers,
    )

    initial_context = indexing_application.build_index_context(
        workspace=workspace,
        repository=registered.repository,
        branch="main",
        revision=None,
        backend="qdrant-vector",
        backend_settings=backend_settings,
        retrieval=retrieval,
        providers=providers,
    )
    with pytest.raises(KnowledgeSearchError) as bootstrap_missing:
        initial_context.legacy("bootstrap")
    assert bootstrap_missing.value.category == "index_missing"
    assert transport.writes == []

    initial_plan = service.run("plan", registered.repository)
    assert initial_plan["mode"] == "initial"
    assert transport.writes == []
    with pytest.raises(KnowledgeSearchError) as missing_sync:
        service.run("sync", registered.repository)
    assert missing_sync.value.category == "snapshot_missing"
    assert transport.writes == []

    rebuilt = service.run("rebuild", registered.repository)
    assert rebuilt["mode"] == "rebuild"
    rebuild_execution = cast(dict[str, object], rebuilt["execution"])
    rebuild_snapshot = cast(dict[str, object], rebuild_execution["snapshot"])
    assert rebuild_snapshot["revision"] == rebuilt["target_revision"]
    assert service.run("status", registered.repository)["currentness"] == "current"
    writes_after_rebuild = len(transport.writes)
    current_context = indexing_application.build_index_context(
        workspace=workspace,
        repository=registered.repository,
        branch="main",
        revision=subprocess.check_output(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"],
            text=True,
        ).strip(),
        backend="qdrant-vector",
        backend_settings=backend_settings,
        retrieval=retrieval,
        providers=providers,
    )
    bootstrap = current_context.legacy("bootstrap")
    assert bootstrap["validated"] is True
    embedding_probe = cast(dict[str, object], bootstrap["embedding_probe"])
    assert embedding_probe["dimensions"] == 4
    assert bootstrap["mode"] == "noop"
    assert bootstrap["currentness"] == "current"
    retrieval_capabilities = cast(dict[str, object], bootstrap["retrieval_capabilities"])
    assert "targeted_lookup" not in retrieval_capabilities
    assert len(transport.writes) == writes_after_rebuild
    embeddings_after_rebuild = len(embedding_texts)
    assert service.run("plan", registered.repository)["mode"] == "noop"
    noop = service.run("sync", registered.repository)
    noop_execution = cast(dict[str, object], noop["execution"])
    assert noop_execution["noop"] is True
    assert len(transport.writes) == writes_after_rebuild
    assert len(embedding_texts) == embeddings_after_rebuild

    source.write_text("value = 2\n")
    changed_plan = service.run("plan", registered.repository)
    assert changed_plan["mode"] == "incremental"
    assert len(transport.writes) == writes_after_rebuild
    changed = service.run("sync", registered.repository)
    assert changed["mode"] == "incremental"
    changed_execution = cast(dict[str, object], changed["execution"])
    changed_telemetry = cast(dict[str, object], changed_execution["telemetry"])
    assert (
        isinstance(changed_telemetry["embedding_vectors"], int)
        and changed_telemetry["embedding_vectors"] > 0
    )
    writes_after_sync = len(transport.writes)
    embeddings_after_sync = len(embedding_texts)
    no_op_after_dirty_sync = service.run("sync", registered.repository)
    no_op_execution = cast(dict[str, object], no_op_after_dirty_sync["execution"])
    assert no_op_execution["noop"] is True
    assert len(transport.writes) == writes_after_sync
    assert len(embedding_texts) == embeddings_after_sync


def test_driver_without_git_history_uses_incremental_full_scan_not_rebuild(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository=_REPOSITORY,
        branch=None,
        revision=None,
    )
    transport = MemoryQdrant()
    index, embedder = store(transport)
    driver = QdrantOperationalIndex(index, runtime)

    with pytest.raises(KnowledgeSearchError) as absent:
        driver.run("sync")
    assert absent.value.category == "snapshot_missing"
    rebuilt = driver.run("rebuild")
    assert rebuilt["mode"] == "rebuild"
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    prior_collection = transport.aliases[alias]

    source.write_text("value = 2\n")
    writes_before_plan = len(transport.writes)
    plan = driver.run("plan")
    assert plan["mode"] == "incremental"
    assert cast(dict[str, object], plan["discovery"])["mode"] == "full-scan"
    assert len(transport.writes) == writes_before_plan
    sync = driver.run("sync")
    execution = cast(dict[str, object], sync["execution"])
    discovery = cast(dict[str, object], sync["discovery"])
    assert sync["mode"] == "incremental"
    assert execution["mode"] == "incremental"
    assert discovery["mode"] == "full-scan"
    assert transport.aliases[alias] != prior_collection
    assert prior_collection in transport.collections
    writes_after_sync = len(transport.writes)
    embeddings_after_sync = len(embedder.texts)

    no_op = driver.run("sync")
    assert no_op["mode"] == "noop"
    assert cast(dict[str, object], no_op["execution"])["noop"] is True
    assert len(transport.writes) == writes_after_sync
    assert len(embedder.texts) == embeddings_after_sync
