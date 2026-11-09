"""Explicit backend-neutral operational reconciliation policy.

Only index/bootstrap commands call this module. Serving and retrieval remain
read-only consumers of validated snapshots.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import replace
from hashlib import sha256
import fcntl
import os
from pathlib import Path
import subprocess
import time
from typing import cast
from urllib.parse import urlparse

from fetchcore.adapters.retrieval.solr.backend import SolrIndexSnapshot, SolrIndexStore
from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store
from fetchcore.adapters.retrieval.solr.index_contract import snapshot_indexing_fingerprint
from fetchcore.adapters.retrieval.solr.reconciliation import (
    inspect_ownership, inspect_snapshot_identity, managed_schema, rebuild_with_swap,
    standalone_mode,
)
from fetchcore.adapters.retrieval.opensearch.backend import OpenSearchIndexStore
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorIndexStore
from fetchcore.application.runtime_config import ReconciliationSettings
from fetchcore.application.indexing import IndexContext, _observe, _probe_embedding_provider
from fetchcore.retrieval.contracts import KnowledgeSearchError


_ADDITIVE_SOLR_FIELDS = frozenset({
    "structural_names", "structural_qualified_names", "structural_identities",
    "structural_incoming_identities",
})


def _source_state(context: IndexContext) -> tuple[bool, str]:
    """Prove the materialized Git source is exactly the requested clean revision."""
    root = context.workspace.resolve()
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--verify", "HEAD"], cwd=root, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"], cwd=root, check=True,
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root,
            check=True, capture_output=True, text=True, timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False, "source_revision_unverifiable"
    if Path(top).resolve() != root or head != context.runtime.revision:
        return False, "source_revision_mismatch"
    if dirty:
        return False, "source_worktree_dirty"
    return True, "source_revision_verified"


@contextmanager
def _resource_lock(context: IndexContext) -> Iterator[None]:
    """Serialize local maintenance processes for one backend resource/repository."""
    store = context.store
    settings = cast(SolrIndexStore | OpenSearchIndexStore | QdrantVectorIndexStore, store).settings
    resource = getattr(settings, "collection", getattr(settings, "index", ""))
    identity = f"{context.backend}\0{settings.url}\0{resource}\0{context.runtime.repository}"
    name = sha256(identity.encode()).hexdigest()
    directory = Path.home() / ".cache" / "fetchcore" / "reconciliation-locks"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory / name, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise KnowledgeSearchError("Reconciliation is already running for this resource",
                                       category="concurrent_write") from exc
        yield
    finally:
        os.close(fd)


def _schema_differences(context: IndexContext) -> dict[str, object]:
    try:
        context.store.validate_schema(require_vectors=context.require_vectors)
    except KnowledgeSearchError as exc:
        if exc.category not in {"schema_mismatch", "index_missing"}:
            raise
        return {"category": exc.category, **exc.metadata}
    return {}


def _status(context: IndexContext) -> dict[str, object]:
    if context.driver is not None:
        result = context.driver.run("status")
    else:
        store = bind_indexing_contract_store(
            cast(SolrIndexStore, context.store), context.runtime.indexer,
        )
        bound = replace(context, store=store)
        observed = _observe(bound)
        result = {"mode": observed.mode, "currentness": observed.currentness,
                  "reason": observed.reason, "indexed_revision": observed.indexed_revision,
                  "schema_compatible": observed.schema_compatible,
                  "snapshot_indexing_fingerprint": observed.snapshot_indexing_fingerprint,
                  "snapshot_projection_fingerprint": observed.snapshot_projection_fingerprint}
    if result.get("snapshot_indexing_fingerprint") is None:
        if context.backend == "solr":
            result.update(inspect_snapshot_identity(
                cast(SolrIndexStore, context.store), context.runtime.repository,
            ))
            return result
        try:
            prior_snapshot = context.store.load_snapshot(context.runtime.repository)
        except KnowledgeSearchError as exc:
            if exc.category not in {"snapshot_missing", "schema_mismatch", "index_stale",
                                    "response_parsing_failure"}:
                raise
        else:
            actual_indexing = vars(prior_snapshot).get("indexing_fingerprint")
            if context.backend == "solr":
                actual_indexing = snapshot_indexing_fingerprint(
                    cast(SolrIndexSnapshot, prior_snapshot)
                )
            result["snapshot_indexing_fingerprint"] = actual_indexing
            result["snapshot_projection_fingerprint"] = prior_snapshot.projection_fingerprint
            result["indexed_revision"] = prior_snapshot.revision
    return result


def reconcile_index_context(
    context: IndexContext, settings: ReconciliationSettings,
) -> dict[str, object]:
    """Inspect, plan and optionally execute a safe repository projection repair."""
    started = time.monotonic()
    if context.backend == "solr":
        store = bind_indexing_contract_store(
            cast(SolrIndexStore, context.store), context.runtime.indexer,
        )
        context = replace(context, store=store)
    differences = _schema_differences(context)
    observation = _status(context)
    prior_mode = str(observation["mode"])
    prior_reason = str(observation["reason"])
    if differences.get("category") == "schema_mismatch":
        prior_mode, prior_reason = "rebuild_required", "schema_mismatch"
    elif differences.get("category") == "index_missing":
        prior_mode, prior_reason = "initial", "index_missing"
    if prior_mode == "noop" and observation.get("currentness") == "current":
        action = "none"
    elif prior_mode in {"initial", "rebuild_required"}:
        action = "rebuild"
    else:
        action = "sync"
    missing = differences.get("missing_fields")
    additive = (
        context.backend == "solr" and isinstance(missing, list) and bool(missing)
        and set(missing) <= _ADDITIVE_SOLR_FIELDS
        and not differences.get("incompatible_fields")
        and differences.get("vector_field_type") is None
    )
    solrcloud = False
    if action == "rebuild" and context.backend == "solr" and differences:
        solr_store = cast(SolrIndexStore, context.store)
        solrcloud = not standalone_mode(solr_store)
        action = "schema_upgrade_and_rebuild" if additive else "staged_rebuild"
        if solrcloud or (additive and not managed_schema(solr_store)):
            action = "staged_rebuild"
    if context.backend == "qdrant-vector" and differences.get("missing_payload_indexes") and not differences.get("incompatible_payload_indexes"):
        store_q = cast(QdrantVectorIndexStore, context.store)
        try:
            old_snapshot = store_q.load_snapshot(context.runtime.repository)
        except KnowledgeSearchError as exc:
            if exc.category not in {"snapshot_missing", "schema_mismatch", "index_stale"}:
                raise
        else:
            if (old_snapshot.indexing_fingerprint == context.runtime.fingerprint
                    and store_q.embedding_snapshot_matches(old_snapshot)
                    and old_snapshot.revision == context.runtime.revision):
                action = "add_payload_indexes"
    if settings.mode == "explicit-rebuild":
        action = "staged_rebuild" if context.backend == "solr" else "rebuild"
        if context.backend == "solr":
            solrcloud = not standalone_mode(cast(SolrIndexStore, context.store))
    blocked: str | None = None
    if action != "none" and settings.mode == "safe-auto" and not settings.managed:
        blocked = "managed_resource_authorization_required"
    source_available, source_reason = _source_state(context)
    if action != "none" and settings.mode != "inspect" and not source_available:
        blocked = source_reason
    if action == "staged_rebuild" and not settings.solr_configset:
        blocked = "compatible_server_configset_required"
    if solrcloud:
        blocked = "solrcloud_cutover_unavailable"
    if settings.mode == "safe-auto" and context.backend == "solr":
        host = urlparse(cast(SolrIndexStore, context.store).settings.url).hostname
        if host not in {"localhost", "127.0.0.1", "::1"}:
            blocked = "remote_solr_ownership_lock_unavailable"
    plan: dict[str, object] = {
        "operation": "reconcile", "backend": context.backend,
        "repository": context.runtime.repository,
        "target_revision": context.runtime.revision,
        "previous_state": prior_mode, "reason": prior_reason,
        "action": action, "execution": settings.mode,
        "schema_differences": differences,
        "source": {"available": source_available, "reason": source_reason},
        "fingerprints": {
            "expected_indexing": context.runtime.fingerprint,
            "actual_indexing": observation.get("snapshot_indexing_fingerprint"),
            "actual_projection": observation.get("snapshot_projection_fingerprint"),
        },
        "blocked_reason": blocked,
        "ready": action == "none" and not differences,
        "status": "ready" if action == "none" and not differences else
                  "blocked" if blocked else "rebuild_required",
    }
    if settings.mode == "inspect" or blocked or action == "none":
        plan["duration_ms"] = round((time.monotonic() - started) * 1000)
        return plan
    if context.require_vectors and action != "sync":
        backend_store = cast(SolrIndexStore | OpenSearchIndexStore | QdrantVectorIndexStore,
                             context.store)
        dimensions = getattr(backend_store.settings, "vector_dimensions", None)
        plan["embedding_probe"] = _probe_embedding_provider(
            backend_store,
            expected_dimensions=dimensions or 768,
        )
    with _resource_lock(context):
        # A new inspection under the lock prevents acting on a stale plan.
        if _schema_differences(context) != differences or _status(context) != observation:
            raise KnowledgeSearchError("Backend changed after reconciliation inspection",
                                       category="concurrent_write")
        mutation_started = time.monotonic()
        if context.backend == "solr":
            store = cast(SolrIndexStore, context.store)
            inspect_ownership(store, context.runtime.repository)
            if action == "sync":
                execution = context.legacy("sync")
            else:
                indexed = context.runtime.indexer.index(context.workspace)
                if action == "schema_upgrade_and_rebuild":
                    from fetchcore.adapters.retrieval.solr.reconciliation import add_structural_fields
                    if not add_structural_fields(store, cast(list[str], missing)):
                        if settings.solr_configset is None:
                            raise KnowledgeSearchError(
                                "Static Solr schema requires a compatible staging configset",
                                category="rebuild_required",
                            )
                        action = "staged_rebuild"
                if action == "staged_rebuild":
                    assert settings.solr_configset is not None
                    execution = rebuild_with_swap(
                        store, indexed, configset=settings.solr_configset,
                        unique_stage=settings.mode == "explicit-rebuild",
                    )
                else:
                    execution = store.full_rebuild(indexed).to_mapping()
        elif context.backend == "qdrant-vector":
            store_q = cast(QdrantVectorIndexStore, context.store)
            if action == "add_payload_indexes":
                try:
                    execution = {"added_payload_indexes": store_q.add_missing_payload_indexes(
                        context.runtime.repository,
                    )}
                except KnowledgeSearchError as exc:
                    if exc.category != "rebuild_required":
                        raise
                    action = "rebuild"
                    indexed = context.runtime.indexer.index(context.workspace)
                    snapshot = store_q.load_snapshot(context.runtime.repository)
                    execution = store_q.reconcile_projection(
                        indexed, expected_snapshot=snapshot,
                    ).to_mapping()
            elif action == "sync":
                assert context.driver is not None
                execution = context.driver.run("sync")
            else:
                indexed = context.runtime.indexer.index(context.workspace)
                snapshot = None
                try:
                    snapshot = store_q.load_snapshot(context.runtime.repository)
                except KnowledgeSearchError as exc:
                    if exc.category not in {"snapshot_missing", "schema_mismatch"}:
                        raise
                if (snapshot is not None and store_q.embedding_snapshot_matches(snapshot)
                        and snapshot.projection_fingerprint == store_q.projection_fingerprint):
                    execution = store_q.reconcile_projection(
                        indexed, expected_snapshot=snapshot,
                    ).to_mapping()
                else:
                    execution = store_q.full_rebuild(indexed).to_mapping()
        else:
            store_o = cast(OpenSearchIndexStore, context.store)
            if action == "sync":
                assert context.driver is not None
                execution = context.driver.run("sync")
            else:
                indexed = context.runtime.indexer.index(context.workspace)
                execution = store_o.full_rebuild(indexed).to_mapping()
        plan["stage_timings_ms"] = {
            "mutation": round((time.monotonic() - mutation_started) * 1000),
        }
        verified = _status(context)
        if (verified.get("mode") != "noop" or verified.get("currentness") != "current"):
            raise KnowledgeSearchError("Reconciled snapshot is not ready for retrieval",
                                       category="index_stale")
        plan.update(action=action, execution_result=execution,
                    schema_compatible=True, snapshot_validated=True,
                    ready=True, status="ready", blocked_reason=None,
                    duration_ms=round((time.monotonic() - started) * 1000))
        return plan
