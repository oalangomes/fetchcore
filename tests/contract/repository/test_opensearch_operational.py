"""OpenSearch operational adapter behavior without an external cluster."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore,
    OpenSearchKnowledgeIndex,
    OpenSearchSettings,
)
from fetchcore.application import indexing
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.retrieval.contracts import KnowledgeSearchError


class MemoryOpenSearch:
    def __init__(self) -> None:
        self.indices: dict[str, dict[str, dict[str, object]]] = {}
        self.mappings: dict[str, dict[str, object]] = {}
        self.settings: dict[str, dict[str, object]] = {}
        self.aliases: dict[str, str] = {}
        self.writes: list[tuple[str, str]] = []
        self.fail_bulk_once = False
        self.fail_alias_after_once = False
        self.fail_bulk_call: int | None = None
        self.bulk_calls = 0
        self.last_reindex: dict[str, object] = {}

    def request(
        self,
        method: str,
        path: str,
        payload: object | bytes | None = None,
        *,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        if (
            method in {"PUT", "DELETE"}
            or method == "POST"
            and path in {"_bulk", "_aliases", "_reindex?wait_for_completion=false&refresh=true"}
        ):
            self.writes.append((method, path))
        if method == "GET" and path == "_cluster/health":
            return {"status": "green"}
        if method == "GET" and path.startswith("_alias/"):
            alias = path.split("/", 1)[1]
            if alias not in self.aliases:
                raise KnowledgeSearchError("missing alias", category="index_missing")
            return {self.aliases[alias]: {"aliases": {alias: {}}}}
        if method == "GET" and path.endswith("/_mapping"):
            index = path.split("/", 1)[0]
            return {index: {"mappings": self.mappings[index]}}
        if method == "GET" and path.endswith("/_settings"):
            index = path.split("/", 1)[0]
            return {index: {"settings": {"index": self.settings[index]}}}
        if method == "PUT":
            if path in self.indices:
                raise KnowledgeSearchError("index exists", category="schema_mismatch")
            assert isinstance(payload, dict)
            self.indices[path] = {}
            self.mappings[path] = copy.deepcopy(payload["mappings"])
            settings = copy.deepcopy(payload.get("settings", {}))
            nested_index = settings.pop("index", {})
            self.settings[path] = {**settings, **nested_index}
            return {"acknowledged": True}
        if method == "DELETE":
            if path not in self.indices:
                raise KnowledgeSearchError("missing index", category="index_missing")
            del self.indices[path]
            del self.mappings[path]
            del self.settings[path]
            return {"acknowledged": True}
        if method == "POST" and path == "_bulk":
            assert isinstance(payload, bytes)
            self.bulk_calls += 1
            if self.fail_bulk_once or self.fail_bulk_call == self.bulk_calls:
                self.fail_bulk_once = False
                self.fail_bulk_call = None
                return {"errors": True}
            lines = [json.loads(line) for line in payload.decode().splitlines()]
            offset = 0
            while offset < len(lines):
                action = lines[offset]
                if "index" in action:
                    item = action["index"]
                    self.indices[item["_index"]][item["_id"]] = lines[offset + 1]
                    offset += 2
                else:
                    item = action["delete"]
                    self.indices[item["_index"]].pop(item["_id"])
                    offset += 1
            return {"errors": False}
        if method == "POST" and path == "_reindex?wait_for_completion=false&refresh=true":
            assert isinstance(payload, dict)
            source = payload["source"]["index"]
            target = payload["dest"]["index"]
            self.indices[target] = copy.deepcopy(self.indices[source])
            self.last_reindex = {
                "created": len(self.indices[target]),
                "failures": [],
                "timed_out": False,
            }
            return {"task": "memory:1"}
        if method == "GET" and path == "_tasks/memory:1":
            return {"completed": True, "response": self.last_reindex}
        if method == "POST" and path == "_aliases":
            assert isinstance(payload, dict)
            for action in payload["actions"]:
                if "remove" in action:
                    self.aliases.pop(action["remove"]["alias"])
                else:
                    self.aliases[action["add"]["alias"]] = action["add"]["index"]
            if self.fail_alias_after_once:
                self.fail_alias_after_once = False
                raise KnowledgeSearchError("ambiguous alias response", category="timeout")
            return {"acknowledged": True}
        if method == "POST" and path.endswith("/_refresh"):
            return {"_shards": {"successful": 1}}
        if method == "POST" and path.endswith("/_count"):
            index = path.split("/", 1)[0]
            if "match_all" in payload["query"]:
                return {"count": len(self.indices[index])}
            if "repository" in payload["query"].get("term", {}):
                repository = payload["query"]["term"]["repository"]
                return {
                    "count": sum(
                        item["repository"] == repository for item in self.indices[index].values()
                    )
                }
            return {
                "count": sum(
                    item["record_kind"] == "knowledge" for item in self.indices[index].values()
                )
            }
        if method == "POST" and path.endswith("/_search"):
            assert isinstance(payload, dict)
            index = path.split("/", 1)[0]
            index = self.aliases.get(index, index)
            if index not in self.indices:
                raise KnowledgeSearchError("missing index", category="index_missing")
            filters = payload["query"]["bool"]["filter"]
            terms = {key: value for item in filters for key, value in item["term"].items()}
            rows = [
                copy.deepcopy(item)
                for item in self.indices[index].values()
                if all(item.get(key) == value for key, value in terms.items())
            ]
            rows.sort(key=lambda item: str(item["id"]))
            after = payload.get("search_after")
            if isinstance(after, list):
                rows = [item for item in rows if str(item["id"]) > str(after[0])]
            rows = rows[: payload["size"]]
            return {
                "hits": {
                    "total": {"value": len(rows)},
                    "hits": [
                        {"_source": item, "_score": 1.0, "sort": [item["id"]]} for item in rows
                    ],
                }
            }
        raise AssertionError((method, path, content_type))


def indexer(revision: str) -> RepositoryIndexer:
    return RepositoryIndexer(RepositoryIndexConfig(repository="acme/widgets", revision=revision))


def store(transport: MemoryOpenSearch) -> OpenSearchIndexStore:
    result = OpenSearchIndexStore(
        transport,
        OpenSearchSettings(index="fetchcore-test"),
        clock=lambda: "2026-10-17T00:00:00+00:00",
    )
    result.repository = "acme/widgets"
    result.indexing_fingerprint = "test-indexing-contract"
    return result


def test_initial_incremental_delete_noop_and_provenance(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    with pytest.raises(KnowledgeSearchError, match="alias"):
        persistence.validate_schema()
    (tmp_path / "first.py").write_text("value = 1\n")
    (tmp_path / "gone.py").write_text("old = True\n")
    first = indexer("rev-1").index(tmp_path)
    persistence.full_rebuild(first)
    persistence.validate_schema()
    assert (
        persistence.load_snapshot("acme/widgets").indexing_fingerprint == "test-indexing-contract"
    )
    before = persistence.load_documents("acme/widgets")
    assert {item.path for item in before} == {"first.py", "gone.py"}
    writes = len(transport.writes)
    assert (
        OpenSearchKnowledgeIndex(persistence, indexer("rev-1"))
        .sync(tmp_path)
        .telemetry.backend_writes
        == 0
    )
    assert len(transport.writes) == writes

    (tmp_path / "first.py").write_text("value = 2\n")
    (tmp_path / "gone.py").rename(tmp_path / "renamed.py")
    second = indexer("rev-2").index(tmp_path, previous_documents=before)
    outcome = persistence.incremental_sync(second)
    assert outcome.updated >= 1
    assert outcome.deleted >= 1
    assert outcome.telemetry.embedding_vectors == 0
    after = persistence.load_documents("acme/widgets")
    assert {item.path for item in after} == {"first.py", "renamed.py"}
    assert persistence.load_snapshot("acme/widgets").revision == "rev-2"
    assert all(item.provenance.repository == "acme/widgets" for item in after)


def test_bulk_failure_keeps_alias_and_retry_is_deterministic(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    (tmp_path / "file.py").write_text("value = 1\n")
    persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    previous = persistence.load_documents("acme/widgets")
    previous_alias = dict(transport.aliases)
    (tmp_path / "file.py").write_text("value = 2\n")
    changed = indexer("rev-2").index(tmp_path, previous_documents=previous)
    transport.fail_bulk_once = True
    with pytest.raises(KnowledgeSearchError, match="bulk"):
        persistence.incremental_sync(changed)
    assert transport.aliases == previous_alias
    assert persistence.load_snapshot("acme/widgets").revision == "rev-1"
    persistence.incremental_sync(changed)
    assert persistence.load_snapshot("acme/widgets").revision == "rev-2"


def test_writer_lock_rejects_competing_writer(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    (tmp_path / "file.py").write_text("value = 1\n")
    with persistence._write_lock("acme/widgets"), pytest.raises(KnowledgeSearchError) as error:
        persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    assert error.value.category == "concurrent_write"
    assert not transport.aliases


def test_failed_delete_does_not_publish_snapshot(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    (tmp_path / "file.py").write_text("value = 1\n")
    (tmp_path / "removed.py").write_text("old = 1\n")
    persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    previous = persistence.load_documents("acme/widgets")
    (tmp_path / "removed.py").unlink()
    changed = indexer("rev-2").index(tmp_path, previous_documents=previous)
    transport.fail_bulk_call = transport.bulk_calls + 1
    with pytest.raises(KnowledgeSearchError):
        persistence.incremental_sync(changed)
    assert persistence.load_snapshot("acme/widgets").revision == "rev-1"
    persistence.incremental_sync(changed)
    assert persistence.load_snapshot("acme/widgets").revision == "rev-2"


def test_ambiguous_alias_response_can_be_recovered_by_read(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    (tmp_path / "file.py").write_text("value = 1\n")
    persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    previous = persistence.load_documents("acme/widgets")
    (tmp_path / "file.py").write_text("value = 2\n")
    changed = indexer("rev-2").index(tmp_path, previous_documents=previous)
    transport.fail_alias_after_once = True
    with pytest.raises(KnowledgeSearchError):
        persistence.incremental_sync(changed)
    assert persistence.load_snapshot("acme/widgets").revision == "rev-2"
    assert {item.content for item in persistence.load_documents("acme/widgets")} == {
        item.content for item in changed.documents
    }


def test_mapping_and_indexing_identity_fail_closed(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    (tmp_path / "file.py").write_text("value = 1\n")
    persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    active = next(iter(transport.aliases.values()))
    transport.mappings[active]["properties"]["canonical_payload"]["type"] = "keyword"
    with pytest.raises(KnowledgeSearchError) as error:
        persistence.validate_schema()
    assert error.value.category == "schema_mismatch"
    transport.mappings[active]["properties"]["canonical_payload"]["type"] = "text"
    previous = persistence.load_documents("acme/widgets")
    (tmp_path / "file.py").write_text("value = 2\n")
    changed = indexer("rev-2").index(tmp_path, previous_documents=previous)
    persistence.indexing_fingerprint = "different"
    with pytest.raises(KnowledgeSearchError) as error:
        persistence.incremental_sync(changed)
    assert error.value.category == "rebuild_required"
    assert persistence.load_snapshot("acme/widgets").revision == "rev-1"


def test_explicit_rebuild_accepts_owned_legacy_generation(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    (tmp_path / "file.py").write_text("value = 1\n")
    persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    old_index = next(iter(transport.aliases.values()))
    del transport.mappings[old_index]["_meta"]
    (tmp_path / "file.py").write_text("value = 2\n")
    persistence.full_rebuild(indexer("rev-2").index(tmp_path))
    assert persistence.load_snapshot("acme/widgets").revision == "rev-2"
    assert old_index in transport.indices


def test_rebuild_refuses_foreign_alias_owner(tmp_path: Path) -> None:
    transport = MemoryOpenSearch()
    persistence = store(transport)
    (tmp_path / "file.py").write_text("value = 1\n")
    persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    old_index = next(iter(transport.aliases.values()))
    transport.mappings[old_index]["_meta"]["repository"] = "other/repository"
    with pytest.raises(KnowledgeSearchError) as error:
        persistence.full_rebuild(indexer("rev-2").index(tmp_path))
    assert error.value.category == "ownership_unverified"
    assert transport.aliases[next(iter(transport.aliases))] == old_index


def test_dense_mapping_and_embedding_identity(tmp_path: Path) -> None:
    class Provider:
        provider_id = "ollama"
        model_id = "embeddinggemma"

        def __init__(self) -> None:
            self.calls = 0

        def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            self.calls += 1
            return tuple((1.0,) * 768 for _ in texts)

    transport = MemoryOpenSearch()
    provider = Provider()
    persistence = OpenSearchIndexStore(
        transport,
        OpenSearchSettings(index="fetchcore-test"),
        clock=lambda: "2026-10-17T00:00:00+00:00",
        embedding_provider=provider,
    )
    persistence.repository = "acme/widgets"
    persistence.indexing_fingerprint = "test-indexing-contract"
    (tmp_path / "file.py").write_text("value = 1\n")
    persistence.full_rebuild(indexer("rev-1").index(tmp_path))
    persistence.validate_schema(require_vectors=True)
    snapshot = persistence.load_snapshot("acme/widgets")
    assert persistence.embedding_snapshot_matches(snapshot)
    assert snapshot.vector_dimensions == 768
    calls = provider.calls
    assert (
        OpenSearchKnowledgeIndex(persistence, indexer("rev-1"))
        .sync(tmp_path)
        .telemetry.embedding_vectors
        == 0
    )
    assert provider.calls == calls
    active = next(iter(transport.aliases.values()))
    method = transport.mappings[active]["properties"]["embedding_vector"]["method"]
    method["parameters"] = {}  # OpenSearch 3.8 normalizes this on GET _mapping.
    persistence.validate_schema(require_vectors=True)
    method["parameters"] = {"ef_construction": 128}
    with pytest.raises(KnowledgeSearchError) as error:
        persistence.validate_schema(require_vectors=True)
    assert error.value.category == "schema_mismatch"
    method["parameters"] = {}
    transport.settings[active]["knn"] = False
    with pytest.raises(KnowledgeSearchError) as error:
        persistence.validate_schema(require_vectors=True)
    assert error.value.category == "schema_mismatch"


def test_registered_application_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    subprocess.run(["git", "-C", str(workspace), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(workspace), "config", "user.email", "test@example.test"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "remote",
            "add",
            "origin",
            "https://example.test/acme/widgets.git",
        ],
        check=True,
    )
    (workspace / "file.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "first"], check=True)
    repositories = RepositoryApplicationService(
        cache_root=tmp_path / "cache",
        allowed_roots=(tmp_path,),
    )
    registered = repositories.add(str(workspace), provider="local-workspace")
    transport = MemoryOpenSearch()
    monkeypatch.setattr(indexing, "UrllibOpenSearchTransport", lambda settings: transport)
    service = indexing.RepositoryIndexApplicationService(
        repositories,
        backend="opensearch",
        backend_settings={"index": "fetchcore-test"},
    )
    assert service.run("plan", registered.repository)["mode"] == "initial"
    assert transport.writes == []
    assert service.run("status", registered.repository)["currentness"] == "absent"
    assert transport.writes == []
    assert service.run("rebuild", registered.repository)["execution"]["snapshot"]["revision"]
    assert service.run("status", registered.repository)["currentness"] == "current"
    writes = len(transport.writes)
    assert service.run("plan", registered.repository)["mode"] == "noop"
    assert service.run("sync", registered.repository)["execution"]["noop"] is True
    assert len(transport.writes) == writes
    (workspace / "file.py").write_text("value = 2\n")
    assert service.run("status", registered.repository)["currentness"] == "unknown"
    assert service.run("plan", registered.repository)["mode"] == "incremental"
    dirty = service.run("sync", registered.repository)
    assert dirty["mode"] == "incremental"
    writes = len(transport.writes)
    assert service.run("plan", registered.repository)["mode"] == "noop"
    assert service.run("sync", registered.repository)["execution"]["noop"] is True
    assert len(transport.writes) == writes
    subprocess.run(["git", "-C", str(workspace), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(workspace), "commit", "-qm", "second"], check=True)
    assert service.run("plan", registered.repository)["mode"] == "incremental"
    synced = service.run("sync", registered.repository)
    assert synced["execution"]["snapshot"]["revision"] == synced["target_revision"]
    assert synced["execution"]["telemetry"]["embedding_vectors"] == 0
    assert synced["discovery"]["mode"] == "git-delta"
    assert service.run("status", registered.repository)["currentness"] == "current"
