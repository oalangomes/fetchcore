from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
from multiprocessing import get_context
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
    SolrCloudGeneration,
    SolrCloudGenerationManager,
    UrllibSolrCloudCollectionsApi,
)
from fetchcore.adapters.retrieval.solr.index_contract import bind_indexing_contract_store
from fetchcore.application import (
    FindSymbolRequest,
    GetRelatedRequest,
    RepositoryBinding,
    RetrievalApplicationService,
)
from fetchcore.application.index_operations import (
    RepositoryIndexRuntime,
    prepare_repository_index_runtime,
)
from fetchcore.application.indexing import SolrCloudOperationalIndex
from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchScope,
)


pytestmark = pytest.mark.skipif(
    "FETCHCORE_SOLR_CLOUD_LIFECYCLE_URL" not in os.environ,
    reason="requires an explicitly configured disposable SolrCloud cluster",
)


def _commit(root: Path, message: str) -> str:
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=FetchCore Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-qm",
            message,
        ],
        check=True,
    )
    return subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        text=True,
    ).strip()


def _runtime(root: Path, repository: str, revision: str) -> RepositoryIndexRuntime:
    return prepare_repository_index_runtime(
        root,
        repository=repository,
        branch="main",
        revision=revision,
    )


def test_live_solrcloud_rebuild_incremental_sync_and_retrieval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = os.environ["FETCHCORE_SOLR_CLOUD_LIFECYCLE_URL"]
    configset = os.environ.get(
        "FETCHCORE_SOLR_CLOUD_LIFECYCLE_CONFIGSET",
        "fetchcore-projection-v3",
    )
    token = uuid4().hex[:10]
    alias = f"fc-lifecycle-{token}"
    seed = f"{alias}-seed"
    repository = f"lifecycle/{token}"
    api_settings = SolrSettings.from_mapping(
        {
            "url": endpoint,
            "collection": alias,
            "deployment_mode": "solrcloud",
            "configset": configset,
        }
    )
    api = UrllibSolrCloudCollectionsApi(api_settings)
    before = set(api.list_collections())
    assert alias not in api.list_aliases()
    assert seed not in before
    lock_name = (
        "fetchcore-lock-" + hashlib.sha256(f"{alias}\0{repository}".encode()).hexdigest()[:20]
    )
    assert lock_name not in before

    subprocess.run(["git", "init", "-b", "main", str(tmp_path)], check=True)
    (tmp_path / "module.py").write_text(
        "def lifecycle_target():\n    return 'first lifecycle sentinel'\n",
        encoding="utf-8",
    )
    (tmp_path / "obsolete.py").write_text(
        "obsolete_lifecycle_sentinel = 'removed by incremental sync'\n",
        encoding="utf-8",
    )
    (tmp_path / "consumer.py").write_text(
        "from module import lifecycle_target\n\n"
        "def consume_lifecycle_target():\n"
        "    return lifecycle_target()\n",
        encoding="utf-8",
    )
    revision_a = _commit(tmp_path, "first generation")
    runtime_a = _runtime(tmp_path, repository, revision_a)
    api.create_collection(seed, configset)
    api.set_alias(alias, seed)

    def driver(runtime: RepositoryIndexRuntime) -> SolrCloudOperationalIndex:
        def store_for(collection: str) -> SolrIndexStore:
            selected = SolrSettings.from_mapping(
                {
                    "url": endpoint,
                    "collection": collection,
                    "deployment_mode": "solrcloud",
                    "configset": configset,
                }
            )
            store = SolrIndexStore(UrllibSolrTransport(selected), selected)
            return bind_indexing_contract_store(store, runtime.indexer)

        return SolrCloudOperationalIndex(store_for(alias), runtime)

    try:
        first = driver(runtime_a)
        assert first.run("bootstrap")["validated"] is True
        assert first.run("status")["mode"] == "initial"
        original_inspect = SolrCloudGenerationManager._inspect_generation
        validation_failed = False

        def fail_after_promotion(
            manager: SolrCloudGenerationManager,
            collection: str,
            selected_repository: str,
        ) -> SolrCloudGeneration:
            nonlocal validation_failed
            generation = original_inspect(manager, collection, selected_repository)
            if (
                collection == alias
                and manager.active_collection() != seed
                and not validation_failed
            ):
                validation_failed = True
                raise RuntimeError("injected post-promotion validation failure")
            return generation

        with monkeypatch.context() as injected:
            injected.setattr(
                SolrCloudGenerationManager,
                "_inspect_generation",
                fail_after_promotion,
            )
            with pytest.raises(
                KnowledgeSearchError,
                match="previous generation was restored",
            ):
                first.run("rebuild")
        assert validation_failed is True
        assert api.list_aliases()[alias] == seed

        original_set_alias = UrllibSolrCloudCollectionsApi.set_alias
        promotion_response_lost = False

        def lose_promotion_response(
            collections_api: UrllibSolrCloudCollectionsApi,
            target_alias: str,
            collection: str,
        ) -> None:
            nonlocal promotion_response_lost
            original_set_alias(collections_api, target_alias, collection)
            if target_alias == alias and not promotion_response_lost:
                promotion_response_lost = True
                raise KnowledgeSearchError(
                    "injected lost alias promotion response",
                    category="unavailable",
                )

        with monkeypatch.context() as injected:
            injected.setattr(
                UrllibSolrCloudCollectionsApi,
                "set_alias",
                lose_promotion_response,
            )
            rebuilt = first.run("rebuild")
        assert promotion_response_lost is True
        publication = rebuilt["publication"]
        assert isinstance(publication, dict)
        active = publication["active_collection"]
        assert isinstance(active, str)
        assert api.list_aliases()[alias] == active
        assert first.run("status")["indexed_revision"] == revision_a
        assert any(
            collection.startswith("fcg-" + hashlib.sha256(alias.encode()).hexdigest()[:10] + "-")
            and collection != active
            for collection in api.list_collections()
        )

        backend = SolrBackend()

        def search(query: str, revision: str) -> KnowledgeSearchResponse:
            return backend.search(
                SearchIntent(canonical_query=query),
                SearchScope(
                    workspace=tmp_path,
                    revision=revision,
                    repository=repository,
                    knowledge_types=(),
                    max_results=5,
                    settings={
                        "url": endpoint,
                        "collection": alias,
                        "deployment_mode": "solrcloud",
                        "configset": configset,
                    },
                ),
                (),
            )

        response = search("first lifecycle sentinel", revision_a)
        assert response.results
        assert response.results[0].revision == revision_a
        assert response.results[0].source_ref.startswith(f"repository://{repository}/")

        (tmp_path / "module.py").write_text(
            "def lifecycle_target():\n    return 'second lifecycle sentinel'\n",
            encoding="utf-8",
        )
        (tmp_path / "added.py").write_text(
            "added_lifecycle_sentinel = 'inserted by incremental sync'\n",
            encoding="utf-8",
        )
        (tmp_path / "obsolete.py").unlink()
        revision_b = _commit(tmp_path, "incremental update")
        runtime_b = _runtime(tmp_path, repository, revision_b)
        second = driver(runtime_b)

        generation_prefix = "fcg-" + hashlib.sha256(alias.encode()).hexdigest()[:10] + "-"
        active_before_crash = second.manager.require_active_generation(repository)
        original_upsert = SolrIndexStore._upsert_documents
        crash_after_write = False

        def terminate_after_cloud_write(
            store: SolrIndexStore,
            documents: Iterable[KnowledgeDocument],
        ) -> tuple[int, int, int]:
            nonlocal crash_after_write
            result = original_upsert(store, documents)
            if store.settings.collection.startswith(generation_prefix) and not crash_after_write:
                crash_after_write = True
                os._exit(73)
            return result

        def crash_worker() -> None:
            with monkeypatch.context() as injected:
                injected.setattr(
                    SolrIndexStore,
                    "_upsert_documents",
                    terminate_after_cloud_write,
                )
                second.run("sync")

        process = get_context("fork").Process(target=crash_worker)
        process.start()
        process.join(timeout=120)
        if process.is_alive():
            process.terminate()
            process.join(timeout=10)
            pytest.fail("interrupted SolrCloud writer did not terminate")
        assert process.exitcode == 73
        assert api.list_aliases()[alias] == active_before_crash

        lock_report = second.run("reconcile")
        assert lock_report["action"] == "recovery_required"
        assert lock_report["mutated"] is False
        crash_snapshot = lock_report["snapshot"]
        assert isinstance(crash_snapshot, dict)
        assert crash_snapshot["revision"] == revision_a
        assert search("first lifecycle sentinel", revision_a).results
        assert all(
            "second lifecycle sentinel" not in item.content
            for item in search("second lifecycle sentinel", revision_a).results
        )
        with pytest.raises(KnowledgeSearchError) as lock_failure:
            second.run("sync")
        assert lock_failure.value.category == "concurrent_write"
        assert lock_failure.value.metadata["recovery_required"] is True

        lock_store = second.manager.store_factory(lock_name)
        lock_records = lock_store._select(
            {
                "q": 'id:"fetchcore-writer-lock"',
                "rows": 2,
                "fl": "id,record_kind,repository,canonical_payload",
                "wt": "json",
            }
        )["docs"]
        assert len(lock_records) == 1
        lock_record = lock_records[0]
        ownership = json.loads(str(lock_record["canonical_payload"]))
        assert lock_record["record_kind"] == "writer_lock"
        assert lock_record["repository"] == repository
        assert ownership["alias"] == alias
        assert ownership["token"]
        api.delete_collection(lock_name)
        assert lock_name not in api.list_collections()

        active_before_failed_sync = second.manager.require_active_generation(repository)
        partial_write_applied = False

        def fail_after_cloud_write(
            store: SolrIndexStore,
            documents: Iterable[KnowledgeDocument],
        ) -> tuple[int, int, int]:
            nonlocal partial_write_applied
            result = original_upsert(store, documents)
            if (
                store.settings.collection.startswith(generation_prefix)
                and not partial_write_applied
            ):
                partial_write_applied = True
                raise RuntimeError("injected failure after Solr accepted an update")
            return result

        with monkeypatch.context() as injected:
            injected.setattr(SolrIndexStore, "_upsert_documents", fail_after_cloud_write)
            with pytest.raises(
                KnowledgeSearchError,
                match="incremental generation staging failed",
            ) as write_failure:
                second.run("sync")
        assert partial_write_applied is True
        assert write_failure.value.category == "index_update_failure"
        assert write_failure.value.metadata["activation"] == "not_attempted"
        assert write_failure.value.metadata["rollback"] == "succeeded"
        assert api.list_aliases()[alias] == active_before_failed_sync
        restored_report = second.manager.reconciliation_report(repository)
        restored_snapshot = restored_report["snapshot"]
        assert isinstance(restored_snapshot, dict)
        assert restored_snapshot["revision"] == revision_a
        assert search("first lifecycle sentinel", revision_a).results
        assert all(
            "second lifecycle sentinel" not in item.content
            for item in search("second lifecycle sentinel", revision_a).results
        )

        synced = second.run("sync")
        execution = synced["execution"]
        assert isinstance(execution, dict)
        publication = synced["publication"]
        assert isinstance(publication, dict)
        assert publication["previous_collection"] == active_before_failed_sync
        assert publication["active_collection"] != active_before_failed_sync
        assert publication["copied_records"] > 0
        assert publication["copy_duration_ms"] >= 0
        assert api.list_aliases()[alias] == publication["active_collection"]
        retained_store = second.manager.store_factory(active_before_failed_sync)
        assert retained_store.load_snapshot(repository).revision == revision_a
        snapshot = execution["snapshot"]
        assert isinstance(snapshot, dict)
        assert snapshot["revision"] == revision_b
        assert execution["added"] > 0
        assert execution["updated"] > 0
        assert execution["deleted"] > 0
        added_results = search("inserted by incremental sync", revision_b).results
        assert any(
            "added.py" in item.source_ref
            and "inserted by incremental sync" in item.content
            for item in added_results
        )
        updated_results = search("second lifecycle sentinel", revision_b).results
        assert any(
            "module.py" in item.source_ref
            and "second lifecycle sentinel" in item.content
            for item in updated_results
        )
        assert all(
            "first lifecycle sentinel" not in item.content
            for item in search("first lifecycle sentinel", revision_b).results
        )
        assert all(
            "obsolete.py" not in item.source_ref
            for item in search("removed by incremental sync", revision_b).results
        )
        navigation = RetrievalApplicationService(
            {
                repository: RepositoryBinding(
                    repository,
                    tmp_path,
                    "solr",
                    knowledge_types=("code",),
                )
            },
            navigation_store=lambda _binding: second.store,
        )
        symbol = navigation.find_symbol(
            FindSymbolRequest("lifecycle_target", repository=repository, path="module.py")
        )
        assert symbol.status == "success"
        assert len(symbol.results) == 1
        related = navigation.get_related(
            GetRelatedRequest(
                f"repository://{repository}/consumer.py",
                repository=repository,
                relation_types=("imports",),
            )
        )
        assert related.status == "success"
        assert related.results
        assert second.run("status")["indexed_revision"] == revision_b
        active_before_noop = second.manager.require_active_generation(repository)
        generations_before_noop = {
            collection for collection in api.list_collections()
            if collection.startswith(generation_prefix)
        }
        noop_execution = second.run("sync")["execution"]
        assert isinstance(noop_execution, dict)
        assert noop_execution["noop"] is True
        assert second.manager.require_active_generation(repository) == active_before_noop
        assert {
            collection for collection in api.list_collections()
            if collection.startswith(generation_prefix)
        } == generations_before_noop
        assert second.run("reconcile")["mutated"] is False
        assert not any(
            collection.startswith("fetchcore-lock-") for collection in api.list_collections()
        )
    finally:
        aliases = api.list_aliases()
        if alias in aliases:
            api.delete_alias(alias)
        created = set(api.list_collections()) - before
        generation_prefix = "fcg-" + hashlib.sha256(alias.encode()).hexdigest()[:10] + "-"
        for collection in sorted(created):
            if collection in {seed, lock_name} or collection.startswith(generation_prefix):
                api.delete_collection(collection)
