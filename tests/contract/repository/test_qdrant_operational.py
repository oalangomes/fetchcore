from __future__ import annotations

import copy
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast
from urllib.parse import unquote

import pytest

from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorBackend,
    QdrantVectorIndexStore,
    QdrantVectorKnowledgeIndex,
    QdrantVectorSettings,
)
from fetchcore.projection import corpus_checksum
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexConfig,
    RepositoryIndexer,
    RepositoryIndexResult,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError


_REPOSITORY = "acme/widgets"


class MemoryQdrant:
    def __init__(self) -> None:
        self.collections: dict[str, dict[str, object]] = {}
        self.aliases: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []
        self.fail_partial_knowledge_upsert = False
        self.fail_partial_delete = False
        self.fail_alias_after_apply = False

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
                if name in self.collections:
                    raise KnowledgeSearchError("collection exists", category="schema_mismatch")
                self.collections[name] = {
                    "vectors": copy.deepcopy(payload["vectors"]),
                    "sparse_vectors": copy.deepcopy(payload["sparse_vectors"]),
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
                return {
                    "result": [
                        self._render_point(
                            row,
                            with_vector=payload.get("with_vector") is True,
                        )
                        for row in selected
                    ]
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


def test_concurrent_rebuilds_share_repository_writer_lock(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    first, _ = store(transport)
    second, _ = store(transport)
    source = tmp_path / "service.py"
    source.write_text("value = 1\n")
    first.full_rebuild(index_result(tmp_path, "rev-1"))
    source.write_text("value = 2\n")
    result_two = index_result(tmp_path, "rev-2")
    source.write_text("value = 3\n")
    result_three = index_result(tmp_path, "rev-3")

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(first.full_rebuild, result_two),
            executor.submit(second.full_rebuild, result_three),
        )
        outcomes = [future.result() for future in futures]

    snapshot = first.load_snapshot(_REPOSITORY)
    assert snapshot in {outcome.snapshot for outcome in outcomes}
    assert snapshot.corpus_checksum in {
        corpus_checksum(result_two.documents),
        corpus_checksum(result_three.documents),
    }
    assert len(first.load_documents(_REPOSITORY)) == snapshot.document_count


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


def test_updating_snapshot_is_not_reported_as_current(tmp_path: Path) -> None:
    transport = MemoryQdrant()
    index, _ = store(transport)
    (tmp_path / "service.py").write_text("value = 1\n")
    index.full_rebuild(index_result(tmp_path))
    alias = "fetchcore-test-" + hashlib.sha256(_REPOSITORY.encode()).hexdigest()[:12]
    collection = transport.aliases[alias]
    points = cast(dict[str, dict[str, object]], transport.collections[collection]["points"])
    snapshot_id = next(
        point_id
        for point_id, point in points.items()
        if cast(dict[str, object], point["payload"]).get("record_kind") == "snapshot"
    )
    cast(dict[str, object], points[snapshot_id]["payload"])["snapshot_status"] = "updating"
    with pytest.raises(KnowledgeSearchError) as stale:
        index.load_snapshot(_REPOSITORY)
    assert stale.value.category == "index_stale"


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
