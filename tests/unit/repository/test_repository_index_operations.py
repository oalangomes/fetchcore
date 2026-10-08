from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from fetchcore.search import KnowledgeSearchError
from fetchcore.repository.incremental import GitDeltaPlan
from fetchcore.repository.index_contract import IndexContractSolrIndexStore, IndexContractSolrSnapshot
from fetchcore.repository.index_operations import (
    dry_run_repository_index,
    inspect_repository_index,
    prepare_repository_index_runtime,
    repository_index_status,
)
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.structure import default_structural_symbol_extractors
from fetchcore.repository.sap_commerce_pack import SapCommerceKnowledgePack
from fetchcore.backends.solr import SOLR_PROJECTION_VERSION, SolrSettings


class NoWriteTransport:
    def __init__(self) -> None:
        self.posts = 0

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        raise AssertionError(f"unexpected Solr GET {path} {params}")

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        self.posts += 1
        raise AssertionError(f"dry-run attempted Solr write {path} {params} {payload}")


class FixtureStore(IndexContractSolrIndexStore):
    def __init__(
        self,
        snapshot: IndexContractSolrSnapshot | None,
        documents: tuple[object, ...] = (),
    ) -> None:
        self.transport_fixture = NoWriteTransport()
        super().__init__(self.transport_fixture, SolrSettings())
        self.snapshot_fixture = snapshot
        self.documents_fixture = documents

    def load_snapshot(self, repository: str) -> IndexContractSolrSnapshot:
        if self.snapshot_fixture is None:
            raise KnowledgeSearchError(
                "missing",
                category="snapshot_missing",
                metadata={"repository": repository},
            )
        assert repository == self.snapshot_fixture.repository
        self._ensure_snapshot_compatible(self.snapshot_fixture)
        return self.snapshot_fixture

    def inspect_projection(self, repository: str) -> dict[str, object]:
        snapshot = self.snapshot_fixture
        assert snapshot is not None
        assert repository == snapshot.repository
        compatible = (
            snapshot.projection_version == self.settings.projection_version
            and snapshot.projection_fingerprint == self.projection_fingerprint
            and snapshot.status == "ready"
        )
        return {
            "expected_projection_version": self.settings.projection_version,
            "actual_projection_version": snapshot.projection_version,
            "expected_projection_fingerprint": self.projection_fingerprint,
            "actual_projection_fingerprint": snapshot.projection_fingerprint,
            "snapshot_status": snapshot.status,
            "rebuild_required": not compatible,
        }

    def load_documents(self, repository: str) -> tuple[object, ...]:
        assert self.snapshot_fixture is not None
        assert repository == self.snapshot_fixture.repository
        return self.documents_fixture


class FixtureDetector:
    def __init__(self, plan: GitDeltaPlan) -> None:
        self.plan_fixture = plan

    def plan(
        self,
        workspace: Path,
        *,
        previous_revision: str | None,
        current_revision: str | None,
    ) -> GitDeltaPlan:
        del workspace, previous_revision, current_revision
        return self.plan_fixture


def _snapshot(
    *,
    fingerprint: str,
    revision: str,
    document_count: int,
    projection_version: str = SOLR_PROJECTION_VERSION,
) -> IndexContractSolrSnapshot:
    return IndexContractSolrSnapshot(
        repository="acme/widgets",
        branch="main",
        revision=revision,
        corpus_checksum="fixture",
        document_count=document_count,
        projection_version=projection_version,
        indexed_at="2026-08-19T00:00:00+00:00",
        indexing_fingerprint=fingerprint,
    )


def test_runtime_honors_explicit_extractor_selection(tmp_path: Path) -> None:
    policy_path = tmp_path / ".fetchcore/indexing.yaml"
    policy_path.parent.mkdir(parents=True)
    policy_path.write_text(
        """
version: 1
extractors:
  enabled:
    - javascript
    - typescript
""".strip()
        + "\n",
        encoding="utf-8",
    )

    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/widgets",
        branch="main",
        revision="rev-a",
    )

    assert tuple(sorted(runtime.indexer.symbol_extractors)) == (
        "javascript",
        "typescript",
    )


def _sap_workspace(root: Path) -> None:
    (root / "hybris" / "bin" / "custom").mkdir(parents=True)


def test_runtime_default_pack_selection_is_preserved_and_observable(
    tmp_path: Path,
) -> None:
    _sap_workspace(tmp_path)

    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
    )

    assert runtime.active_knowledge_packs == (("sap.commerce", "8"),)
    assert runtime.to_mapping()["knowledge_packs"] == [
        {"id": "sap.commerce", "version": "8"}
    ]
    assert runtime.indexer.source_interpreters
    assert runtime.indexer.semantic_enrichers
    assert runtime.indexer.corpus_resolvers


def test_runtime_explicit_empty_pack_registry_is_generic_only(
    tmp_path: Path,
) -> None:
    _sap_workspace(tmp_path)

    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(),
    )

    assert runtime.active_knowledge_packs == ()
    assert runtime.to_mapping()["knowledge_packs"] == []
    assert runtime.indexer.source_interpreters == ()
    assert runtime.indexer.semantic_enrichers == ()
    assert runtime.indexer.corpus_resolvers == ()


def test_runtime_explicit_pack_registry_activates_only_matching_packs(
    tmp_path: Path,
) -> None:
    pack = SapCommerceKnowledgePack()

    inactive = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/plain",
        branch="main",
        revision="rev-a",
        knowledge_packs=(pack,),
    )
    assert inactive.active_knowledge_packs == ()

    _sap_workspace(tmp_path)
    active = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(pack,),
    )

    assert active.active_knowledge_packs == (("sap.commerce", "8"),)
    assert active.indexer.source_interpreters
    assert active.indexer.semantic_enrichers
    assert active.indexer.corpus_resolvers


def test_explicit_generic_and_sap_runtimes_have_distinct_indexing_contracts(
    tmp_path: Path,
) -> None:
    _sap_workspace(tmp_path)

    generic = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(),
    )
    sap = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(SapCommerceKnowledgePack(),),
    )

    assert generic.fingerprint != sap.fingerprint
    assert generic.to_mapping()["knowledge_packs"] == []
    assert sap.to_mapping()["knowledge_packs"] == [
        {"id": "sap.commerce", "version": "8"}
    ]


def test_status_is_cheap_and_reports_clean_same_snapshot(tmp_path: Path) -> None:
    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/widgets",
        branch="main",
        revision="rev-a",
    )
    snapshot = _snapshot(
        fingerprint=runtime.fingerprint,
        revision="rev-a",
        document_count=42,
    )
    store = FixtureStore(snapshot)
    detector = FixtureDetector(
        GitDeltaPlan(
            mode="git-delta",
            base_revision="rev-a",
            current_revision="rev-a",
        )
    )

    payload = repository_index_status(store, runtime, detector=detector)  # type: ignore[arg-type]

    assert payload["state"] == "ready"
    assert payload["reason"] == "up_to_date"
    assert payload["sync_mode"] == "incremental"
    assert payload["snapshot_fingerprint_matches"] is True
    assert payload["projection"] == {
        "expected_projection_version": SOLR_PROJECTION_VERSION,
        "actual_projection_version": SOLR_PROJECTION_VERSION,
        "expected_projection_fingerprint": store.projection_fingerprint,
        "actual_projection_fingerprint": store.projection_fingerprint,
        "snapshot_status": "ready",
        "rebuild_required": False,
    }
    assert payload["discovery"] == {
        "mode": "git-delta",
        "reason": "safe_git_delta",
        "base_revision": "rev-a",
        "current_revision": "rev-a",
        "changed_paths": 0,
        "removed_paths": 0,
        "targeted_paths": 0,
        "changed_path_values": [],
        "removed_path_values": [],
    }
    assert store.transport_fixture.posts == 0


def test_inspection_exposes_projection_mismatch_and_full_rebuild_requirement(
    tmp_path: Path,
) -> None:
    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/widgets",
        branch="main",
        revision="rev-a",
    )
    snapshot = _snapshot(
        fingerprint=runtime.fingerprint,
        revision="rev-a",
        document_count=4,
        projection_version="2",
    )
    store = FixtureStore(snapshot)

    payload = inspect_repository_index(store, runtime)  # type: ignore[arg-type]

    assert payload["state"] == "rebuild_required"
    assert payload["sync_mode"] == "full_rebuild"
    assert payload["snapshot"] is None
    assert payload["projection"]["expected_projection_version"] == SOLR_PROJECTION_VERSION  # type: ignore[index]
    assert payload["projection"]["actual_projection_version"] == "2"  # type: ignore[index]
    assert payload["projection"]["rebuild_required"] is True  # type: ignore[index]
    assert payload["documents"]["persisted_count"] == 0  # type: ignore[index]
    assert store.transport_fixture.posts == 0


def test_inspect_adds_persisted_document_distributions(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("def run():\n    return True\n", encoding="utf-8")
    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/widgets",
        branch="main",
        revision="rev-a",
    )
    documents = runtime.indexer.index(tmp_path).documents
    snapshot = _snapshot(
        fingerprint=runtime.fingerprint,
        revision="rev-a",
        document_count=len(documents),
    )
    store = FixtureStore(snapshot, documents)
    detector = FixtureDetector(
        GitDeltaPlan(
            mode="git-delta",
            base_revision="rev-a",
            current_revision="rev-a",
        )
    )

    payload = inspect_repository_index(store, runtime, detector=detector)  # type: ignore[arg-type]
    distribution = payload["documents"]

    assert isinstance(distribution, dict)
    assert distribution["persisted_count"] == len(documents)
    assert distribution["knowledge_types"] == {"code": len(documents)}
    assert distribution["languages"] == {"python": len(documents)}
    assert store.transport_fixture.posts == 0


def test_dry_run_calculates_targeted_update_without_solr_writes(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text("def run():\n    return 1\n", encoding="utf-8")
    previous_indexer = RepositoryIndexer(
        RepositoryIndexConfig(
            repository="acme/widgets",
            branch="main",
            revision="rev-a",
        ),
        symbol_extractors=default_structural_symbol_extractors(),
    )
    previous = previous_indexer.index(tmp_path).documents

    source.write_text("def run():\n    return 2\n", encoding="utf-8")
    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/widgets",
        branch="main",
        revision="rev-b",
    )
    snapshot = _snapshot(
        fingerprint=runtime.fingerprint,
        revision="rev-a",
        document_count=len(previous),
    )
    store = FixtureStore(snapshot, previous)
    detector = FixtureDetector(
        GitDeltaPlan(
            mode="git-delta",
            base_revision="rev-a",
            current_revision="rev-b",
            changed_paths=("service.py",),
        )
    )

    payload = dry_run_repository_index(store, runtime, detector=detector)  # type: ignore[arg-type]
    mutations = payload["planned_mutations"]

    assert payload["mode"] == "incremental"
    assert payload["noop"] is False
    assert payload["discovery"]["changed_path_values"] == ["service.py"]  # type: ignore[index]
    assert isinstance(mutations, dict)
    assert mutations["updated"] == 1
    assert mutations["added"] == 0
    assert mutations["deleted"] == 0
    assert mutations["upsert_documents"] == 1
    assert mutations["replace_repository"] is False
    assert store.transport_fixture.posts == 0


def test_dry_run_without_snapshot_plans_full_rebuild_without_write(tmp_path: Path) -> None:
    (tmp_path / "service.py").write_text("value = 1\n", encoding="utf-8")
    runtime = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/widgets",
        branch="main",
        revision="rev-a",
    )
    store = FixtureStore(None)

    payload = dry_run_repository_index(store, runtime)
    mutations = payload["planned_mutations"]

    assert payload["mode"] == "full_rebuild"
    assert payload["reason"] == "snapshot_snapshot_missing"
    assert isinstance(mutations, dict)
    assert mutations["replace_repository"] is True
    assert mutations["upsert_documents"] == mutations["resulting_document_count"]
    assert store.transport_fixture.posts == 0


def test_base_index_capacity_is_applied_before_knowledge_pack_treatment(
    tmp_path: Path,
) -> None:
    (tmp_path / "hybris" / "bin" / "custom").mkdir(parents=True)
    base_config = RepositoryIndexConfig(
        max_files=65_000,
        max_file_bytes=1_000_000,
        max_total_bytes=512_000_000,
    )

    generic = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(),
        base_config=base_config,
    )
    treated = prepare_repository_index_runtime(
        tmp_path,
        repository="acme/sap-commerce",
        branch="main",
        revision="rev-a",
        knowledge_packs=(SapCommerceKnowledgePack(),),
        base_config=base_config,
    )

    expected = (65_000, 1_000_000, 512_000_000)
    for runtime in (generic, treated):
        assert (
            runtime.indexer.config.max_files,
            runtime.indexer.config.max_file_bytes,
            runtime.indexer.config.max_total_bytes,
        ) == expected

    assert generic.active_knowledge_packs == ()
    assert treated.active_knowledge_packs == (("sap.commerce", "8"),)
    assert generic.fingerprint != treated.fingerprint
