"""Read-only operational views over repository indexing state.

Part 7 exposes status, inspection, and dry-run behavior without creating a
parallel indexing runtime. The operations reuse the repository-local policy,
canonical indexer, indexing fingerprint, Git delta detector, and persisted Solr
snapshot established by the previous indexing deliveries.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path

from fetchcore.backends.solr import SolrIndexSnapshot, SolrIndexStore
from fetchcore.repository.corpus_diagnostics import analyze_corpus_duplication
from fetchcore.repository.default_knowledge_packs import (
    default_repository_knowledge_packs,
)
from fetchcore.repository.document_incremental import (
    DocumentAwareGitSolrSynchronizer,
)
from fetchcore.repository.document_policy import (
    RepositoryDocumentIndexPolicy,
    load_repository_document_index_policy,
)
from fetchcore.repository.html import HtmlStructuralExtractor
from fetchcore.repository.incremental import (
    GitDeltaPlan,
    GitRepositoryChangeDetector,
)
from fetchcore.repository.index_contract import (
    bind_indexing_contract_store,
    indexing_contract_fingerprint,
    snapshot_indexing_fingerprint,
    snapshot_mapping,
)
from fetchcore.repository.index_policy import (
    RepositoryIndexPolicy,
    load_repository_index_policy,
)
from fetchcore.repository.indexing import (
    RepositoryIndexConfig,
    RepositoryIndexResult,
    RepositoryIndexer,
)
from fetchcore.repository.knowledge_pack import (
    RepositoryKnowledgePack,
    configure_repository_knowledge_packs,
    corpus_resolvers_from_packs,
    select_repository_knowledge_packs,
    semantic_enrichers_from_packs,
    source_interpreters_from_packs,
)
from fetchcore.repository.semantic_indexing import (
    SemanticDocumentRepositoryIndexer,
)
from fetchcore.repository.shell import ShellStructuralExtractor
from fetchcore.repository.structure import default_structural_symbol_extractors
from fetchcore.search import KnowledgeSearchError


_REBUILDABLE_SNAPSHOT_ERRORS = frozenset(
    {"snapshot_missing", "schema_mismatch", "response_parsing_failure"}
)


@dataclass(frozen=True)
class RepositoryIndexRuntime:
    """Resolved indexing runtime for one materialized repository snapshot."""

    workspace: Path
    repository: str
    branch: str | None
    revision: str | None
    policy: RepositoryIndexPolicy
    document_policy: RepositoryDocumentIndexPolicy
    indexer: RepositoryIndexer
    fingerprint: str
    active_knowledge_packs: tuple[tuple[str, str], ...]

    def to_mapping(self) -> dict[str, object]:
        """Serialize runtime diagnostics without exposing backend implementation state."""

        document_extractors = getattr(self.indexer, "document_extractors", {})
        semantic_contract = getattr(self.indexer, "semantic_indexing_contract", None)
        return {
            "repository": {
                "id": self.repository,
                "workspace": str(self.workspace),
                "branch": self.branch,
                "revision": self.revision,
            },
            "indexing": {
                "fingerprint": self.fingerprint,
                "extractors": sorted(self.indexer.symbol_extractors),
                "document_extractors": sorted(document_extractors),
                "semantic": semantic_contract,
            },
            "knowledge_packs": [
                {"id": pack_id, "version": version}
                for pack_id, version in self.active_knowledge_packs
            ],
            "policy": self.policy.to_mapping(),
            "document_policy": self.document_policy.to_mapping(),
        }


def prepare_repository_index_runtime(
    workspace: Path,
    *,
    repository: str,
    branch: str | None,
    revision: str | None,
    knowledge_packs: Iterable[RepositoryKnowledgePack] | None = None,
    base_config: RepositoryIndexConfig | None = None,
) -> RepositoryIndexRuntime:
    """Resolve repository policy, active knowledge packs and canonical indexer."""

    root = workspace.resolve(strict=True)
    policy = load_repository_index_policy(root, base_config=base_config)
    document_policy = load_repository_document_index_policy(root)
    registry = default_structural_symbol_extractors()
    registry["shell"] = ShellStructuralExtractor()
    registry["html"] = HtmlStructuralExtractor()
    extractors = policy.select_extractors(registry)
    pack_registry = (
        default_repository_knowledge_packs()
        if knowledge_packs is None
        else tuple(knowledge_packs)
    )
    packs = select_repository_knowledge_packs(
        root,
        pack_registry,
    )
    config = configure_repository_knowledge_packs(
        policy.config_for(
            repository=repository,
            branch=branch,
            revision=revision,
        ),
        packs,
        apply_defaults=policy.source is None,
    )
    indexer = SemanticDocumentRepositoryIndexer(
        config,
        symbol_extractors=extractors,
        document_policy=document_policy,
        source_interpreters=source_interpreters_from_packs(packs),
        semantic_enrichers=semantic_enrichers_from_packs(packs),
        corpus_resolvers=corpus_resolvers_from_packs(packs),
    )
    if policy.enabled_extractors is not None:
        # RepositoryIndexer keeps a legacy Python fallback when callers do not
        # provide a registry. A repository policy is an explicit registry, so
        # it must be authoritative even when Python is intentionally disabled.
        indexer.symbol_extractors = dict(extractors)
    return RepositoryIndexRuntime(
        workspace=root,
        repository=repository,
        branch=branch,
        revision=revision,
        policy=policy,
        document_policy=document_policy,
        indexer=indexer,
        fingerprint=indexing_contract_fingerprint(indexer),
        active_knowledge_packs=tuple(
            (pack.pack_id.strip(), pack.version.strip()) for pack in packs
        ),
    )


def repository_index_status(
    store: SolrIndexStore,
    runtime: RepositoryIndexRuntime,
    *,
    detector: GitRepositoryChangeDetector | None = None,
) -> dict[str, object]:
    """Return a cheap repository/snapshot compatibility and freshness view."""

    bound = bind_indexing_contract_store(store, runtime.indexer)
    snapshot, snapshot_error = _load_snapshot_optional(bound, runtime.repository)
    payload = runtime.to_mapping()
    payload["projection"] = (
        bound.inspect_projection(runtime.repository)
        if snapshot is None
        else {
            "expected_projection_version": bound.settings.projection_version,
            "actual_projection_version": snapshot.projection_version,
            "expected_projection_fingerprint": bound.projection_fingerprint,
            "actual_projection_fingerprint": snapshot.projection_fingerprint,
            "snapshot_status": snapshot.status,
            "rebuild_required": False,
        }
    )

    if snapshot is None:
        reason = f"snapshot_{snapshot_error}"
        payload.update(
            {
                "state": "not_indexed"
                if snapshot_error == "snapshot_missing"
                else "rebuild_required",
                "reason": reason,
                "sync_mode": "full_rebuild",
                "snapshot": None,
                "snapshot_error": snapshot_error,
                "discovery": _full_scan_mapping(None, runtime.revision, reason),
            }
        )
        return payload

    snapshot_fingerprint = snapshot_indexing_fingerprint(snapshot)
    payload["snapshot"] = snapshot_mapping(snapshot)
    payload["snapshot_error"] = None
    payload["snapshot_fingerprint_matches"] = snapshot_fingerprint == runtime.fingerprint

    if snapshot_fingerprint != runtime.fingerprint:
        payload.update(
            {
                "state": "rebuild_required",
                "reason": "indexing_contract_changed",
                "sync_mode": "full_rebuild",
                "discovery": _full_scan_mapping(
                    snapshot.revision,
                    runtime.revision,
                    "indexing_contract_changed",
                ),
            }
        )
        return payload

    plan = (detector or GitRepositoryChangeDetector()).plan(
        runtime.workspace,
        previous_revision=snapshot.revision,
        current_revision=runtime.revision,
    )
    clean = _is_clean_same_snapshot(snapshot, runtime, plan)
    payload.update(
        {
            "state": "ready" if clean else "changes_pending",
            "reason": "up_to_date" if clean else plan.reason,
            "sync_mode": "incremental",
            "discovery": _discovery_mapping(plan),
        }
    )
    return payload


def inspect_repository_index(
    store: SolrIndexStore,
    runtime: RepositoryIndexRuntime,
    *,
    detector: GitRepositoryChangeDetector | None = None,
) -> dict[str, object]:
    """Return status plus persisted document and corpus diagnostics."""

    payload = repository_index_status(store, runtime, detector=detector)
    snapshot_payload = payload.get("snapshot")
    if snapshot_payload is None:
        payload["documents"] = {
            "persisted_count": 0,
            "knowledge_types": {},
            "languages": {},
            "technical_roles": {},
            "corpus_purposes": {},
            "source_origins": {},
            "corpus_diagnostics": analyze_corpus_duplication(()).to_mapping(),
        }
        return payload

    bound = bind_indexing_contract_store(store, runtime.indexer)
    documents = bound.load_documents(runtime.repository)
    payload["documents"] = {
        "persisted_count": len(documents),
        "knowledge_types": _distribution(item.knowledge_type for item in documents),
        "languages": _distribution(
            item.language if item.language is not None else "unknown" for item in documents
        ),
        "technical_roles": _distribution(
            item.technical_role if item.technical_role is not None else "unclassified"
            for item in documents
        ),
        "corpus_purposes": _distribution(item.corpus_purpose for item in documents),
        "source_origins": _distribution(item.source_origin for item in documents),
        "corpus_diagnostics": analyze_corpus_duplication(documents).to_mapping(),
    }
    return payload


def dry_run_repository_index(
    store: SolrIndexStore,
    runtime: RepositoryIndexRuntime,
    *,
    detector: GitRepositoryChangeDetector | None = None,
) -> dict[str, object]:
    """Calculate the next indexing mutation without persisting anything."""

    bound = bind_indexing_contract_store(store, runtime.indexer)
    change_detector = detector or GitRepositoryChangeDetector()
    snapshot, snapshot_error = _load_snapshot_optional(bound, runtime.repository)

    if snapshot is None:
        reason = f"snapshot_{snapshot_error}"
        result = runtime.indexer.index(runtime.workspace)
        return _full_rebuild_preview(
            runtime,
            result,
            reason=reason,
            previous_snapshot=None,
            snapshot_error=snapshot_error,
        )

    if snapshot_indexing_fingerprint(snapshot) != runtime.fingerprint:
        result = runtime.indexer.index(runtime.workspace)
        return _full_rebuild_preview(
            runtime,
            result,
            reason="indexing_contract_changed",
            previous_snapshot=snapshot,
            snapshot_error=None,
        )

    plan = change_detector.plan(
        runtime.workspace,
        previous_revision=snapshot.revision,
        current_revision=runtime.revision,
    )
    if _is_clean_same_snapshot(snapshot, runtime, plan):
        payload = runtime.to_mapping()
        payload.update(
            {
                "mode": "incremental",
                "noop": True,
                "reason": "up_to_date",
                "snapshot": snapshot_mapping(snapshot),
                "discovery": _discovery_mapping(plan),
                "planned_mutations": {
                    "added": 0,
                    "updated": 0,
                    "deleted": 0,
                    "unchanged": snapshot.document_count,
                    "upsert_documents": 0,
                    "resulting_document_count": snapshot.document_count,
                    "replace_repository": False,
                },
            }
        )
        return payload

    previous = bound.load_documents(snapshot.repository)
    planner = DocumentAwareGitSolrSynchronizer(
        bound,
        runtime.indexer,
        detector=change_detector,
    )
    if plan.mode == "git-delta":
        delta_result = planner._index_delta(runtime.workspace, previous, plan)
        if delta_result is None:
            plan = replace(plan, mode="full-scan", reason="delta_scan_unsafe")
            result = runtime.indexer.index(
                runtime.workspace,
                previous_documents=previous,
            )
        else:
            result = delta_result
    else:
        result = runtime.indexer.index(
            runtime.workspace,
            previous_documents=previous,
        )

    if result.repository != snapshot.repository:
        raise KnowledgeSearchError(
            "repository identity differs from the active Solr snapshot",
            category="snapshot_revision_mismatch",
        )

    changes = result.changes
    noop = (
        plan.mode == "git-delta"
        and not (changes.added or changes.updated or changes.removed)
        and snapshot.revision == result.revision
        and (result.branch is None or snapshot.branch == result.branch)
    )
    payload = runtime.to_mapping()
    payload.update(
        {
            "mode": "incremental",
            "noop": noop,
            "reason": "canonical_noop" if noop else plan.reason,
            "snapshot": snapshot_mapping(snapshot),
            "discovery": _discovery_mapping(plan),
            "planned_mutations": {
                "added": len(changes.added),
                "updated": len(changes.updated),
                "deleted": len(changes.removed),
                "unchanged": len(changes.unchanged),
                "upsert_documents": len(changes.added) + len(changes.updated),
                "resulting_document_count": len(result.documents),
                "replace_repository": False,
            },
            "indexing_telemetry": result.telemetry.to_mapping(),
            "corpus_diagnostics": analyze_corpus_duplication(result.documents).to_mapping(),
        }
    )
    return payload


def _full_rebuild_preview(
    runtime: RepositoryIndexRuntime,
    result: RepositoryIndexResult,
    *,
    reason: str,
    previous_snapshot: SolrIndexSnapshot | None,
    snapshot_error: str | None,
) -> dict[str, object]:
    payload = runtime.to_mapping()
    payload.update(
        {
            "mode": "full_rebuild",
            "noop": False,
            "reason": reason,
            "snapshot": (
                None if previous_snapshot is None else snapshot_mapping(previous_snapshot)
            ),
            "snapshot_error": snapshot_error,
            "discovery": _full_scan_mapping(
                None if previous_snapshot is None else previous_snapshot.revision,
                result.revision,
                reason,
            ),
            "planned_mutations": {
                "added": len(result.documents),
                "updated": 0,
                "deleted": 0,
                "unchanged": 0,
                "upsert_documents": len(result.documents),
                "resulting_document_count": len(result.documents),
                "replace_repository": True,
                "previous_document_count": (
                    0 if previous_snapshot is None else previous_snapshot.document_count
                ),
            },
            "indexing_telemetry": result.telemetry.to_mapping(),
            "corpus_diagnostics": analyze_corpus_duplication(result.documents).to_mapping(),
        }
    )
    return payload


def _load_snapshot_optional(
    store: SolrIndexStore,
    repository: str,
) -> tuple[SolrIndexSnapshot | None, str | None]:
    try:
        return store.load_snapshot(repository), None
    except KnowledgeSearchError as exc:
        if exc.category not in _REBUILDABLE_SNAPSHOT_ERRORS:
            raise
        return None, exc.category


def _is_clean_same_snapshot(
    snapshot: SolrIndexSnapshot,
    runtime: RepositoryIndexRuntime,
    plan: GitDeltaPlan,
) -> bool:
    return (
        plan.mode == "git-delta"
        and not plan.changed_paths
        and not plan.removed_paths
        and snapshot.revision == plan.current_revision
        and (runtime.branch is None or snapshot.branch == runtime.branch)
    )


def _discovery_mapping(plan: GitDeltaPlan) -> dict[str, object]:
    payload = plan.to_mapping()
    payload["changed_path_values"] = list(plan.changed_paths)
    payload["removed_path_values"] = list(plan.removed_paths)
    return payload


def _full_scan_mapping(
    previous_revision: str | None,
    current_revision: str | None,
    reason: str,
) -> dict[str, object]:
    return {
        "mode": "full-scan",
        "reason": reason,
        "base_revision": previous_revision,
        "current_revision": current_revision,
        "changed_paths": 0,
        "removed_paths": 0,
        "targeted_paths": 0,
        "changed_path_values": [],
        "removed_path_values": [],
    }


def _distribution(values: Iterable[object]) -> dict[str, int]:
    counter = Counter(str(value) for value in values)
    return {key: counter[key] for key in sorted(counter)}
