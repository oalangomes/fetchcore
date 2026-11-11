from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
from types import SimpleNamespace
from typing import cast

import pytest

from fetchcore.adapters.retrieval.solr import cloud_lifecycle
from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexSnapshot,
    SolrIndexStore,
    SolrIndexSyncResult,
    SolrSettings,
    corpus_checksum,
)
from fetchcore.adapters.retrieval.solr.cloud_lifecycle import (
    SolrCloudCollectionsApi,
    SolrCloudGenerationManager,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.repository.indexing import RepositoryIndexResult


class FakeCloud:
    def __init__(self) -> None:
        self.collections: set[str] = {"generation-initial"}
        self.aliases: dict[str, object] = {"knowledge": "generation-initial"}
        self.configsets: dict[str, str] = {"generation-initial": "fetchcore-projection-v3"}
        self.fail_promotion_after_change = False
        self.fail_promotion_before_change = False
        self.fail_alias_validation = False

    def list_collections(self) -> tuple[str, ...]:
        return tuple(sorted(self.collections))

    def list_aliases(self) -> Mapping[str, object]:
        return dict(self.aliases)

    def collection_configset(self, collection: str) -> str:
        return self.configsets[collection]

    def create_collection(self, collection: str, configset: str) -> None:
        if collection in self.collections:
            raise KnowledgeSearchError("collection already exists", category="schema_mismatch")
        self.collections.add(collection)
        self.configsets[collection] = configset

    def set_alias(self, alias: str, collection: str) -> None:
        if self.fail_promotion_before_change:
            self.fail_promotion_before_change = False
            raise KnowledgeSearchError("promotion failed", category="timeout")
        self.aliases[alias] = collection
        if self.fail_promotion_after_change:
            self.fail_promotion_after_change = False
            raise KnowledgeSearchError("response lost", category="timeout")

    def delete_collection(self, collection: str) -> None:
        self.collections.remove(collection)
        self.configsets.pop(collection, None)


class FakeCloudState:
    def __init__(self, cloud: FakeCloud) -> None:
        self.cloud = cloud
        self.snapshots: dict[str, SolrIndexSnapshot] = {}
        self.documents: dict[str, tuple[object, ...]] = {}
        self.lock_records: dict[str, list[dict[str, object]]] = {}
        self.fail_alias_validation = False

    def resolve(self, name: str) -> str:
        target = self.cloud.aliases.get(name)
        return target if isinstance(target, str) else name


class FakeTransport:
    def __init__(self, state: FakeCloudState, collection: str) -> None:
        self.state = state
        self.collection = collection

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        del params
        if path == "update" and isinstance(payload, list):
            self.state.lock_records.setdefault(self.collection, []).extend(
                item for item in payload if isinstance(item, dict)
            )
        return {}

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        del path, params
        return {"schema": {}}


class FakeStore:
    def __init__(self, state: FakeCloudState, settings: SolrSettings) -> None:
        self.state = state
        self.settings = settings
        self.embedding_provider = None
        self.transport = FakeTransport(state, settings.collection)
        self.clock = lambda: datetime.now(UTC).isoformat()
        self.timer = lambda: 0.0

    def validate_schema(self, *, require_vectors: bool = False) -> None:
        assert require_vectors is False

    def _select(self, params: Mapping[str, object]) -> dict[str, object]:
        del params
        if self.settings.collection.startswith("fetchcore-lock-"):
            records = self.state.lock_records.get(self.settings.collection, [])
            return {"docs": records, "num_found": len(records)}
        actual = self.state.resolve(self.settings.collection)
        count = len(self.state.documents.get(actual, ()))
        if actual in self.state.snapshots:
            count += 1
        return {"docs": [], "num_found": count}

    def load_snapshot(self, repository: str) -> SolrIndexSnapshot:
        actual = self.state.resolve(self.settings.collection)
        if (
            self.settings.collection == "knowledge"
            and self.state.fail_alias_validation
        ):
            self.state.fail_alias_validation = False
            raise KnowledgeSearchError("injected post-promotion failure", category="unavailable")
        snapshot = self.state.snapshots.get(actual)
        if snapshot is None or snapshot.repository != repository:
            raise KnowledgeSearchError("snapshot missing", category="snapshot_missing")
        return snapshot

    def load_documents(self, repository: str) -> tuple[object, ...]:
        del repository
        return self.state.documents.get(self.state.resolve(self.settings.collection), ())

    def full_rebuild(self, result: RepositoryIndexResult) -> SolrIndexSyncResult:
        snapshot = SolrIndexSnapshot(
            repository=result.repository,
            branch=None,
            revision=result.revision,
            corpus_checksum=corpus_checksum(result.documents),
            document_count=len(result.documents),
            projection_version="3",
            indexed_at=self.clock(),
        )
        self.state.documents[self.settings.collection] = result.documents
        self.state.snapshots[self.settings.collection] = snapshot
        return SolrIndexSyncResult(
            mode="full_rebuild",
            snapshot=snapshot,
            added=len(result.documents),
            updated=0,
            deleted=0,
            unchanged=0,
            duration_ms=1,
        )


def _result() -> RepositoryIndexResult:
    return cast(
        RepositoryIndexResult,
        SimpleNamespace(repository="owner/repo", revision="abc123", documents=()),
    )


def _manager(
    cloud: FakeCloud,
    *,
    monkeypatch: pytest.MonkeyPatch,
    state: FakeCloudState | None = None,
) -> SolrCloudGenerationManager:
    monkeypatch.setattr(cloud_lifecycle, "validate_projection_v3_schema", lambda _schema: None)
    shared = state or FakeCloudState(cloud)
    settings = SolrSettings(
        collection="knowledge",
        deployment_mode="solrcloud",
        configset="fetchcore-projection-v3",
    )
    return SolrCloudGenerationManager(
        settings,
        api=cast(SolrCloudCollectionsApi, cloud),
        store_factory=lambda collection: cast(
            SolrIndexStore,
            FakeStore(
                shared,
                SolrSettings(
                    collection=collection,
                    deployment_mode="solrcloud",
                    configset="fetchcore-projection-v3",
                ),
            ),
        ),
    )


def test_rebuild_publishes_new_generation_and_preserves_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = FakeCloud()
    manager = _manager(cloud, monkeypatch=monkeypatch)
    previous = manager.active_collection()

    publication = manager.rebuild(_result())

    assert cloud.aliases["knowledge"] == publication.active_collection
    assert publication.previous_collection == previous
    assert previous in cloud.collections
    assert publication.storage.snapshot.revision == "abc123"
    assert not any(name.startswith("fetchcore-lock-") for name in cloud.collections)


def test_lost_promotion_response_is_resolved_by_observing_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = FakeCloud()
    cloud.fail_promotion_after_change = True
    manager = _manager(cloud, monkeypatch=monkeypatch)

    publication = manager.rebuild(_result())

    assert cloud.aliases["knowledge"] == publication.active_collection


def test_failed_promotion_without_alias_change_keeps_previous_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = FakeCloud()
    cloud.fail_promotion_before_change = True
    manager = _manager(cloud, monkeypatch=monkeypatch)
    previous = manager.active_collection()

    with pytest.raises(KnowledgeSearchError, match="promotion did not take effect"):
        manager.rebuild(_result())

    assert cloud.aliases["knowledge"] == previous
    assert previous in cloud.collections


def test_post_promotion_validation_failure_restores_verified_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = FakeCloud()
    state = FakeCloudState(cloud)
    state.fail_alias_validation = True
    manager = _manager(cloud, monkeypatch=monkeypatch, state=state)
    previous = manager.active_collection()

    with pytest.raises(KnowledgeSearchError, match="previous generation was restored"):
        manager.rebuild(_result())

    assert cloud.aliases["knowledge"] == previous
    assert previous in cloud.collections


def test_existing_writer_lock_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = FakeCloud()
    manager = _manager(cloud, monkeypatch=monkeypatch)
    lock_name = "fetchcore-lock-" + hashlib.sha256(
        b"knowledge\0owner/repo"
    ).hexdigest()[:20]
    cloud.create_collection(lock_name, "fetchcore-projection-v3")

    with pytest.raises(KnowledgeSearchError) as failure, manager.writer_lock("owner/repo"):
        pytest.fail("the existing writer lock must block entry")

    assert failure.value.category == "concurrent_write"
    assert failure.value.metadata["recovery_required"] is True


def test_reconciliation_reports_stale_lock_without_mutating_cluster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cloud = FakeCloud()
    manager = _manager(cloud, monkeypatch=monkeypatch)
    lock_name = "fetchcore-lock-" + hashlib.sha256(
        b"knowledge\0owner/repo"
    ).hexdigest()[:20]
    cloud.create_collection(lock_name, "fetchcore-projection-v3")
    before = set(cloud.collections)

    report = manager.reconciliation_report("owner/repo")

    assert report["action"] == "recovery_required"
    assert report["mutated"] is False
    assert report["writer_lock"]["collection"] == lock_name
    assert cloud.collections == before


def test_alias_with_multiple_targets_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    cloud = FakeCloud()
    cloud.aliases["knowledge"] = "generation-initial,generation-other"

    with pytest.raises(KnowledgeSearchError) as failure:
        _manager(cloud, monkeypatch=monkeypatch).active_collection()

    assert failure.value.category == "repository_resource_collision"
