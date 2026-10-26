"""Operational indexing composition, including the legacy CI and registered surfaces.

This is the single backend-selection boundary for indexing commands. Repository
identity belongs to RepositoryApplicationService; delta and persistence belong to
the existing indexing runtime and adapter.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from datetime import UTC, datetime
import time

from fetchcore.application.composition import EmbeddingProviderRegistry
from fetchcore.application.index_store import OperationalIndexDriver, OperationalIndexStore
from fetchcore.application.index_operations import (
    RepositoryIndexRuntime, dry_run_repository_index, prepare_repository_index_runtime,
    repository_index_status,
)
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.adapters.retrieval.solr.backend import (
    SOLR_VECTOR_DIMENSIONS,
    SolrIndexStore,
    SolrIndexSyncResult,
    SolrSettings,
    UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import (
    bind_indexing_contract_store, snapshot_indexing_fingerprint,
)
from fetchcore.adapters.retrieval.solr.incremental_persistence import (
    TargetedGitAwareSolrSynchronizer,
)
from fetchcore.adapters.retrieval.opensearch.backend import (
    OPENSEARCH_VECTOR_DIMENSIONS, OpenSearchIndexStore, OpenSearchSettings,
    UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.opensearch.operational import OpenSearchOperationalIndex
from fetchcore.adapters.retrieval.mongodb.indexing import MongoDBIndexStore, MongoDBOperationalIndex
from fetchcore.adapters.retrieval.mongodb.settings import MongoDBSettings
from fetchcore.adapters.retrieval.mongodb.transport import PyMongoTransport
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorBackend,
    QdrantVectorIndexStore,
    QdrantVectorSettings,
    UrllibQdrantTransport,
)
from fetchcore.adapters.retrieval.qdrant.operational import QdrantOperationalIndex
from fetchcore.repository.sources import LocalWorkspaceSourceProvider
from fetchcore.repository.registry import RepositoryRegistryError
from fetchcore.retrieval.config import EmbeddingConfig, RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.telemetry import IndexStageTelemetry, elapsed_ms
from fetchcore.retrieval.embedding import (
    VECTOR_EMBEDDING_SERIALIZATION_VERSION,
    validate_vector_dimensions,
)


_EMBEDDING_PROBE_TEXT = "fetchcore embedding probe"


@dataclass(frozen=True)
class IndexingCapabilities:
    incremental: bool
    rebuild: bool


def indexing_capabilities(backend: str) -> IndexingCapabilities:
    # Retrieval capability declarations do not imply operational persistence support.
    if backend == "solr":
        return IndexingCapabilities(incremental=True, rebuild=True)
    if backend == "opensearch":
        return IndexingCapabilities(incremental=True, rebuild=True)
    if backend == "qdrant-vector":
        return IndexingCapabilities(incremental=True, rebuild=True)
    if backend == "mongodb-search":
        return IndexingCapabilities(incremental=True, rebuild=True)
    raise KnowledgeSearchError(
        "Unsupported operational indexing backend", category="unsupported_backend",
    )


@dataclass(frozen=True)
class IndexContext:
    workspace: Path
    backend: str
    runtime: RepositoryIndexRuntime
    store: OperationalIndexStore
    retrieval: RetrievalConfig | None = None
    driver: OperationalIndexDriver | None = None

    @property
    def retrieval_mode(self) -> str:
        if self.retrieval is not None:
            return self.retrieval.mode
        return "dense" if self.backend == "qdrant-vector" else "lexical"

    @property
    def require_vectors(self) -> bool:
        return self.retrieval_mode in {"dense", "hybrid"}

    def legacy(self, operation: str) -> dict[str, object]:
        if self.backend == "mongodb-search":
            assert self.driver is not None
            try:
                return self.driver.run("plan" if operation == "dry-run" else operation)
            finally:
                cast(MongoDBIndexStore, self.store).transport.close()
        if self.backend == "qdrant-vector":
            store_qdrant = cast(QdrantVectorIndexStore, self.store)
            driver_qdrant = cast(QdrantOperationalIndex, self.driver)
            if operation == "bootstrap":
                store_qdrant.validate_schema(require_vectors=True)
                dimensions = store_qdrant.settings.vector_dimensions
                if dimensions is None:
                    raise KnowledgeSearchError(
                        "Qdrant operational indexing requires configured vector_dimensions",
                        category="schema_mismatch",
                    )
                qdrant_result: dict[str, object] = {
                    "schema": "fetchcore.qdrant.projection-v3",
                    "projection_version": store_qdrant.settings.projection_version,
                    "validated": True,
                    "retrieval_mode": self.retrieval_mode,
                    "vectors_required": True,
                    "retrieval_capabilities": QdrantVectorBackend.retrieval_capabilities.to_mapping(),
                    "embedding_probe": _probe_embedding_provider(
                        store_qdrant,
                        expected_dimensions=dimensions,
                    ),
                }
                qdrant_result.update(driver_qdrant.run("status"))
                return qdrant_result
            return driver_qdrant.run("plan" if operation == "dry-run" else operation)
        if self.backend == "opensearch":
            store = cast(OpenSearchIndexStore, self.store)
            driver = OpenSearchOperationalIndex(store, self.runtime)
            if operation == "bootstrap":
                store.validate_schema(require_vectors=self.require_vectors)
                os_result: dict[str, object] = {
                    "schema": "fetchcore.opensearch.projection-v3",
                    "projection_version": store.settings.projection_version,
                    "validated": True,
                    "retrieval_mode": self.retrieval.mode if self.retrieval else "lexical",
                    "vectors_required": self.require_vectors,
                }
                if self.require_vectors:
                    os_result["embedding_probe"] = _probe_embedding_provider(
                        store, expected_dimensions=OPENSEARCH_VECTOR_DIMENSIONS,
                    )
                return os_result
            return driver.run("plan" if operation == "dry-run" else operation)
        solr = cast(SolrIndexStore, self.store)
        if operation == "bootstrap":
            solr.validate_schema(require_vectors=self.require_vectors)
            result: dict[str, object] = {
                "schema": "fetchcore.solr.projection-v3",
                "projection_version": solr.settings.projection_version,
                "validated": True,
                "retrieval_mode": self.retrieval.mode if self.retrieval else "lexical",
                "vectors_required": self.require_vectors,
            }
            if self.require_vectors:
                result["embedding_probe"] = _probe_embedding_provider(solr)
            return result
        if operation == "status":
            return repository_index_status(solr, self.runtime)
        if operation == "dry-run":
            return dry_run_repository_index(solr, self.runtime)
        if operation == "sync":
            return TargetedGitAwareSolrSynchronizer(
                solr, self.runtime.indexer,
            ).sync(self.workspace).to_mapping()
        raise ValueError("Unsupported legacy indexing operation")


def build_index_context(
    *, workspace: Path, repository: str, branch: str | None, revision: str | None,
    backend: str, backend_settings: dict[str, object],
    retrieval: RetrievalConfig | None = None,
    providers: EmbeddingProviderRegistry | None = None,
) -> IndexContext:
    indexing_capabilities(backend)
    root = workspace.expanduser().resolve(strict=True)
    runtime = prepare_repository_index_runtime(
        root, repository=repository.strip(), branch=branch or None, revision=revision or None,
    )
    if backend == "mongodb-search":
        mongo_settings = MongoDBSettings.from_mapping(backend_settings)
        mongo_provider = None
        if retrieval is not None and retrieval.mode in {"dense", "hybrid"}:
            if retrieval.dense.embedding.dimensions not in {None, mongo_settings.vector_dimensions}:
                raise KnowledgeSearchError(
                    "MongoDB vector dimensions mismatch", category="schema_mismatch",
                )
            mongo_provider = (providers or EmbeddingProviderRegistry()).create(
                retrieval.dense.embedding, backend_id=backend, backend_name="MongoDB",
            )
        mongo_store = MongoDBIndexStore(
            PyMongoTransport(mongo_settings), mongo_settings, embedding_provider=mongo_provider,
        )
        return IndexContext(
            root, backend, runtime, mongo_store, retrieval,
            MongoDBOperationalIndex(mongo_store, runtime),
        )
    if backend == "qdrant-vector":
        settings_data = dict(backend_settings)
        configured_settings = QdrantVectorSettings.from_mapping(settings_data)
        if retrieval is not None:
            if retrieval.mode not in {"dense", "hybrid"} or not retrieval.dense.enabled:
                raise KnowledgeSearchError(
                    "Qdrant operational indexing requires dense or hybrid retrieval capability",
                    category="unsupported_capability",
                    metadata={"backend": backend, "requested_mode": retrieval.mode},
                )
            embedding = retrieval.dense.embedding
        else:
            embedding = EmbeddingConfig(model=configured_settings.embedding_model)
        configured_dimensions = configured_settings.vector_dimensions
        requested_dimensions = embedding.dimensions
        if (
            configured_dimensions is not None
            and requested_dimensions is not None
            and configured_dimensions != requested_dimensions
        ):
            raise KnowledgeSearchError(
                "configured embedding dimensions do not match the Qdrant vector schema",
                category="schema_mismatch",
                metadata={
                    "configured_dimensions": configured_dimensions,
                    "requested_dimensions": requested_dimensions,
                    "rebuild_required": True,
                },
            )
        vector_dimensions = configured_dimensions or requested_dimensions
        if vector_dimensions is None:
            raise KnowledgeSearchError(
                "Qdrant operational indexing requires explicit vector_dimensions",
                category="schema_mismatch",
                metadata={"rebuild_required": True},
            )
        settings_data.update(
            embedding_model=embedding.model,
            vector_dimensions=vector_dimensions,
        )
        settings_qdrant = QdrantVectorSettings.from_mapping(settings_data)
        registry = providers or EmbeddingProviderRegistry(
            settings={"ollama": {"url": settings_qdrant.embedding_url}},
        )
        embedder = registry.create(
            embedding,
            backend_id=backend,
            backend_name="Qdrant",
        )
        store_qdrant = QdrantVectorIndexStore(
            UrllibQdrantTransport(settings_qdrant),
            settings_qdrant,
            embedder,
            clock=lambda: datetime.now(UTC).isoformat(),
        )
        store_qdrant.repository = runtime.repository
        store_qdrant.indexing_fingerprint = runtime.fingerprint
        return IndexContext(
            root,
            backend,
            runtime,
            store_qdrant,
            retrieval,
            QdrantOperationalIndex(store_qdrant, runtime),
        )
    if backend == "opensearch":
        settings_os = OpenSearchSettings.from_mapping(backend_settings)
        provider_os = None
        if retrieval is not None and retrieval.mode in {"dense", "hybrid"}:
            configured_dimensions = retrieval.dense.embedding.dimensions
            if configured_dimensions not in {None, OPENSEARCH_VECTOR_DIMENSIONS}:
                raise KnowledgeSearchError(
                    "configured embedding dimensions do not match OpenSearch vector mapping",
                    category="schema_mismatch",
                    metadata={"expected_dimensions": OPENSEARCH_VECTOR_DIMENSIONS,
                              "actual_dimensions": configured_dimensions,
                              "rebuild_required": True},
                )
            provider_os = (providers or EmbeddingProviderRegistry()).create(
                retrieval.dense.embedding, backend_id="opensearch", backend_name="OpenSearch",
            )
        store_os = OpenSearchIndexStore(
            UrllibOpenSearchTransport(settings_os), settings_os,
            clock=lambda: datetime.now(UTC).isoformat(), embedding_provider=provider_os,
        )
        store_os.repository = runtime.repository
        store_os.indexing_fingerprint = runtime.fingerprint
        return IndexContext(
            root, backend, runtime, store_os, retrieval,
            OpenSearchOperationalIndex(store_os, runtime),
        )
    settings = SolrSettings.from_mapping(backend_settings)
    embedding_provider = None
    if retrieval is not None and retrieval.mode in {"dense", "hybrid"}:
        configured_dimensions = retrieval.dense.embedding.dimensions
        if configured_dimensions not in {None, SOLR_VECTOR_DIMENSIONS}:
            raise KnowledgeSearchError(
                "configured embedding dimensions do not match the frozen Solr vector schema",
                category="schema_mismatch",
                metadata={
                    "expected_dimensions": SOLR_VECTOR_DIMENSIONS,
                    "actual_dimensions": configured_dimensions,
                    "rebuild_required": True,
                },
            )
        embedding_provider = (providers or EmbeddingProviderRegistry()).create(
            retrieval.dense.embedding,
            backend_id="solr",
            backend_name="Solr",
        )
    store = SolrIndexStore(
        UrllibSolrTransport(settings),
        settings,
        embedding_provider=embedding_provider,
    )
    return IndexContext(root, backend, runtime, store, retrieval)


def _probe_embedding_provider(
    store: SolrIndexStore | OpenSearchIndexStore | QdrantVectorIndexStore,
    *,
    expected_dimensions: int = SOLR_VECTOR_DIMENSIONS,
) -> dict[str, object]:
    """Probe the configured provider once without mutating the projection."""
    provider = store.embedding_provider
    serializer = store.embedding_serializer
    if provider is None or serializer is None:
        raise KnowledgeSearchError(
            "vector indexing requires a composed embedding provider",
            category="unsupported_capability",
            metadata={"capability": "embedding_provider"},
        )
    vectors = provider.embed((_EMBEDDING_PROBE_TEXT,))
    dimensions = validate_vector_dimensions(vectors)
    if len(vectors) != 1:
        raise KnowledgeSearchError(
            "embedding probe returned an unexpected vector count",
            category="embedding_failed",
        )
    if dimensions != expected_dimensions:
        raise KnowledgeSearchError(
            "embedding probe dimensions do not match the vector schema",
            category="schema_mismatch",
            metadata={
                "expected_dimensions": expected_dimensions,
                "actual_dimensions": dimensions,
                "rebuild_required": True,
            },
        )
    return {
        "requests": 1,
        "vectors": 1,
        "provider": provider.provider_id,
        "model": provider.model_id,
        "dimensions": dimensions,
        "serializer_mode": serializer.mode.value,
        "serializer_version": VECTOR_EMBEDDING_SERIALIZATION_VERSION,
        "serializer_fingerprint": serializer.serializer_fingerprint,
        "projection_fingerprint": serializer.projection_fingerprint,
    }


@dataclass(frozen=True)
class IndexObservation:
    mode: Literal["initial", "incremental", "noop", "rebuild_required"]
    currentness: Literal["current", "stale", "absent", "incompatible", "unknown"]
    reason: str
    indexed_revision: str | None = None
    schema_compatible: bool = True
    projection_compatible: bool | None = None
    discovery: dict[str, object] | None = None
    snapshot_indexing_fingerprint: str | None = None
    snapshot_projection_fingerprint: str | None = None


def _observe(context: IndexContext) -> IndexObservation:
    store, runtime = cast(SolrIndexStore, context.store), context.runtime
    try:
        store.validate_schema(require_vectors=context.require_vectors)
    except KnowledgeSearchError as exc:
        if exc.category != "schema_mismatch":
            raise
        return IndexObservation("rebuild_required", "incompatible", "schema_mismatch",
                                schema_compatible=False)
    try:
        snapshot = store.load_snapshot(runtime.repository)
    except KnowledgeSearchError as exc:
        if exc.category == "snapshot_missing" and "snapshot_count" not in exc.metadata:
            return IndexObservation("initial", "absent", "snapshot_missing")
        if exc.category in {"snapshot_missing", "schema_mismatch", "index_stale",
                            "response_parsing_failure"}:
            actual_fingerprint = exc.metadata.get("actual_projection_fingerprint")
            return IndexObservation(
                "rebuild_required", "incompatible", exc.category,
                projection_compatible=False if exc.category == "schema_mismatch" else None,
                snapshot_projection_fingerprint=(
                    actual_fingerprint if isinstance(actual_fingerprint, str) else None
                ),
            )
        raise
    if snapshot_indexing_fingerprint(snapshot) != runtime.fingerprint:
        return IndexObservation("rebuild_required", "incompatible", "indexing_contract_changed",
                                snapshot.revision, projection_compatible=True,
                                snapshot_indexing_fingerprint=snapshot_indexing_fingerprint(snapshot),
                                snapshot_projection_fingerprint=snapshot.projection_fingerprint)
    if context.require_vectors and not store.embedding_snapshot_matches(snapshot):
        return IndexObservation("rebuild_required", "incompatible", "embedding_identity_changed",
                                snapshot.revision, projection_compatible=True,
                                snapshot_indexing_fingerprint=snapshot_indexing_fingerprint(snapshot),
                                snapshot_projection_fingerprint=snapshot.projection_fingerprint)
    status = repository_index_status(store, runtime)
    currentness: Literal["current", "stale", "unknown"] = (
        "current" if status["state"] == "ready" else
        "unknown" if runtime.revision is None or runtime.revision == snapshot.revision else "stale"
    )
    return IndexObservation(
        "noop" if status["state"] == "ready" else "incremental",
        currentness, str(status["reason"]), snapshot.revision, projection_compatible=True,
        discovery=cast(dict[str, object] | None, status.get("discovery")),
        snapshot_indexing_fingerprint=snapshot_indexing_fingerprint(snapshot),
        snapshot_projection_fingerprint=snapshot.projection_fingerprint,
    )


class RepositoryIndexApplicationService:
    def __init__(self, repositories: RepositoryApplicationService, *, backend: str = "solr",
                 backend_settings: Mapping[str, object] | None = None,
                 retrieval: RetrievalConfig | None = None,
                 providers: EmbeddingProviderRegistry | None = None) -> None:
        self.repositories = repositories
        self.backend = backend
        self.backend_settings = dict(backend_settings or {})
        self.retrieval = retrieval
        self.providers = providers

    def run(self, operation: str, reference: str) -> dict[str, object]:
        if operation not in {"plan", "status", "sync", "rebuild"}:
            raise ValueError("Unsupported indexing operation")
        capabilities = indexing_capabilities(self.backend)
        if operation == "sync" and not capabilities.incremental:
            raise KnowledgeSearchError("Incremental indexing unavailable",
                                       category="incremental_unsupported")
        if operation == "rebuild" and not capabilities.rebuild:
            raise KnowledgeSearchError("Rebuild unavailable", category="unsupported_backend")
        registered = self.repositories.inspect(reference)
        request = registered.source_request()
        with ExitStack() as stack:
            try:
                materialization = (
                    self.repositories.git.materialize(request, refresh=False)
                    if registered.provider == "git"
                    else LocalWorkspaceSourceProvider().materialize(request)
                )
                source = stack.enter_context(materialization)
            except (OSError, ValueError) as exc:
                raise RepositoryRegistryError(
                    "materialization_failure", "Registered source could not be materialized",
                ) from exc
            context = build_index_context(
                workspace=source.workspace, repository=source.repository, branch=source.branch,
                revision=source.revision, backend=self.backend, backend_settings=self.backend_settings,
                retrieval=self.retrieval, providers=self.providers,
            )
            if self.backend == "mongodb-search":
                stack.callback(cast(MongoDBIndexStore, context.store).transport.close)
            if context.driver is not None:
                return {
                    "repository": source.repository, "provider": source.provider,
                    "source": registered.source, "source_ref": source.source_ref,
                    "branch": source.branch, "registered_revision": registered.revision,
                    "target_revision": source.revision,
                    "retrieval_mode": context.retrieval_mode,
                    **context.driver.run(operation),
                }
            store = bind_indexing_contract_store(
                cast(SolrIndexStore, context.store), context.runtime.indexer,
            )
            context = IndexContext(
                context.workspace,
                context.backend,
                context.runtime,
                store,
                context.retrieval,
            )
            observation = _observe(context)
            result: dict[str, object] = {
                "repository": source.repository, "provider": source.provider,
                "source": registered.source, "source_ref": source.source_ref,
                "branch": source.branch, "registered_revision": registered.revision,
                "target_revision": source.revision, "indexed_revision": observation.indexed_revision,
                "mode": observation.mode, "currentness": observation.currentness,
                "reason": observation.reason,
                "schema_compatible": observation.schema_compatible,
                "projection_compatible": observation.projection_compatible,
                "incremental_possible": observation.mode in {"incremental", "noop"},
                "indexing_fingerprint": context.runtime.fingerprint,
                "projection_fingerprint": store.projection_fingerprint,
                "snapshot_indexing_fingerprint": observation.snapshot_indexing_fingerprint,
                "snapshot_projection_fingerprint": observation.snapshot_projection_fingerprint,
                "retrieval_mode": self.retrieval.mode if self.retrieval else "lexical",
            }
            if operation == "status":
                if observation.discovery is not None:
                    result["discovery"] = observation.discovery
                return result
            if operation == "plan":
                if observation.mode != "rebuild_required":
                    preview = dry_run_repository_index(store, context.runtime)
                    # Canonical comparison can prove no-op for already-indexed dirty
                    # content, or require a wider scan than the cheap status observed.
                    result["reason"] = preview["reason"]
                    if preview["mode"] == "full_rebuild":
                        initial = preview.get("snapshot_error") == "snapshot_missing"
                        result["mode"] = "initial" if initial else "rebuild_required"
                        result["currentness"] = "absent" if initial else "incompatible"
                        result["incremental_possible"] = False
                    else:
                        result["mode"] = "noop" if preview["noop"] else "incremental"
                        result["currentness"] = (
                            "current" if preview["noop"] else
                            "unknown" if source.revision is None else "stale"
                        )
                    # Only expose runtime-proven quantities. Full-scan path counts are
                    # discovery placeholders, not measured changed/deleted file counts.
                    for key in ("planned_mutations", "discovery", "indexing_telemetry",
                                "stage_timings_ms"):
                        if key in preview:
                            result[key] = preview[key]
                    mutations = result.get("planned_mutations")
                    if observation.mode == "initial" and isinstance(mutations, dict):
                        # An absent snapshot does not prove that no orphan records exist.
                        result["planned_mutations"] = {
                            **mutations, "deleted": None, "previous_document_count": None,
                        }
                    discovery = result.get("discovery")
                    if isinstance(discovery, dict) and discovery.get("mode") == "full-scan":
                        result["discovery"] = {
                            k: v for k, v in discovery.items()
                            if k not in {"changed_paths", "removed_paths", "targeted_paths",
                                         "changed_path_values", "removed_path_values"}
                        }
                    # Timing measurements cannot be deterministic planning facts.
                    telemetry = result.get("indexing_telemetry")
                    if isinstance(telemetry, dict):
                        result["indexing_telemetry"] = {
                            k: v for k, v in telemetry.items()
                            if not k.endswith("_ms") and "throughput" not in k
                        }
                return result
            if operation == "sync":
                if observation.mode == "initial":
                    raise KnowledgeSearchError("Index absent; call index rebuild explicitly",
                                               category="snapshot_missing")
                if observation.mode == "rebuild_required":
                    raise KnowledgeSearchError(
                        "Explicit rebuild required: " + observation.reason,
                        category="rebuild_required",
                    )
                synced = TargetedGitAwareSolrSynchronizer(
                    store, context.runtime.indexer, allow_rebuild=False,
                ).sync(context.workspace)
                outcome = _execution_result(synced.storage)
                outcome["noop"] = synced.noop
                outcome["discovery"] = synced.discovery.to_mapping()
                if synced.stage_timings_ms is not None:
                    outcome["stage_timings_ms"] = IndexStageTelemetry.from_mapping(
                        synced.stage_timings_ms,
                    ).to_mapping()
            else:
                # Validate live schema even for explicit rebuild; never repair it here.
                store.validate_schema(require_vectors=context.require_vectors)
                started = time.monotonic()
                indexed = context.runtime.indexer.index(context.workspace)
                canonical_full_index_ms = elapsed_ms(started, time.monotonic())
                started = time.monotonic()
                persisted = store.full_rebuild(indexed)
                persistence_call_ms = elapsed_ms(started, time.monotonic())
                outcome = _execution_result(persisted)
                outcome["stage_timings_ms"] = IndexStageTelemetry(
                    canonical_full_index_ms=canonical_full_index_ms,
                    persistence_call_ms=persistence_call_ms,
                ).to_mapping()
            result["execution"] = outcome
            result["mode"] = "rebuild" if operation == "rebuild" else (
                "noop" if outcome.get("noop") else "incremental"
            )
            # Pre-execution observations are retained explicitly; status is a separate read.
            result["previous_indexed_revision"] = result.pop("indexed_revision")
            result["previous_currentness"] = result.pop("currentness")
            result["reason"] = "explicit_rebuild" if operation == "rebuild" else observation.reason
            return result
        raise AssertionError("indexing operation did not return a result")


def _execution_result(storage: SolrIndexSyncResult) -> dict[str, object]:
    """Translate persisted adapter output without exposing its native snapshot record."""
    snapshot = storage.snapshot
    result = storage.to_mapping()
    result["snapshot"] = {
        "repository": snapshot.repository, "branch": snapshot.branch,
        "revision": snapshot.revision, "document_count": snapshot.document_count,
        "corpus_checksum": snapshot.corpus_checksum,
        "projection_version": snapshot.projection_version,
        "indexed_at": snapshot.indexed_at, "status": snapshot.status,
        "projection_fingerprint": snapshot.projection_fingerprint,
        "indexing_fingerprint": snapshot_indexing_fingerprint(snapshot),
        "embedding_provider": snapshot.embedding_provider,
        "embedding_model": snapshot.embedding_model,
        "vector_dimensions": snapshot.vector_dimensions,
        "serializer_mode": snapshot.embedding_serializer_mode,
        "serializer_version": snapshot.embedding_serializer_version,
        "serializer_fingerprint": snapshot.embedding_serializer_fingerprint,
    }
    return result
