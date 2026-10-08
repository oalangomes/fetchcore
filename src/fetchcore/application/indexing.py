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
from typing import Literal

from fetchcore.application.index_operations import (
    RepositoryIndexRuntime, dry_run_repository_index, prepare_repository_index_runtime,
    repository_index_status,
)
from fetchcore.application.repositories import RepositoryApplicationService
from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore, SolrIndexSyncResult, SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import (
    bind_indexing_contract_store, snapshot_indexing_fingerprint,
)
from fetchcore.adapters.retrieval.solr.incremental_persistence import (
    TargetedGitAwareSolrSynchronizer,
)
from fetchcore.repository.sources import LocalWorkspaceSourceProvider
from fetchcore.repository.registry import RepositoryRegistryError
from fetchcore.retrieval.contracts import KnowledgeSearchError


@dataclass(frozen=True)
class IndexingCapabilities:
    incremental: bool
    rebuild: bool


def indexing_capabilities(backend: str) -> IndexingCapabilities:
    # Retrieval capability declarations do not imply operational persistence support.
    if backend == "solr":
        return IndexingCapabilities(incremental=True, rebuild=True)
    raise KnowledgeSearchError(
        "Unsupported operational indexing backend", category="unsupported_backend",
    )


@dataclass(frozen=True)
class IndexContext:
    workspace: Path
    backend: str
    runtime: RepositoryIndexRuntime
    store: SolrIndexStore

    def legacy(self, operation: str) -> dict[str, object]:
        if operation == "bootstrap":
            self.store.validate_schema(require_vectors=False)
            return {
                "schema": "fetchcore.solr.projection-v3",
                "projection_version": self.store.settings.projection_version,
                "validated": True,
            }
        if operation == "status":
            return repository_index_status(self.store, self.runtime)
        if operation == "dry-run":
            return dry_run_repository_index(self.store, self.runtime)
        if operation == "sync":
            return TargetedGitAwareSolrSynchronizer(
                self.store, self.runtime.indexer,
            ).sync(self.workspace).to_mapping()
        raise ValueError("Unsupported legacy indexing operation")


def build_index_context(
    *, workspace: Path, repository: str, branch: str | None, revision: str | None,
    backend: str, backend_settings: dict[str, object],
) -> IndexContext:
    indexing_capabilities(backend)
    root = workspace.expanduser().resolve(strict=True)
    runtime = prepare_repository_index_runtime(
        root, repository=repository.strip(), branch=branch or None, revision=revision or None,
    )
    settings = SolrSettings.from_mapping(backend_settings)
    return IndexContext(root, backend, runtime, SolrIndexStore(UrllibSolrTransport(settings), settings))


@dataclass(frozen=True)
class IndexObservation:
    mode: Literal["initial", "incremental", "noop", "rebuild_required"]
    currentness: Literal["current", "stale", "absent", "incompatible", "unknown"]
    reason: str
    indexed_revision: str | None = None
    schema_compatible: bool = True
    projection_compatible: bool | None = None


def _observe(context: IndexContext) -> IndexObservation:
    store, runtime = context.store, context.runtime
    try:
        store.validate_schema(require_vectors=False)
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
            return IndexObservation(
                "rebuild_required", "incompatible", exc.category,
                projection_compatible=False if exc.category == "schema_mismatch" else None,
            )
        raise
    if snapshot_indexing_fingerprint(snapshot) != runtime.fingerprint:
        return IndexObservation("rebuild_required", "incompatible", "indexing_contract_changed",
                                snapshot.revision, projection_compatible=True)
    if not store.embedding_snapshot_matches(snapshot):
        return IndexObservation("rebuild_required", "incompatible", "embedding_identity_changed",
                                snapshot.revision, projection_compatible=True)
    status = repository_index_status(store, runtime)
    currentness: Literal["current", "stale", "unknown"] = (
        "current" if status["state"] == "ready" else
        "unknown" if runtime.revision is None else "stale"
    )
    return IndexObservation(
        "noop" if status["state"] == "ready" else "incremental",
        currentness, str(status["reason"]), snapshot.revision, projection_compatible=True,
    )


class RepositoryIndexApplicationService:
    def __init__(self, repositories: RepositoryApplicationService, *, backend: str = "solr",
                 backend_settings: Mapping[str, object] | None = None) -> None:
        self.repositories = repositories
        self.backend = backend
        self.backend_settings = dict(backend_settings or {})

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
            )
            store = bind_indexing_contract_store(context.store, context.runtime.indexer)
            context = IndexContext(context.workspace, context.backend, context.runtime, store)
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
            }
            if operation == "status":
                return result
            if operation == "plan":
                if observation.mode != "rebuild_required":
                    preview = dry_run_repository_index(store, context.runtime)
                    # Only expose runtime-proven quantities. Full-scan path counts are
                    # discovery placeholders, not measured changed/deleted file counts.
                    for key in ("planned_mutations", "discovery", "indexing_telemetry"):
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
            else:
                # Validate live schema even for explicit rebuild; never repair it here.
                store.validate_schema(require_vectors=False)
                outcome = _execution_result(store.full_rebuild(
                    context.runtime.indexer.index(context.workspace),
                ))
            result["execution"] = outcome
            result["mode"] = "rebuild" if operation == "rebuild" else (
                "noop" if outcome.get("noop") else "incremental"
            )
            # Pre-execution observations are retained explicitly; status is a separate read.
            result["previous_indexed_revision"] = result.pop("indexed_revision")
            result["previous_currentness"] = result.pop("currentness")
            result["reason"] = "explicit_rebuild" if operation == "rebuild" else observation.reason
            return result


def _execution_result(storage: SolrIndexSyncResult) -> dict[str, object]:
    """Translate persisted adapter output without exposing its native snapshot record."""
    snapshot = storage.snapshot
    result = storage.to_mapping()
    result["snapshot"] = {
        "repository": snapshot.repository, "branch": snapshot.branch,
        "revision": snapshot.revision, "document_count": snapshot.document_count,
        "indexed_at": snapshot.indexed_at, "status": snapshot.status,
        "projection_fingerprint": snapshot.projection_fingerprint,
        "indexing_fingerprint": snapshot_indexing_fingerprint(snapshot),
        "embedding_provider": snapshot.embedding_provider,
        "embedding_model": snapshot.embedding_model,
        "vector_dimensions": snapshot.vector_dimensions,
        "serializer_fingerprint": snapshot.embedding_serializer_fingerprint,
    }
    return result
