"""Read-only, backend-neutral composition of existing operational observations.

A healthy process is not evidence that a repository snapshot is current, and
readiness is never a judgement of retrieval quality or consumer satisfaction.
This module does not write, index, migrate, or implicitly repair projections.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TYPE_CHECKING, cast

from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.config import EmbeddingConfig

if TYPE_CHECKING:
    from fetchcore.application.runtime_inspection import RuntimeInspectionService

ReadinessState = Literal[
    "ready", "absent", "stale", "incompatible", "unavailable",
    "unsupported", "unknown", "not_required",
]
_REQUIRED_CHECKS = frozenset((
    "service", "schema", "snapshot", "currentness",
    "indexing_fingerprint", "embedding", "retrieval",
))

@dataclass(frozen=True)
class ReadinessFact:
    state: ReadinessState
    category: str | None = None

    def to_mapping(self) -> dict[str, str]:
        data: dict[str, str] = {"state": self.state}
        if self.category is not None:
            data["category"] = self.category
        return data


@dataclass(frozen=True)
class ReadinessReport:
    backend: str
    repository: str | None
    requested_revision: str | None
    indexed_revision: str | None
    mode: str
    checks: dict[str, ReadinessFact]
    fingerprints: dict[str, str | None]
    observations: dict[str, object] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return (
            _REQUIRED_CHECKS <= self.checks.keys()
            and all(f.state in {"ready", "not_required"} for f in self.checks.values())
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "ready" if self.ready else "not_ready",
            "ready": self.ready,
            "backend": self.backend,
            "repository": self.repository,
            "requested_revision": self.requested_revision,
            "indexed_revision": self.indexed_revision,
            "mode": self.mode,
            "checks": {key: value.to_mapping() for key, value in sorted(self.checks.items())},
            "fingerprints": dict(sorted(self.fingerprints.items())),
            "observations": self.observations,
        }


def _category(exc: Exception) -> str:
    value = getattr(exc, "category", None)
    return value if isinstance(value, str) and value else "inspection_failed"


def _failed(category: str) -> ReadinessFact:
    if category in {"unsupported_backend", "unsupported_capability", "incremental_unsupported"}:
        return ReadinessFact("unsupported", category)
    if category in {"snapshot_missing", "index_missing", "collection_missing"}:
        return ReadinessFact("absent", category)
    if category in {"index_stale", "snapshot_revision_mismatch"}:
        return ReadinessFact("stale", category)
    if category in {"schema_mismatch", "rebuild_required", "indexing_contract_changed",
                    "repository_binding_mismatch"}:
        return ReadinessFact("incompatible", category)
    if category in {"unavailable", "timeout", "connection_failure", "embedding_failed"}:
        return ReadinessFact("unavailable", category)
    return ReadinessFact("unknown", category)


def _from_doctor(status: str, category: str | None) -> ReadinessFact:
    if status == "ok":
        return ReadinessFact("ready")
    if status == "not_required":
        return ReadinessFact("not_required")
    if status == "failed":
        return _failed(category or "inspection_failed")
    return ReadinessFact("unknown", category)


def inspect_readiness(inspector: RuntimeInspectionService) -> ReadinessReport:
    """Inspect explicit configured repository/mode without writes or search.

    Versioned facts are derived from doctor, persisted snapshots, and existing
    registered-index status semantics. Unsupported checks remain unknown rather
    than being invented from HTTP process liveness.
    """
    config = inspector.config
    mode = (config.retrieval.mode if config.retrieval is not None else
            "dense" if config.backend == "qdrant-vector" else "lexical")
    report = inspector.doctor()
    doctor = {c.name: c for c in report.checks}
    def check(name: str) -> ReadinessFact:
        item = doctor.get(name)
        return (_from_doctor(item.status, item.category) if item is not None
                else ReadinessFact("unknown", "not_checked"))
    connectivity = check("backend_connectivity")
    schema = check("schema_compatibility")
    translation = check("retrieval_compatibility")
    if translation.state == "unknown" and config.retrieval is None:
        # No explicit policy: the backend's normal default mode applies.
        translation = check("backend_resolution")
    checks = {
        "service": connectivity,
        "schema": schema,
        "snapshot": ReadinessFact("unknown", "not_checked"),
        "currentness": ReadinessFact("unknown", "not_checked"),
        "indexing_fingerprint": ReadinessFact("unknown", "not_checked"),
        "embedding": (ReadinessFact("not_required") if mode == "lexical"
                      else check("embedding_provider")),
        "retrieval": translation,
    }
    fingerprints: dict[str, str | None] = {}
    indexed_revision: str | None = None
    observations: dict[str, object] = {}

    if config.repository is None:
        checks["snapshot"] = ReadinessFact("unknown", "repository_unbound")
        checks["currentness"] = ReadinessFact("unknown", "repository_unbound")
        checks["indexing_fingerprint"] = ReadinessFact("unknown", "repository_unbound")
        return ReadinessReport(
            config.backend, config.repository, config.revision, None,
            mode, checks, fingerprints,
        )

    if (connectivity.state != "ready"
            or schema.state in {"incompatible", "unavailable", "absent"}
            or (mode in {"dense", "hybrid"} and checks["embedding"].state != "ready")):
        checks["snapshot"] = ReadinessFact("unknown", "probe_blocked")
        return ReadinessReport(
            config.backend, config.repository, config.revision, None,
            mode, checks, fingerprints,
        )

    # Local scanning has no persisted repository snapshot.
    if config.backend == "local-semantic":
        checks["snapshot"] = ReadinessFact("not_required")
        checks["currentness"] = ReadinessFact("not_required")
        checks["indexing_fingerprint"] = ReadinessFact("not_required")
        return ReadinessReport(
            config.backend, config.repository, config.revision, None,
            mode, checks, fingerprints,
        )
    if config.backend == "mongodb-search":
        from fetchcore.application.index_operations import prepare_repository_index_runtime
        from fetchcore.adapters.retrieval.mongodb.indexing import (
            MongoDBIndexStore, MongoDBOperationalIndex,
        )
        from fetchcore.adapters.retrieval.mongodb.settings import MongoDBSettings
        from fetchcore.adapters.retrieval.mongodb.transport import PyMongoTransport
        from fetchcore.retrieval.embedding import validate_vector_dimensions

        mongo_transport = None
        try:
            mongo_settings = MongoDBSettings.from_mapping(config.backend_settings)
            mongo_transport = PyMongoTransport(mongo_settings)
            mongo_provider = None
            if mode in {"dense", "hybrid"} and config.retrieval is not None:
                requested = config.retrieval.dense.embedding
                if requested.dimensions not in {None, mongo_settings.vector_dimensions}:
                    raise KnowledgeSearchError("MongoDB dimensions mismatch", category="schema_mismatch")
                mongo_provider = inspector.providers.create(
                    requested, backend_id=config.backend, backend_name="MongoDB",
                )
            mongo_store = MongoDBIndexStore(
                mongo_transport, mongo_settings, embedding_provider=mongo_provider,
            )
            mongo_store.validate_schema(require_vectors=mode in {"dense", "hybrid"})
            mongo_snapshot = mongo_store.load_snapshot(config.repository)
            indexed_revision = mongo_snapshot.revision
            runtime_mongo = prepare_repository_index_runtime(
                config.workspace, repository=config.repository, branch=None, revision=config.revision,
            )
            status_mongo = MongoDBOperationalIndex(mongo_store, runtime_mongo).run("status")
            checks["snapshot"] = ReadinessFact("ready")
            checks["indexing_fingerprint"] = (
                ReadinessFact("ready") if mongo_snapshot.indexing_fingerprint == runtime_mongo.fingerprint
                else ReadinessFact("incompatible", "indexing_contract_changed")
            )
            checks["currentness"] = (
                ReadinessFact("ready") if status_mongo["currentness"] == "current"
                else ReadinessFact("stale", "changes_pending")
            )
            if config.revision is None:
                checks["currentness"] = ReadinessFact("unknown", "revision_unbound")
            fingerprints.update(
                projection=mongo_snapshot.projection_fingerprint,
                indexing=mongo_snapshot.indexing_fingerprint,
                expected_indexing=runtime_mongo.fingerprint,
            )
            if mongo_provider is not None:
                if not mongo_store.embedding_snapshot_matches(mongo_snapshot):
                    checks["embedding"] = ReadinessFact("incompatible", "embedding_identity_changed")
                else:
                    vectors = mongo_provider.embed(("fetchcore readiness",))
                    checks["embedding"] = (
                        ReadinessFact("ready")
                        if len(vectors) == 1 and validate_vector_dimensions(vectors) == mongo_settings.vector_dimensions
                        else ReadinessFact("incompatible", "vector_dimensions_mismatch")
                    )
            observations["search_consistency"] = "eventual"
        except (KnowledgeSearchError, OSError, ValueError) as exc:
            checks["snapshot"] = _failed(_category(exc))
        finally:
            if mongo_transport is not None:
                mongo_transport.close()
        return ReadinessReport(
            config.backend, config.repository, config.revision, indexed_revision,
            mode, checks, fingerprints, observations,
        )

    try:
        from fetchcore.adapters.retrieval.solr.backend import (
            SolrIndexStore, SolrIndexSnapshot, SolrSettings, UrllibSolrTransport,
            SOLR_VECTOR_DIMENSIONS,
        )
        from fetchcore.adapters.retrieval.opensearch.backend import (
            OpenSearchIndexStore, OpenSearchIndexSnapshot, OpenSearchSettings,
            UrllibOpenSearchTransport, OPENSEARCH_VECTOR_DIMENSIONS,
        )
        from fetchcore.adapters.retrieval.qdrant.backend import (
            QdrantVectorIndexStore, QdrantVectorSettings, QdrantVectorSnapshot,
            UrllibQdrantTransport,
        )
        provider = None
        if mode in {"dense", "hybrid"}:
            selected = (
                config.retrieval.dense.embedding
                if config.retrieval is not None
                else EmbeddingConfig(
                    model=str(config.backend_settings.get("embedding_model", EmbeddingConfig.model)),
                )
            )
            if config.backend in {"solr", "opensearch"}:
                expected_dimensions = (
                    SOLR_VECTOR_DIMENSIONS if config.backend == "solr"
                    else OPENSEARCH_VECTOR_DIMENSIONS
                )
                if selected.dimensions not in {None, expected_dimensions}:
                    checks["embedding"] = ReadinessFact(
                        "incompatible", "vector_dimensions_mismatch"
                    )
                    checks["snapshot"] = ReadinessFact("unknown", "probe_blocked")
                    return ReadinessReport(
                        config.backend, config.repository, config.revision, None,
                        mode, checks, fingerprints,
                    )
            provider = inspector.providers.create(
                selected, backend_id=config.backend, backend_name=config.backend,
            )
        store: SolrIndexStore | OpenSearchIndexStore | QdrantVectorIndexStore
        if config.backend == "solr":
            # Read the canonical indexing fingerprint from the bound store.
            # The legacy SolrIndexSnapshot silently discards the canonical payload.
            from fetchcore.application.indexing import build_index_context
            from fetchcore.application.index_operations import repository_index_status
            from fetchcore.adapters.retrieval.solr.index_contract import (
                bind_indexing_contract_store, snapshot_indexing_fingerprint,
            )
            index = build_index_context(
                workspace=config.workspace, repository=config.repository,
                branch=None, revision=config.revision, backend="solr",
                backend_settings=dict(config.backend_settings),
            )
            if provider is None:
                store = bind_indexing_contract_store(cast(SolrIndexStore, index.store), index.runtime.indexer)
            else:
                settings = SolrSettings.from_mapping(dict(config.backend_settings))
                vector_store = SolrIndexStore(
                    UrllibSolrTransport(settings), settings, embedding_provider=provider,
                )
                store = bind_indexing_contract_store(vector_store, index.runtime.indexer)
        elif config.backend == "opensearch":
            os_settings = OpenSearchSettings.from_mapping(dict(config.backend_settings))
            store = OpenSearchIndexStore(
                UrllibOpenSearchTransport(os_settings), os_settings,
                clock=lambda: "", embedding_provider=provider,
            )
        elif config.backend == "qdrant-vector" and provider is not None:
            q_settings = QdrantVectorSettings.from_mapping(dict(config.backend_settings))
            store = QdrantVectorIndexStore(
                UrllibQdrantTransport(q_settings), q_settings, provider, clock=lambda: "",
            )
        else:
            checks["snapshot"] = ReadinessFact("unsupported", "unsupported_backend")
            return ReadinessReport(
                config.backend, config.repository, config.revision, None,
                mode, checks, fingerprints,
            )
        snapshot = store.load_snapshot(config.repository)
        if config.backend == "solr" and snapshot.repository != config.repository:
            raise KnowledgeSearchError(
                "persisted snapshot repository does not match serving binding",
                category="repository_binding_mismatch",
            )
        indexed_revision = snapshot.revision
        fingerprints["projection"] = snapshot.projection_fingerprint
        fingerprints["embedding_serializer"] = snapshot.embedding_serializer_fingerprint if hasattr(
            snapshot, "embedding_serializer_fingerprint"
        ) else getattr(snapshot, "serializer_fingerprint", None)
        if config.backend == "solr":
            # The runtime branch always composes a Solr-backed, contract-bound store.
            solr_store = cast(SolrIndexStore, store)
            solr_snapshot = cast(SolrIndexSnapshot, snapshot)
            status = repository_index_status(solr_store, index.runtime)
            discovery = status.get("discovery")
            observed_currentness: dict[str, object] = {}
            if isinstance(discovery, dict):
                observed_currentness = {
                    key: discovery[key] for key in (
                        "mode", "reason", "changed_paths", "removed_paths",
                        "targeted_paths", "changed_path_values", "removed_path_values",
                        "path_sample_limit", "path_samples_truncated",
                    ) if key in discovery
                }
            observed_currentness["status_reason"] = status.get("reason")
            observations["currentness"] = observed_currentness
            expected = index.runtime.fingerprint
            actual = snapshot_indexing_fingerprint(solr_snapshot)
            fingerprints["indexing"] = actual
            fingerprints["expected_indexing"] = expected
            checks["indexing_fingerprint"] = (
                ReadinessFact("ready") if actual == expected
                else ReadinessFact("incompatible", "indexing_contract_changed")
            )
            if status["state"] == "rebuild_required":
                checks["indexing_fingerprint"] = ReadinessFact(
                    "incompatible", str(status.get("reason", "rebuild_required")),
                )
            if config.revision is not None and snapshot.revision != config.revision:
                checks["currentness"] = ReadinessFact("stale", "snapshot_revision_mismatch")
            elif status["state"] == "ready" and config.revision is not None:
                checks["currentness"] = ReadinessFact("ready")
            elif status["state"] == "changes_pending":
                checks["currentness"] = ReadinessFact("stale", "changes_pending")
            else:
                checks["currentness"] = ReadinessFact("unknown", "revision_unbound")
            if mode in {"dense", "hybrid"}:
                if not solr_store.embedding_snapshot_matches(solr_snapshot):
                    checks["embedding"] = ReadinessFact("incompatible", "embedding_identity_changed")
                elif provider is None:
                    checks["embedding"] = ReadinessFact("unavailable", "embedding_provider_missing")
                else:
                    # A provider object alone is not a live provider. A bounded explicit
                    # readiness check verifies reachability and live vector dimensions.
                    from fetchcore.retrieval.embedding import validate_vector_dimensions
                    dimensions = validate_vector_dimensions(provider.embed(("fetchcore readiness",)))
                    checks["embedding"] = (
                        ReadinessFact("ready") if dimensions == SOLR_VECTOR_DIMENSIONS
                        else ReadinessFact("incompatible", "vector_dimensions_mismatch")
                    )
        elif config.backend == "opensearch":
            from fetchcore.application.index_operations import prepare_repository_index_runtime
            from fetchcore.adapters.retrieval.opensearch.operational import OpenSearchOperationalIndex
            os_store = cast(OpenSearchIndexStore, store)
            os_snapshot = cast(OpenSearchIndexSnapshot, snapshot)
            runtime = prepare_repository_index_runtime(
                config.workspace, repository=config.repository, branch=None,
                revision=config.revision,
            )
            observation = OpenSearchOperationalIndex(os_store, runtime).observe()
            checks["schema"] = (
                ReadinessFact("ready") if observation.schema_compatible
                else ReadinessFact("incompatible", "schema_mismatch")
            )
            fingerprints["indexing"] = os_snapshot.indexing_fingerprint
            fingerprints["expected_indexing"] = runtime.fingerprint
            checks["indexing_fingerprint"] = (
                ReadinessFact("ready")
                if os_snapshot.indexing_fingerprint == runtime.fingerprint
                else ReadinessFact("incompatible", "indexing_contract_changed")
            )
            checks["currentness"] = ReadinessFact(
                "ready" if observation.currentness == "current" else cast(ReadinessState, observation.currentness),
                None if observation.currentness == "current" else observation.reason,
            )
            if mode in {"dense", "hybrid"}:
                if not os_store.embedding_snapshot_matches(os_snapshot):
                    checks["embedding"] = ReadinessFact("incompatible", "embedding_identity_changed")
                elif provider is None:
                    checks["embedding"] = ReadinessFact("unavailable", "embedding_provider_missing")
                else:
                    from fetchcore.retrieval.embedding import validate_vector_dimensions
                    dimensions = validate_vector_dimensions(provider.embed(("fetchcore readiness",)))
                    checks["embedding"] = (
                        ReadinessFact("ready") if dimensions == OPENSEARCH_VECTOR_DIMENSIONS
                        else ReadinessFact("incompatible", "vector_dimensions_mismatch")
                    )
        else:
            from fetchcore.application.index_operations import prepare_repository_index_runtime
            from fetchcore.adapters.retrieval.qdrant.operational import QdrantOperationalIndex
            from fetchcore.retrieval.embedding import validate_vector_dimensions
            q_store = cast(QdrantVectorIndexStore, store)
            q_snapshot = cast(QdrantVectorSnapshot, snapshot)
            runtime = prepare_repository_index_runtime(
                config.workspace, repository=config.repository, branch=None,
                revision=config.revision,
            )
            q_observation = QdrantOperationalIndex(q_store, runtime).observe()
            checks["schema"] = (
                ReadinessFact("ready") if q_observation.schema_compatible
                else ReadinessFact("incompatible", "schema_mismatch")
            )
            fingerprints["indexing"] = q_snapshot.indexing_fingerprint
            fingerprints["expected_indexing"] = runtime.fingerprint
            checks["indexing_fingerprint"] = (
                ReadinessFact("ready")
                if q_snapshot.indexing_fingerprint == runtime.fingerprint
                else ReadinessFact("incompatible", "indexing_contract_changed")
            )
            checks["currentness"] = ReadinessFact(
                "ready" if q_observation.currentness == "current"
                else cast(ReadinessState, q_observation.currentness),
                None if q_observation.currentness == "current" else q_observation.reason,
            )
            if mode in {"dense", "hybrid"}:
                if selected.dimensions not in {None, q_snapshot.vector_dimensions}:
                    checks["embedding"] = ReadinessFact(
                        "incompatible", "vector_dimensions_mismatch",
                    )
                elif not q_store.embedding_snapshot_matches(q_snapshot):
                    checks["embedding"] = ReadinessFact(
                        "incompatible", "embedding_identity_changed",
                    )
                elif provider is None:
                    checks["embedding"] = ReadinessFact(
                        "unavailable", "embedding_provider_missing",
                    )
                else:
                    dimensions = validate_vector_dimensions(provider.embed(("fetchcore readiness",)))
                    checks["embedding"] = (
                        ReadinessFact("ready") if dimensions == q_snapshot.vector_dimensions
                        else ReadinessFact("incompatible", "vector_dimensions_mismatch")
                    )
        checks["snapshot"] = ReadinessFact("ready")
    except (KnowledgeSearchError, OSError, ValueError) as exc:
        category = _category(exc)
        failure = _failed(category)
        if category in {"snapshot_missing", "index_missing"}:
            checks["snapshot"] = ReadinessFact("absent", "snapshot_missing")
            checks["currentness"] = ReadinessFact("absent", "snapshot_missing")
        elif category in {"schema_mismatch", "index_stale", "response_parsing_failure"}:
            checks["snapshot"] = ReadinessFact("incompatible", category)
        elif checks["snapshot"].state == "unknown":
            checks["snapshot"] = failure
        if category in {"embedding_failed", "timeout", "unavailable"} and mode != "lexical":
            checks["embedding"] = _failed(category)

    return ReadinessReport(
        config.backend, config.repository, config.revision, indexed_revision,
        mode, checks, fingerprints, observations,
    )
