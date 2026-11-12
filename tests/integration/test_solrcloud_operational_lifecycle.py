from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
from uuid import uuid4

import pytest

from fetchcore.adapters.retrieval.solr.backend import (
    SolrBackend,
    SolrIndexStore,
    SolrSettings,
    UrllibSolrTransport,
)
from fetchcore.adapters.retrieval.solr.cloud_lifecycle import (
    UrllibSolrCloudCollectionsApi,
)
from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store
from fetchcore.application.index_operations import prepare_repository_index_runtime
from fetchcore.application.indexing import SolrCloudOperationalIndex
from fetchcore.retrieval.contracts import SearchIntent, SearchScope


pytestmark = pytest.mark.skipif(
    "FETCHCORE_SOLR_CLOUD_LIFECYCLE_URL" not in os.environ,
    reason="requires an explicitly configured disposable SolrCloud cluster",
)


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git", "-C", str(root), "-c", "user.name=FetchCore Test",
            "-c", "user.email=test@example.invalid", "commit", "-qm", message,
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _runtime(root: Path, repository: str, revision: str):
    return prepare_repository_index_runtime(
        root,
        repository=repository,
        branch="main",
        revision=revision,
    )


def test_live_solrcloud_rebuild_incremental_sync_and_retrieval(tmp_path: Path) -> None:
    endpoint = os.environ["FETCHCORE_SOLR_CLOUD_LIFECYCLE_URL"]
    configset = os.environ.get(
        "FETCHCORE_SOLR_CLOUD_LIFECYCLE_CONFIGSET",
        "fetchcore-projection-v3",
    )
    token = uuid4().hex[:10]
    alias = f"fc-lifecycle-{token}"
    seed = f"{alias}-seed"
    repository = f"lifecycle/{token}"
    api_settings = SolrSettings.from_mapping({
        "url": endpoint,
        "collection": alias,
        "deployment_mode": "solrcloud",
        "configset": configset,
    })
    api = UrllibSolrCloudCollectionsApi(api_settings)
    before = set(api.list_collections())
    assert alias not in api.list_aliases()
    assert seed not in before

    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "module.py").write_text(
        "def lifecycle_target():\n    return 'first lifecycle sentinel'\n",
        encoding="utf-8",
    )
    revision_a = _commit(tmp_path, "first generation")
    runtime_a = _runtime(tmp_path, repository, revision_a)
    api.create_collection(seed, configset)
    api.set_alias(alias, seed)

    def driver(runtime):
        def store_for(collection: str) -> SolrIndexStore:
            selected = SolrSettings.from_mapping({
                "url": endpoint,
                "collection": collection,
                "deployment_mode": "solrcloud",
                "configset": configset,
            })
            store = SolrIndexStore(UrllibSolrTransport(selected), selected)
            return bind_indexing_contract_store(store, runtime.indexer)

        return SolrCloudOperationalIndex(store_for(alias), runtime)

    try:
        first = driver(runtime_a)
        assert first.run("bootstrap")["validated"] is True
        assert first.run("status")["mode"] == "initial"
        rebuilt = first.run("rebuild")
        active = rebuilt["publication"]["active_collection"]
        assert api.list_aliases()[alias] == active
        assert first.run("status")["indexed_revision"] == revision_a

        backend = SolrBackend()
        search_scope = SearchScope(
            workspace=tmp_path,
            revision=revision_a,
            repository=repository,
            knowledge_types=(),
            max_results=5,
            settings={
                "url": endpoint,
                "collection": alias,
                "deployment_mode": "solrcloud",
                "configset": configset,
            },
        )
        response = backend.search(
            SearchIntent(canonical_query="first lifecycle sentinel"),
            search_scope,
            (),
        )
        assert response.results
        assert response.results[0].revision == revision_a
        assert response.results[0].source_ref.startswith(f"repository://{repository}/")

        (tmp_path / "module.py").write_text(
            "def lifecycle_target():\n    return 'second lifecycle sentinel'\n",
            encoding="utf-8",
        )
        revision_b = _commit(tmp_path, "incremental update")
        runtime_b = _runtime(tmp_path, repository, revision_b)
        second = driver(runtime_b)
        synced = second.run("sync")
        assert synced["execution"]["snapshot"]["revision"] == revision_b
        assert second.run("status")["indexed_revision"] == revision_b
        assert second.run("sync")["execution"]["noop"] is True
        assert second.run("reconcile")["mutated"] is False
    finally:
        aliases = api.list_aliases()
        if alias in aliases:
            api.delete_alias(alias)
        created = set(api.list_collections()) - before
        generation_prefix = "fcg-" + hashlib.sha256(alias.encode()).hexdigest()[:10] + "-"
        for collection in sorted(created):
            if collection == seed or collection.startswith(generation_prefix):
                api.delete_collection(collection)
