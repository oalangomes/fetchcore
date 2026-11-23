"""Fault-injected two-collection publication and fail-closed reader tests."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from fetchcore.adapters.retrieval.solr.backend import SolrSettings
from fetchcore.application.graph_publication import (
    GraphPublicationManifest, GraphPublicationService,
)
from fetchcore.knowledge.graph_projection import GraphProjection
from fetchcore.retrieval.contracts import KnowledgeSearchError


class FakeApi:
    def __init__(self) -> None:
        self.aliases = {"knowledge": "knowledge-old"}
        self.collections = {"knowledge-old", "knowledge-new"}
        self.fail_create = False
        self.fail_alias_before = False
        self.lose_alias_response = False

    def list_aliases(self):
        return dict(self.aliases)

    def list_collections(self):
        return tuple(sorted(self.collections))

    def collection_configset(self, collection):
        return "fetchcore-graph-v1" if collection not in {
            "knowledge-old", "knowledge-new",
        } else "fetchcore-projection-v3"

    def create_collection(self, collection, configset):
        assert configset == "fetchcore-graph-v1"
        if self.fail_create:
            raise KnowledgeSearchError("create failed", category="index_update_failure")
        self.collections.add(collection)

    def set_alias(self, alias, collection):
        if self.fail_alias_before:
            raise KnowledgeSearchError("promotion failed", category="index_update_failure")
        self.aliases[alias] = collection
        if self.lose_alias_response:
            self.lose_alias_response = False
            raise KnowledgeSearchError("response lost", category="unavailable")

    def delete_alias(self, alias):
        self.aliases.pop(alias, None)

    def delete_collection(self, collection):
        self.collections.remove(collection)


class FakeStore:
    def __init__(self) -> None:
        self.manifest = None
        self.edges = ()
        self.fail_write = False

    def validate_schema(self, collection):
        assert collection

    def write_projection(self, collection, projection, manifest):
        if self.fail_write:
            raise KnowledgeSearchError("write failed", category="index_update_failure")
        self.manifest = manifest
        self.edges = projection.edges

    def load_manifest(self, collection, repository):
        assert repository == "owner/repo"
        if self.manifest is None:
            raise KnowledgeSearchError("manifest absent", category="snapshot_missing")
        return self.manifest

    def load_all_edges(self, collection, repository, revision, generation):
        return self.edges

    def count_records(self, collection):
        return len(self.edges) + (self.manifest is not None)


class FakeManager:
    def __init__(self, api: FakeApi) -> None:
        self.api = api
        self.settings = SolrSettings(
            url="http://example.test/solr", collection="knowledge",
            deployment_mode="solrcloud", configset="fetchcore-projection-v3",
        )
        self.snapshot = SimpleNamespace(
            repository="owner/repo", revision="revision-1", corpus_checksum="corpus",
            projection_version="3", projection_fingerprint="projection-v3",
            indexing_fingerprint="indexing", status="ready",
        )
        self.orphan_lock = False

    @contextmanager
    def writer_lock(self, repository: str) -> Iterator[None]:
        assert repository == "owner/repo"
        if self.orphan_lock:
            raise KnowledgeSearchError("orphan writer lock", category="concurrent_write")
        yield

    def require_active_generation(self, repository: str) -> str:
        return self.api.aliases["knowledge"]

    def active_collection(self) -> str:
        return self.api.aliases["knowledge"]

    def store_factory(self, collection):
        return SimpleNamespace(load_snapshot=lambda repository: self.snapshot)


@pytest.fixture
def setup():
    api = FakeApi()
    manager = FakeManager(api)
    graph_settings = replace(manager.settings, collection="knowledge_graph",
                             configset="fetchcore-graph-v1")
    stores: dict[str, FakeStore] = {}

    def store_factory(collection):
        return stores.setdefault(collection, FakeStore())

    publisher = GraphPublicationService(
        manager, graph_settings, api=api, store_factory=store_factory,  # type: ignore[arg-type]
    )
    projection = GraphProjection("owner/repo", "revision-1", "graph-1", "corpus", "indexing", ())
    return api, manager, stores, publisher, projection


def test_publication_retains_old_generation_and_rejects_stale_knowledge(setup) -> None:
    api, manager, stores, publisher, projection = setup
    first = publisher.publish(projection)
    second = publisher.publish(replace(projection, generation="graph-2"))
    assert first.active_collection in api.collections
    assert second.previous_collection == first.active_collection
    assert second.active_collection != first.active_collection
    with publisher.read_active("owner/repo", "revision-1", manager.snapshot):
        pass
    api.aliases["knowledge"] = "knowledge-new"
    with pytest.raises(KnowledgeSearchError) as exc, publisher.read_active(
        "owner/repo", "revision-1", manager.snapshot,
    ):
        pass
    assert exc.value.category == "snapshot_revision_mismatch"
    assert stores[first.active_collection].manifest is not None


@pytest.mark.parametrize("fault", ["create", "write", "activation", "lock"])
def test_staging_failures_never_activate_partial_graph(setup, fault: str) -> None:
    api, manager, _stores, publisher, projection = setup
    if fault == "create":
        api.fail_create = True
    if fault == "write":
        original_factory = publisher.store_factory

        def failing_factory(collection: str):
            store = original_factory(collection)
            store.fail_write = True
            return store

        publisher.store_factory = failing_factory
    if fault == "activation":
        api.fail_alias_before = True
    if fault == "lock":
        manager.orphan_lock = True
    with pytest.raises(KnowledgeSearchError):
        publisher.publish(projection)
    assert "knowledge_graph" not in api.aliases


def test_lost_activation_response_is_reconciled_by_observation(setup) -> None:
    api, _, _, publisher, projection = setup
    api.lose_alias_response = True
    published = publisher.publish(projection)
    assert api.aliases["knowledge_graph"] == published.active_collection


def test_alias_changed_during_read_and_manifest_mismatch_fail_closed(setup) -> None:
    api, manager, stores, publisher, projection = setup
    published = publisher.publish(projection)
    with pytest.raises(KnowledgeSearchError, match="graph alias changed"), publisher.read_active(
        "owner/repo", "revision-1", manager.snapshot,
    ):
        api.aliases["knowledge_graph"] = "external-collection"
    api.aliases["knowledge_graph"] = published.active_collection
    manifest = stores[published.active_collection].manifest
    assert isinstance(manifest, dict)
    stores[published.active_collection].manifest = {**manifest, "projection_fingerprint": "wrong"}
    with pytest.raises(KnowledgeSearchError) as exc, publisher.read_active(
        "owner/repo", "revision-1", manager.snapshot,
    ):
        pass
    assert exc.value.category == "schema_mismatch"


def test_manifest_rejects_unversioned_or_indeterminate_state(setup) -> None:
    _, _, _, publisher, projection = setup
    manifest = publisher.publish(projection).manifest.to_mapping()
    for changed in ({**manifest, "manifest_version": 2},
                    {**manifest, "publication_state": "staging"}):
        with pytest.raises(KnowledgeSearchError):
            GraphPublicationManifest.from_mapping(changed)
