"""Consistent, optional publication of a graph beside a knowledge generation.

Only the graph alias is activated here. A manifest in its immutable physical
collection binds it to the exact physical knowledge generation. Readers pin
both physical collections and verify the active knowledge alias before and
after reading. A later knowledge-only sync therefore disables graph serving.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass

from fetchcore.adapters.retrieval.solr.backend import SolrIndexSnapshot, SolrSettings
from fetchcore.adapters.retrieval.solr.cloud_lifecycle import (
    SolrCloudCollectionsApi,
    SolrCloudGenerationManager,
    UrllibSolrCloudCollectionsApi,
    _alias_targets,
    _generation_collection,
    _generation_prefix,
)
from fetchcore.adapters.retrieval.solr.graph_store import GraphStore, edge_checksum
from fetchcore.adapters.retrieval.solr.index_contract import snapshot_indexing_fingerprint
from fetchcore.knowledge.graph_projection import (
    GRAPH_PROJECTION_FINGERPRINT,
    GRAPH_PROJECTION_VERSION,
    GraphProjection,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError


@dataclass(frozen=True)
class GraphPublicationManifest:
    """Versioned compatibility record; graph alias is the activation point."""

    repository: str
    revision: str
    generation: str
    graph_collection: str
    graph_alias: str
    knowledge_collection: str
    knowledge_alias: str
    corpus_checksum: str
    indexing_fingerprint: str
    knowledge_projection_version: str
    knowledge_projection_fingerprint: str
    projection_version: str
    projection_fingerprint: str
    edge_count: int
    edges_checksum: str
    publication_state: str = "eligible"
    manifest_version: int = 1

    def to_mapping(self) -> dict[str, object]:
        return dict(vars(self))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> GraphPublicationManifest:
        if set(raw) != set(cls.__dataclass_fields__):
            raise KnowledgeSearchError("graph manifest fields are incompatible", category="schema_mismatch")
        texts = set(cls.__dataclass_fields__) - {"edge_count", "manifest_version"}
        if any(not isinstance(raw[key], str) or not raw[key] for key in texts):
            raise KnowledgeSearchError("graph manifest identity is invalid", category="schema_mismatch")
        if (type(raw["edge_count"]) is not int or raw["edge_count"] < 0
                or raw["manifest_version"] != 1
                or raw["publication_state"] != "eligible"
                or raw["projection_version"] != GRAPH_PROJECTION_VERSION
                or raw["projection_fingerprint"] != GRAPH_PROJECTION_FINGERPRINT):
            raise KnowledgeSearchError("graph manifest contract is incompatible", category="schema_mismatch")
        return cls(**raw)  # type: ignore[arg-type]


@dataclass(frozen=True)
class GraphReadSession:
    graph_collection: str
    knowledge_collection: str
    manifest: GraphPublicationManifest
    store: GraphStore


@dataclass(frozen=True)
class GraphPublication:
    previous_collection: str | None
    active_collection: str
    manifest: GraphPublicationManifest


class GraphPublicationService:
    """Staged graph rebuild and fail-closed, physically pinned graph reads."""

    def __init__(
        self,
        knowledge_manager: SolrCloudGenerationManager,
        graph_settings: SolrSettings,
        *,
        api: SolrCloudCollectionsApi | None = None,
        store_factory: Callable[[str], GraphStore] | None = None,
    ) -> None:
        if (graph_settings.deployment_mode != "solrcloud"
                or knowledge_manager.settings.url != graph_settings.url
                or knowledge_manager.settings.collection == graph_settings.collection
                or knowledge_manager.settings.configset == graph_settings.configset):
            raise KnowledgeSearchError(
                "graph publication requires distinct SolrCloud aliases and configsets",
                category="schema_mismatch",
            )
        self.knowledge_manager = knowledge_manager
        self.graph_settings = graph_settings
        self.api = api or UrllibSolrCloudCollectionsApi(graph_settings)
        self.store_factory = store_factory or (lambda _collection: GraphStore(graph_settings))

    def _graph_target(self, *, allow_missing: bool = False) -> str | None:
        targets = _alias_targets(self.api.list_aliases().get(self.graph_settings.collection))
        if not targets and allow_missing:
            return None
        if len(targets) != 1:
            raise KnowledgeSearchError(
                "graph alias is absent or ambiguous",
                category="unsupported_capability" if not targets else "repository_resource_collision",
            )
        return targets[0]

    def _knowledge_snapshot(self, collection: str, repository: str) -> SolrIndexSnapshot:
        store = self.knowledge_manager.store_factory(collection)
        return store.load_snapshot(repository)

    @staticmethod
    def _snapshot_matches(
        manifest: GraphPublicationManifest, snapshot: SolrIndexSnapshot,
    ) -> bool:
        return (
            snapshot.repository == manifest.repository
            and snapshot.revision == manifest.revision
            and snapshot.corpus_checksum == manifest.corpus_checksum
            and snapshot.projection_version == manifest.knowledge_projection_version
            and snapshot.projection_fingerprint == manifest.knowledge_projection_fingerprint
            and snapshot_indexing_fingerprint(snapshot) == manifest.indexing_fingerprint
            and snapshot.status == "ready"
        )

    def publish(self, projection: GraphProjection) -> GraphPublication:
        """Build a new graph generation and activate only after full validation."""

        with self.knowledge_manager.writer_lock(projection.repository):
            knowledge_collection = self.knowledge_manager.require_active_generation(
                projection.repository,
            )
            knowledge_snapshot = self._knowledge_snapshot(knowledge_collection, projection.repository)
            if (
                knowledge_snapshot.revision != projection.revision
                or knowledge_snapshot.corpus_checksum != projection.corpus_checksum
                or snapshot_indexing_fingerprint(knowledge_snapshot) != projection.indexing_fingerprint
            ):
                raise KnowledgeSearchError(
                    "graph projection does not match the active knowledge snapshot",
                    category="snapshot_revision_mismatch",
                )
            previous = self._graph_target(allow_missing=True)
            graph_collection = _generation_collection(self.graph_settings.collection)
            if graph_collection in self.api.list_collections():
                raise KnowledgeSearchError(
                    "graph generation name already exists", category="repository_resource_collision",
                )
            manifest = GraphPublicationManifest(
                repository=projection.repository, revision=projection.revision,
                generation=projection.generation, graph_collection=graph_collection,
                graph_alias=self.graph_settings.collection,
                knowledge_collection=knowledge_collection,
                knowledge_alias=self.knowledge_manager.settings.collection,
                corpus_checksum=projection.corpus_checksum,
                indexing_fingerprint=projection.indexing_fingerprint,
                knowledge_projection_version=knowledge_snapshot.projection_version,
                knowledge_projection_fingerprint=knowledge_snapshot.projection_fingerprint or "",
                projection_version=projection.version,
                projection_fingerprint=projection.fingerprint,
                edge_count=len(projection.edges), edges_checksum=edge_checksum(projection.edges),
            )
            try:
                self.api.create_collection(graph_collection, self.graph_settings.configset)
            except Exception as exc:
                raise KnowledgeSearchError(
                    "graph staging collection creation failed; inspect cluster state",
                    category="index_update_failure",
                    metadata={"staging_collection": graph_collection,
                              "activation": "not_attempted", "recovery_required": True},
                ) from exc
            staged = self.store_factory(graph_collection)
            try:
                if self.api.collection_configset(graph_collection) != self.graph_settings.configset:
                    raise KnowledgeSearchError(
                        "graph staging collection configset differs", category="schema_mismatch",
                    )
                staged.write_projection(graph_collection, projection, manifest.to_mapping())
                observed = GraphPublicationManifest.from_mapping(
                    staged.load_manifest(graph_collection, projection.repository),
                )
                edges = staged.load_all_edges(
                    graph_collection, projection.repository, projection.revision,
                    projection.generation,
                )
                if (observed != manifest or edges != projection.edges
                        or edge_checksum(edges) != manifest.edges_checksum
                        or staged.count_records(graph_collection) != len(edges) + 1):
                    raise KnowledgeSearchError(
                        "graph staged generation differs from canonical projection",
                        category="index_update_failure",
                    )
                self._assert_knowledge_current(knowledge_collection, manifest)
                if self._graph_target(allow_missing=True) != previous:
                    raise KnowledgeSearchError(
                        "graph alias changed during staging", category="concurrent_write",
                    )
            except Exception as exc:
                category = exc.category if isinstance(exc, KnowledgeSearchError) else "index_update_failure"
                raise KnowledgeSearchError(
                    "graph generation staging failed; prior graph remains active",
                    category=category,
                    metadata={"staging_collection": graph_collection,
                              "activation": "not_attempted", "recovery_required": True},
                ) from exc
            try:
                self.api.set_alias(self.graph_settings.collection, graph_collection)
            except Exception as exc:
                target = self._graph_target_after_error()
                if target != graph_collection:
                    raise KnowledgeSearchError(
                        "graph activation was not confirmed" if target == previous
                        else "graph activation outcome is indeterminate",
                        category="index_update_failure" if target == previous else "concurrent_write",
                        metadata={"observed_target": target, "previous_collection": previous,
                                  "staging_collection": graph_collection,
                                  "recovery_required": target != previous},
                    ) from exc
            try:
                if self._graph_target() != graph_collection:
                    raise KnowledgeSearchError(
                        "graph alias moved after activation", category="concurrent_write",
                    )
                self._assert_knowledge_current(knowledge_collection, manifest)
                if GraphPublicationManifest.from_mapping(
                    staged.load_manifest(graph_collection, projection.repository),
                ) != manifest:
                    raise KnowledgeSearchError(
                        "graph manifest changed after activation", category="schema_mismatch",
                    )
            except (KnowledgeSearchError, ValueError) as exc:
                self._rollback_after_error(previous, graph_collection, exc)
            return GraphPublication(previous, graph_collection, manifest)

    def _graph_target_after_error(self) -> str | None:
        try:
            return self._graph_target(allow_missing=True)
        except KnowledgeSearchError:
            return None

    def _rollback_after_error(
        self, previous: str | None, failed: str, cause: Exception,
    ) -> None:
        if previous is not None:
            try:
                if self._graph_target() != failed:
                    raise KnowledgeSearchError("graph alias moved before rollback", category="concurrent_write")
                self.api.set_alias(self.graph_settings.collection, previous)
                if self._graph_target() != previous:
                    raise KnowledgeSearchError("graph rollback was not confirmed", category="concurrent_write")
            except Exception as rollback_error:
                raise KnowledgeSearchError(
                    "graph activation failed and prior alias could not be restored",
                    category="concurrent_write",
                    metadata={"previous_collection": previous, "failed_collection": failed,
                              "recovery_required": True},
                ) from rollback_error
            raise KnowledgeSearchError(
                "graph activation failed; previous alias restored",
                category="index_update_failure",
                metadata={"active_collection": previous, "recovery_required": False},
            ) from cause
        raise KnowledgeSearchError(
            "first graph activation requires reconciliation",
            category="index_update_failure",
            metadata={"active_collection": failed, "recovery_required": True},
        ) from cause

    def _assert_knowledge_current(
        self, expected_collection: str, manifest: GraphPublicationManifest,
    ) -> SolrIndexSnapshot:
        if self.knowledge_manager.active_collection() != expected_collection:
            raise KnowledgeSearchError(
                "knowledge alias no longer matches graph manifest",
                category="snapshot_revision_mismatch",
            )
        snapshot = self._knowledge_snapshot(expected_collection, manifest.repository)
        if not self._snapshot_matches(manifest, snapshot):
            raise KnowledgeSearchError(
                "knowledge snapshot no longer matches graph manifest",
                category="snapshot_revision_mismatch",
            )
        return snapshot

    @contextmanager
    def read_active(
        self, repository: str, revision: str, knowledge_snapshot: SolrIndexSnapshot,
    ) -> Iterator[GraphReadSession]:
        """Pin exact physical generations and recheck activation after a read."""

        graph_collection = self._graph_target()
        assert graph_collection is not None
        store = self.store_factory(graph_collection)
        if self.api.collection_configset(graph_collection) != self.graph_settings.configset:
            raise KnowledgeSearchError("active graph configset is incompatible", category="schema_mismatch")
        store.validate_schema(graph_collection)
        manifest = GraphPublicationManifest.from_mapping(
            store.load_manifest(graph_collection, repository),
        )
        if (
            manifest.repository != repository or manifest.revision != revision
            or manifest.graph_collection != graph_collection
            or manifest.graph_alias != self.graph_settings.collection
            or manifest.knowledge_alias != self.knowledge_manager.settings.collection
            or not self._snapshot_matches(manifest, knowledge_snapshot)
            or store.count_records(graph_collection) != manifest.edge_count + 1
        ):
            raise KnowledgeSearchError(
                "active graph manifest is incompatible with selected knowledge snapshot",
                category="snapshot_revision_mismatch",
            )
        self._assert_knowledge_current(manifest.knowledge_collection, manifest)
        try:
            yield GraphReadSession(graph_collection, manifest.knowledge_collection, manifest, store)
        finally:
            if self._graph_target() != graph_collection:
                raise KnowledgeSearchError(
                    "graph alias changed during navigation", category="snapshot_revision_mismatch",
                )
            self._assert_knowledge_current(manifest.knowledge_collection, manifest)
            if store.count_records(graph_collection) != manifest.edge_count + 1:
                raise KnowledgeSearchError(
                    "graph generation changed during navigation", category="snapshot_revision_mismatch",
                )

    def inspect(self, repository: str) -> dict[str, object]:
        """Read-only reconciliation; never deletes staging or retained generations."""

        graph_collection = self._graph_target(allow_missing=True)
        collections = self.api.list_collections()
        prefix = _generation_prefix(self.graph_settings.collection)
        retained = tuple(sorted(name for name in collections if name.startswith(prefix)
                                and name != graph_collection))
        if graph_collection is None:
            return {"ready": False, "reason": "graph_alias_missing", "active_collection": None,
                    "retained_generations": retained, "mutated": False}
        try:
            store = self.store_factory(graph_collection)
            store.validate_schema(graph_collection)
            manifest = GraphPublicationManifest.from_mapping(
                store.load_manifest(graph_collection, repository),
            )
            self._assert_knowledge_current(manifest.knowledge_collection, manifest)
            ready = store.count_records(graph_collection) == manifest.edge_count + 1
            reason = "ready" if ready else "graph_record_count_mismatch"
        except KnowledgeSearchError as exc:
            ready, reason = False, exc.category
        return {"ready": ready, "reason": reason,
                "active_collection": graph_collection,
                "retained_generations": retained, "mutated": False}
