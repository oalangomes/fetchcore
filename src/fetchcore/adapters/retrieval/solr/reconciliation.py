"""Physical Solr reconciliation for a dedicated standalone core.

The active core is never reset. A new core is built from a server-side configset,
validated, and atomically swapped into the stable name. The old core remains under
the staging name for operator recovery.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
import json
from xml.etree import ElementTree
from pathlib import Path

from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore, SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import (
    IndexContractSolrIndexStore, IndexContractSolrSnapshot,
)
from fetchcore.operational.solr_runtime import (
    DeploymentMode, UrllibJsonClient, probe_solr_service,
)
from fetchcore.repository.indexing import RepositoryIndexResult
from fetchcore.projection import corpus_checksum
from fetchcore.retrieval.contracts import KnowledgeSearchError


@dataclass(frozen=True)
class SolrOwnership:
    total_records: int
    repository_records: int
    snapshot_records: int
    snapshot_signature: str | None = None


def _count(store: SolrIndexStore, query: str) -> int:
    payload = store.transport.get("select", {"q": query, "rows": 0, "wt": "json"})
    response = payload.get("response")
    count = response.get("numFound") if isinstance(response, dict) else None
    if (not isinstance(count, int) or isinstance(count, bool)
            or (isinstance(response, dict) and response.get("numFoundExact") is False)):
        raise KnowledgeSearchError("Solr ownership count is unavailable",
                                   category="ownership_unproven")
    return count


def inspect_ownership(store: SolrIndexStore, repository: str) -> SolrOwnership:
    """Prove a dedicated core contains only this repository and one snapshot."""
    from fetchcore.adapters.retrieval.solr.query import _term_filter

    total = _count(store, "*:*")
    matching = _count(store, _term_filter("repository", repository))
    snapshots = _count(store, "record_kind:snapshot")
    if matching != total or (total and snapshots != 1):
        raise KnowledgeSearchError(
            "Solr core ownership or snapshot identity is unproven",
            category="ownership_unproven",
            metadata={"total_records": total, "repository_records": matching,
                      "snapshot_records": snapshots},
        )
    signature: str | None = None
    if snapshots:
        raw = store.transport.get("select", {
            "q": "record_kind:snapshot", "rows": 2, "fl": "*", "wt": "json",
        })
        response = raw.get("response")
        docs = response.get("docs") if isinstance(response, dict) else None
        if (not isinstance(docs, list) or len(docs) != 1
                or not isinstance(docs[0], dict)
                or docs[0].get("repository") != repository):
            raise KnowledgeSearchError("Solr snapshot binding is unproven",
                                       category="ownership_unproven")
        snapshot = docs[0]
        if (snapshot.get("snapshot_status") != "ready"
                or snapshot.get("document_count") != total - 1
                or not isinstance(snapshot.get("snapshot_revision"), str)
                or not isinstance(snapshot.get("corpus_checksum"), str)):
            raise KnowledgeSearchError("Solr snapshot count or provenance is unproven",
                                       category="ownership_unproven")
        signature = sha256(json.dumps(snapshot, sort_keys=True, default=str).encode()).hexdigest()
    return SolrOwnership(total, matching, snapshots, signature)


def inspect_snapshot_identity(store: SolrIndexStore, repository: str) -> dict[str, object]:
    """Read raw persisted identity without accepting an incompatible snapshot."""
    from fetchcore.adapters.retrieval.solr.query import _term_filter

    payload = store.transport.get("select", {
        "q": "*:*", "fq": ["record_kind:snapshot", _term_filter("repository", repository)],
        "rows": 2, "fl": "*", "wt": "json",
    })
    response = payload.get("response")
    docs = response.get("docs") if isinstance(response, dict) else None
    if not isinstance(docs, list) or len(docs) != 1 or not isinstance(docs[0], dict):
        return {}
    snapshot = IndexContractSolrSnapshot.from_solr(docs[0])
    return {"indexed_revision": snapshot.revision,
            "snapshot_indexing_fingerprint": snapshot.indexing_fingerprint,
            "snapshot_projection_fingerprint": snapshot.projection_fingerprint}


def managed_schema(store: SolrIndexStore) -> bool:
    """Read the loaded core's schema filename; never infer from a mounted configset."""
    client = UrllibJsonClient()
    payload = client.request(
        "GET", f"{store.settings.url}/admin/cores",
        params={"action": "STATUS", "core": store.settings.collection,
                "indexInfo": "false", "wt": "json"},
        timeout=store.settings.timeout_seconds,
    )
    statuses = payload.get("status")
    core = statuses.get(store.settings.collection) if isinstance(statuses, dict) else None
    if not isinstance(core, dict) or not isinstance(core.get("schema"), str):
        raise KnowledgeSearchError("Solr core schema identity is unavailable",
                                   category="schema_mismatch")
    return "managed-schema" in core["schema"]


def standalone_mode(store: SolrIndexStore) -> bool:
    service = probe_solr_service(
        store.settings.url, expected_mode=DeploymentMode.AUTO,
        timeout=store.settings.timeout_seconds, client=UrllibJsonClient(),
    )
    return service.deployment_mode is DeploymentMode.STANDALONE


def add_structural_fields(store: SolrIndexStore, missing: list[str]) -> bool:
    """Apply only exact additive field definitions from the packaged configset."""
    if not managed_schema(store):
        return False
    names = {
        "structural_names", "structural_qualified_names", "structural_identities",
        "structural_incoming_identities",
    }
    if not missing or not set(missing) <= names:
        raise KnowledgeSearchError("Solr additive field request is unsupported",
                                   category="schema_mismatch")
    schema_file = Path(__file__).parents[3] / "operational" / "configset" / "conf" / "schema.xml"
    root = ElementTree.parse(schema_file).getroot()
    fields = {field.attrib["name"]: field.attrib for field in root.findall(".//field")}
    for name in sorted(missing):
        spec = fields.get(name)
        if spec is None:
            raise KnowledgeSearchError("Versioned Solr field definition is missing",
                                       category="schema_mismatch")
        store.transport.post("schema", {"add-field": dict(spec)}, {"wt": "json"})
    store.validate_schema(require_vectors=store.embedding_provider is not None)
    return True


def rebuild_with_swap(
    store: SolrIndexStore, result: RepositoryIndexResult, *, configset: str,
    unique_stage: bool = False,
) -> dict[str, object]:
    """Stage a complete projection and swap only on standalone Solr."""
    if not configset or not configset.replace("-", "").replace("_", "").isalnum():
        raise ValueError("A safe server-side Solr configset name is required")
    service = probe_solr_service(
        store.settings.url, expected_mode=DeploymentMode.STANDALONE,
        timeout=store.settings.timeout_seconds, client=UrllibJsonClient(),
    )
    original = inspect_ownership(store, result.repository)
    identity = sha256(
        f"{result.repository}\0{result.revision}\0{corpus_checksum(result.documents)}\0{getattr(store, 'indexing_fingerprint', '')}".encode()
    ).hexdigest()[:16]
    active = store.settings.collection
    stage = f"{active}-fc-reconcile-{identity}"
    if unique_stage:
        from uuid import uuid4
        stage = f"{stage}-{uuid4().hex[:8]}"
    client = UrllibJsonClient()
    status = client.request(
        "GET", f"{service.endpoint}/admin/cores",
        params={"action": "STATUS", "core": stage, "indexInfo": "false", "wt": "json"},
        timeout=store.settings.timeout_seconds,
    )
    cores = status.get("status")
    details = cores.get(stage) if isinstance(cores, dict) else None
    exists = isinstance(details, dict) and details.get("name") == stage
    if not exists:
        client.request(
            "GET", f"{service.endpoint}/admin/cores",
            params={"action": "CREATE", "name": stage, "configSet": configset,
                    "wt": "json"},
            timeout=store.settings.timeout_seconds,
        )
    stage_settings: SolrSettings = replace(store.settings, collection=stage)
    stage_store = type(store)(
        UrllibSolrTransport(stage_settings), stage_settings,
        embedding_provider=store.embedding_provider,
        clock=store.clock, timer=store.timer,
    )
    if isinstance(store, IndexContractSolrIndexStore):
        assert isinstance(stage_store, IndexContractSolrIndexStore)
        stage_store.indexing_fingerprint = store.indexing_fingerprint
    stage_store.validate_schema(require_vectors=store.embedding_provider is not None)
    inspect_ownership(stage_store, result.repository)
    persisted = stage_store.full_rebuild(result)
    staged = stage_store.load_snapshot(result.repository)
    if staged != persisted.snapshot:
        raise KnowledgeSearchError("Solr staged snapshot changed before cutover",
                                   category="concurrent_write")
    # Recheck ownership before the name swap; a separate writer may have acted.
    if inspect_ownership(store, result.repository) != original:
        raise KnowledgeSearchError("Solr active core changed while staging",
                                   category="concurrent_write")
    client.request(
        "GET", f"{service.endpoint}/admin/cores",
        params={"action": "SWAP", "core": active, "other": stage, "wt": "json"},
        timeout=store.settings.timeout_seconds,
    )
    try:
        store.validate_schema(require_vectors=store.embedding_provider is not None)
        published = store.load_snapshot(result.repository)
        if published != staged:
            raise KnowledgeSearchError("Solr published snapshot differs from stage",
                                       category="index_update_failure")
    except Exception:
        client.request(
            "GET", f"{service.endpoint}/admin/cores",
            params={"action": "SWAP", "core": active, "other": stage, "wt": "json"},
            timeout=store.settings.timeout_seconds,
        )
        raise
    return {"active_resource": active, "preserved_previous_resource": stage,
            "snapshot": persisted.snapshot.to_solr(),
            "telemetry": None if persisted.telemetry is None else persisted.telemetry.to_mapping()}
