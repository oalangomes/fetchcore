"""Read-only, backend-neutral composition of existing operational observations.

A healthy process is not evidence that a repository snapshot is current, and
readiness is never a judgement of retrieval quality or consumer satisfaction.
This module does not write, index, migrate, or implicitly repair projections.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.config import EmbeddingConfig

if TYPE_CHECKING:
    from fetchcore.application.runtime_inspection import RuntimeInspectionService

ReadinessState = Literal[
    "ready", "absent", "stale", "incompatible", "unavailable",
    "unsupported", "unknown", "not_required",
]

@dataclass(frozen=True)
class ReadinessFact:
    state: ReadinessState
    category: str | None = None

    def to_mapping(self) -> dict[str, str]:
        data = {"state": self.state}
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

    @property
    def ready(self) -> bool:
        return all(f.state in {"ready", "not_required"} for f in self.checks.values())

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
    if category in {"schema_mismatch", "rebuild_required", "indexing_contract_changed"}:
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
    mode = (config.retrieval.mode() if config.retrieval is not None else
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

    if config.repository is None:
        checks["snapshot"] = ReadinessFact("unknown", "repository_unbound")
        checks["currentness"] = ReadinessFact("unknown", "repository_unbound")
        checks["indexing_fingerprint"] = ReadinessFact("unknown", "repository_unbound")
        return ReadinessReport(
            config.backend, config.repository, config.revision, None,
            mode, checks, fingerprints,
        )

    if connectivity.state != "ready" or schema.state in {"incompatible", "unavailable", "absent"}:
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

    try:
        from fetchcore.adapters.retrieval.solr.backend import (
            SolrIndexStore, SolrSettings, UrllibSolrTransport,
            SOLR_VECTOR_DIMENSIONS,
        )
        from fetchcore.adapters.retrieval.opensearch.backend import (
            OpenSearchIndexStore, OpenSearchSettings, UrllibOpenSearchTransport,
        )
        from fetchcore.adapters.retrieval.qdrant.backend import (
            QdrantVectorIndexStore, QdrantVectorSettings, UrllibQdrantTransport,
        )
        provider = None
        if mode in {"dense", "hybrid"}:
            selected = (config.retrieval.dense.embedding
                        if config.retrieval is not None else EmbeddingConfig())
            provider = inspector.providers.create(
                selected, backend_id=config.backend, backend_name=config.backend,
            )
        if config.backend == "solr":
            settings = SolrSettings.from_mapping(dict(config.backend_settings))
            store = SolrIndexStore(
                UrllibSolrTransport(settings), settings, embedding_provider=provider,
            )
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
        indexed_revision = snapshot.revision
        fingerprints["projection"] = snapshot.projection_fingerprint
        fingerprints["embedding_serializer"] = snapshot.embedding_serializer_fingerprint if hasattr(
            snapshot, "embedding_serializer_fingerprint"
        ) else getattr(snapshot, "serializer_fingerprint", None)
        if config.backend == "solr":
            # Reuse the exact indexing contract and cheap git-delta status.
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
            bound = bind_indexing_contract_store(index.store, index.runtime.indexer)
            status = repository_index_status(bound, index.runtime)
            expected = index.runtime.fingerprint
            actual = snapshot_indexing_fingerprint(snapshot)
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
            if status["state"] == "ready" and config.revision is not None:
                checks["currentness"] = ReadinessFact("ready")
            elif config.revision is not None and snapshot.revision != config.revision:
                checks["currentness"] = ReadinessFact("stale", "snapshot_revision_mismatch")
            elif status["state"] == "changes_pending":
                checks["currentness"] = ReadinessFact("stale", "changes_pending")
            else:
                checks["currentness"] = ReadinessFact("unknown", "revision_unbound")
            if mode in {"dense", "hybrid"}:
                if not store.embedding_snapshot_matches(snapshot):
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
        else:
            # Operational indexing contract for these adapters is #123.
            # A compatible snapshot doesn't independently prove source/indexing
            # fingerprint or working tree currentness.
            checks["indexing_fingerprint"] = ReadinessFact("unknown", "indexing_contract_unavailable")
            checks["currentness"] = (
                ReadinessFact("stale", "snapshot_revision_mismatch")
                if config.revision is not None and indexed_revision != config.revision
                else ReadinessFact("unknown", "source_currentness_unverified")
            )
            if mode in {"dense", "hybrid"} and provider is not None:
                checks["embedding"] = ReadinessFact("unknown", "vector_readiness_unverified")
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
        mode, checks, fingerprints,
    )
