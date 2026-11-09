"""Opt-in live old-generation reconciliation against disposable backend resources."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest

from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchIndexStore, OpenSearchSettings, UrllibOpenSearchTransport,
    _repository_alias as opensearch_alias,
)
from fetchcore.adapters.retrieval.opensearch.operational import OpenSearchOperationalIndex
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorIndexStore, QdrantVectorSettings, UrllibQdrantTransport,
)
from fetchcore.adapters.retrieval.qdrant.operational import QdrantOperationalIndex
from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexStore, SolrSettings, UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store
from fetchcore.adapters.retrieval.solr.reconciliation import rebuild_with_swap
from fetchcore.application import (
    FindSymbolRequest, GetRelatedRequest, RepositoryBinding,
    RetrievalApplicationService, SourceReadRequest,
)
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.application.indexing import IndexContext
from fetchcore.application.reconciliation import reconcile_index_context
from fetchcore.application.runtime_config import ReconciliationSettings
from fetchcore.operational.solr_runtime import UrllibJsonClient
from fetchcore.retrieval.contracts import KnowledgeSearchError


pytestmark = pytest.mark.skipif(
    "FETCHCORE_LIVE_RECONCILE_BACKEND" not in os.environ,
    reason="requires an explicitly selected live disposable backend",
)


class DeterministicEmbedder:
    provider_id = "reconciliation-test"
    model_id = "sha256-4"

    def __init__(self) -> None:
        self.vectors = 0

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.vectors += len(texts)
        return tuple(tuple((hashlib.sha256(text.encode()).digest()[i] + 1) / 256
                           for i in range(4)) for text in texts)


def _source(root: Path, repository: str):
    (root / "target.py").write_text("def exact_symbol():\n    return 42\n")
    (root / "caller.py").write_text(
        "from target import exact_symbol\n\n"
        "def caller():\n    return exact_symbol()\n",
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test",
                    "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture"],
                   check=True)
    revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                       text=True).strip()
    return prepare_repository_index_runtime(
        root, repository=repository, branch=None, revision=revision,
    )


def test_live_old_generation_reconciles_without_losing_prior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = os.environ["FETCHCORE_LIVE_RECONCILE_BACKEND"]
    repository = "integration/reconciliation"
    runtime = _source(tmp_path, repository)
    indexed = runtime.indexer.index(tmp_path)
    policy = ReconciliationSettings(mode="safe-auto", managed=True)
    token = uuid4().hex[:10]
    clock = lambda: datetime.now(UTC).isoformat()
    if backend == "opensearch":
        settings = OpenSearchSettings.from_mapping({
            "url": os.environ["FETCHCORE_OPENSEARCH_URL"],
            "index": f"fetchcore-reconcile-live-{token}",
        })
        store = OpenSearchIndexStore(UrllibOpenSearchTransport(settings), settings,
                                     clock=clock)
        store.repository = repository
        store.indexing_fingerprint = runtime.fingerprint
        alias = opensearch_alias(settings, repository)
        old = f"{alias}-old"
        mapping = store._generation_mapping(repository)
        properties = mapping["mappings"]["properties"]
        properties.pop("structural_names")
        store.transport.request("PUT", old, mapping)
        records, *_ = store._project_documents_with_telemetry(indexed.documents)
        for record in records:
            record.pop("structural_names", None)
        snapshot = store._snapshot(indexed, indexed_at=clock())
        store._bulk_upsert(old, (*records, snapshot.to_mapping()))
        store.transport.request("POST", f"{old}/_refresh", {})
        store.transport.request("POST", "_aliases", {"actions": [
            {"add": {"index": old, "alias": alias}},
        ]})
        context = IndexContext(tmp_path, "opensearch", runtime, store,
                               driver=OpenSearchOperationalIndex(store, runtime))
        report = reconcile_index_context(context, policy)
        assert report["schema_differences"]["missing_fields"] == ["structural_names"]
        assert report["ready"] is True
        assert store._alias_indexes(alias) != (old,)
        assert store.transport.request("GET", f"{old}/_mapping")
        assert store.load_snapshot(repository).revision == runtime.revision
        assert reconcile_index_context(context, policy)["action"] == "none"
        previous_alias = store._alias_indexes(alias)
        previous_snapshot = store.load_snapshot(repository)
        original_load = store.load_snapshot

        def fail_new_alias(name: str):
            if store._alias_indexes(alias) != previous_alias:
                raise KnowledgeSearchError("Injected post-promotion validation failure",
                                           category="index_update_failure")
            return original_load(name)

        with monkeypatch.context() as promotion_failure:
            promotion_failure.setattr(store, "load_snapshot", fail_new_alias)
            with pytest.raises(KnowledgeSearchError, match="Injected post-promotion"):
                reconcile_index_context(context, ReconciliationSettings(
                    mode="explicit-rebuild",
                ))
        assert store._alias_indexes(alias) == previous_alias
        assert store.load_snapshot(repository) == previous_snapshot
        assert reconcile_index_context(context, policy)["action"] == "none"
    elif backend == "qdrant":
        settings_q = QdrantVectorSettings.from_mapping({
            "url": os.environ["FETCHCORE_QDRANT_URL"],
            "collection": f"fetchcore-reconcile-live-{token}",
            "embedding_model": "sha256-4", "vector_dimensions": 4,
        })
        embedder = DeterministicEmbedder()
        store_q = QdrantVectorIndexStore(UrllibQdrantTransport(settings_q), settings_q,
                                         embedder, clock=clock)
        store_q.repository = repository
        store_q.indexing_fingerprint = "old-indexing-contract"
        first = store_q.full_rebuild(indexed)
        assert first.snapshot.active_collection is not None
        prior = first.snapshot.active_collection
        store_q.indexing_fingerprint = runtime.fingerprint
        context_q = IndexContext(tmp_path, "qdrant-vector", runtime, store_q,
                                 driver=QdrantOperationalIndex(store_q, runtime))
        before = embedder.vectors
        report_q = reconcile_index_context(context_q, policy)
        assert report_q["ready"] is True
        assert report_q["execution_result"]["telemetry"]["embedding_vectors_reused"] == len(indexed.documents)
        assert embedder.vectors == before + 1  # configured-provider probe only
        assert store_q.load_snapshot(repository).active_collection != prior
        assert prior in store_q._collection_names()
        active = store_q.load_snapshot(repository).active_collection
        assert active is not None
        store_q.transport.request("DELETE", f"collections/{active}/index/structural_names?wait=true")
        repaired = reconcile_index_context(context_q, policy)
        assert repaired["action"] == "add_payload_indexes"
        assert repaired["ready"] is True
        assert store_q.load_snapshot(repository).active_collection == active
        assert reconcile_index_context(context_q, policy)["action"] == "none"
    elif backend == "solr":
        configset = os.environ["FETCHCORE_SOLR_RECONCILE_CONFIGSET"]
        settings_s = SolrSettings.from_mapping({
            "url": os.environ["FETCHCORE_SOLR_URL"],
            "collection": f"fetchcore-reconcile-live-{token}",
        })
        client = UrllibJsonClient()
        client.request(
            "GET", f"{settings_s.url}/admin/cores",
            params={"action": "CREATE", "name": settings_s.collection,
                    "configSet": os.environ.get("FETCHCORE_SOLR_OLD_CONFIGSET", "fetchcore"),
                    "wt": "json"},
            timeout=settings_s.timeout_seconds,
        )
        store_s = bind_indexing_contract_store(
            SolrIndexStore(UrllibSolrTransport(settings_s), settings_s), runtime.indexer,
        )
        context_s = IndexContext(tmp_path, "solr", runtime, store_s)
        snapshot_method = type(store_s)._stage_snapshot

        def interrupt_during_rebuild(self, snapshot):
            if self.settings.collection != settings_s.collection:
                raise KeyboardInterrupt("Injected interruption during Solr staging")
            return snapshot_method(self, snapshot)

        with monkeypatch.context() as interruption:
            interruption.setattr(type(store_s), "_stage_snapshot", interrupt_during_rebuild)
            with pytest.raises(KeyboardInterrupt, match="Injected interruption"):
                reconcile_index_context(context_s, ReconciliationSettings(
                    mode="safe-auto", managed=True, solr_configset=configset,
                ))
        assert store_s.transport.get("select", {"q": "*:*", "rows": 0})["response"]["numFound"] == 0
        stage_method = type(store_s).full_rebuild

        def interrupt_after_stage(self, result):
            persisted = stage_method(self, result)
            if self.settings.collection != settings_s.collection:
                raise KnowledgeSearchError("Injected interruption after staging",
                                           category="index_update_failure")
            return persisted

        with monkeypatch.context() as interruption:
            interruption.setattr(type(store_s), "full_rebuild", interrupt_after_stage)
            with pytest.raises(KnowledgeSearchError, match="Injected interruption"):
                reconcile_index_context(context_s, ReconciliationSettings(
                    mode="safe-auto", managed=True, solr_configset=configset,
                ))
        assert store_s.transport.get("select", {"q": "*:*", "rows": 0})["response"]["numFound"] == 0
        # Resume in a new process; no Python object from staging survives.
        config_path = tmp_path.with_name(f"reconciliation-{token}.yml")
        config_path.write_text(
            "version: 1\nreconciliation:\n  mode: safe-auto\n"
            f"  managed: true\n  solr_configset: {configset}\n",
        )
        resumed = subprocess.run([
            sys.executable, "-m", "fetchcore.cli", "index",
            "--config", str(config_path), "--workspace", str(tmp_path),
            "--repository", repository, "--revision", str(runtime.revision),
            "--backend", "solr", "--backend-settings", json.dumps({
                "url": settings_s.url, "collection": settings_s.collection,
            }), "reconcile", "--json",
        ], capture_output=True, text=True, check=True, timeout=180)
        report_s = json.loads(resumed.stdout)["result"]
        store_s = bind_indexing_contract_store(
            SolrIndexStore(UrllibSolrTransport(settings_s), settings_s), runtime.indexer,
        )
        context_s = IndexContext(tmp_path, "solr", runtime, store_s)
        assert report_s["action"] == "staged_rebuild"
        assert report_s["ready"] is True
        assert report_s["execution_result"]["reused_staged_generation"] is True
        assert store_s.load_snapshot(repository).document_count == len(indexed.documents)
        application = RetrievalApplicationService(
            {repository: RepositoryBinding(repository, tmp_path, "solr")},
            navigation_store=lambda _binding: store_s,
        )
        definition = application.find_symbol(
            FindSymbolRequest("exact_symbol", repository)
        ).results[0]
        caller = application.find_symbol(
            FindSymbolRequest("caller", repository)
        ).results[0]
        assert application.get_related(GetRelatedRequest(
            caller.identity, repository, relation_types=("calls",),
        )).results
        assert "def exact_symbol" in application.get_source(SourceReadRequest(
            definition.source_ref, repository=repository, revision=definition.revision,
            checksum=definition.checksum,
        )).content
        prior = report_s["execution_result"]["preserved_previous_resource"]
        assert isinstance(prior, str)
        old_settings = SolrSettings.from_mapping({"url": settings_s.url, "collection": prior})
        old_store = SolrIndexStore(UrllibSolrTransport(old_settings), old_settings)
        assert old_store.transport.get("select", {"q": "*:*", "rows": 0})["response"]["numFound"] == 0
        assert reconcile_index_context(context_s, policy)["action"] == "none"
        active_snapshot = store_s.load_snapshot(repository)
        original_load = store_s.load_snapshot
        calls = 0

        def fail_post_cutover(name: str):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise KnowledgeSearchError("Injected post-cutover validation failure",
                                           category="index_update_failure")
            return original_load(name)

        monkeypatch.setattr(store_s, "load_snapshot", fail_post_cutover)
        with pytest.raises(KnowledgeSearchError, match="Injected post-cutover"):
            rebuild_with_swap(store_s, indexed, configset=configset, unique_stage=True)
        monkeypatch.setattr(store_s, "load_snapshot", original_load)
        assert store_s.load_snapshot(repository) == active_snapshot
        assert reconcile_index_context(context_s, policy)["action"] == "none"
        rebuilt = reconcile_index_context(context_s, ReconciliationSettings(
            mode="explicit-rebuild", solr_configset=configset,
        ))
        assert rebuilt["ready"] is True
        assert rebuilt["execution_result"]["preserved_previous_resource"] != prior
        assert old_store.transport.get("select", {"q": "*:*", "rows": 0})["response"]["numFound"] == 0
    else:
        raise ValueError("unsupported live reconciliation backend")
