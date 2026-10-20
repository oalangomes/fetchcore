"""Operational readiness is typed, read-only and deliberately fail-closed."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from fetchcore.application.readiness import (
    ReadinessFact, ReadinessReport, inspect_readiness,
)
from fetchcore.application.runtime_config import FetchCoreRuntimeConfig
from fetchcore.application.runtime_inspection import (
    DoctorReport, RuntimeCheck, RuntimeInspectionService,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.config import RetrievalConfig


def _doctor(**statuses: tuple[str, str | None]) -> DoctorReport:
    defaults = {
        "runtime_config": ("ok", None),
        "backend_resolution": ("ok", None),
        "backend_connectivity": ("ok", None),
        "schema_compatibility": ("ok", None),
        "embedding_provider": ("not_required", None),
        "retrieval_compatibility": ("ok", None),
    }
    defaults.update(statuses)
    return DoctorReport(tuple(
        RuntimeCheck(name, status, category)  # type: ignore[arg-type]
        for name, (status, category) in defaults.items()
    ))


def test_report_never_promotes_unknown_and_does_not_claim_satisfaction() -> None:
    facts = {
        key: ReadinessFact("ready")
        for key in ("service", "schema", "snapshot", "currentness",
                    "indexing_fingerprint", "embedding", "retrieval")
    }
    facts["embedding"] = ReadinessFact("not_required")
    facts["snapshot"] = ReadinessFact("unknown")
    report = ReadinessReport("solr", "demo/repo", "a", None, "lexical", facts, {})
    assert not report.ready
    payload = report.to_mapping()
    assert payload["schema_version"] == 1
    assert payload["status"] == "not_ready"
    assert "satisfaction" not in payload and "relevance" not in payload
    assert not ReadinessReport("solr", "demo/repo", None, None, "lexical", {}, {}).ready
    facts["snapshot"] = ReadinessFact("ready")
    assert report.ready


def test_healthy_but_unindexed_is_not_ready(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fetchcore.adapters.retrieval.solr.index_contract as contract
    from fetchcore.application import indexing
    import fetchcore.application.runtime_inspection as inspection
    monkeypatch.setattr(inspection.RuntimeInspectionService, "doctor", lambda self: _doctor())
    class MissingStore:
        def load_snapshot(self, repository: str) -> object:
            assert repository == "demo/repo"
            raise KnowledgeSearchError("absent", category="snapshot_missing")
    runtime = SimpleNamespace(indexer=object(), fingerprint="fingerprint")
    monkeypatch.setattr(indexing, "build_index_context", lambda **kw: SimpleNamespace(store=MissingStore(), runtime=runtime))
    monkeypatch.setattr(contract, "bind_indexing_contract_store", lambda store, indexer: store)
    config = FetchCoreRuntimeConfig(
        backend="solr", workspace=tmp_path, repository="demo/repo", revision="abc",
    )
    report = inspect_readiness(RuntimeInspectionService(config))
    assert report.checks["service"].state == "ready"
    assert report.checks["schema"].state == "ready"
    assert report.checks["snapshot"].state == "absent"
    assert report.checks["currentness"].state == "absent"
    assert not report.ready


@pytest.mark.parametrize("compatible", [True, False])
def test_opensearch_observation_sets_schema_fact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, compatible: bool,
) -> None:
    from fetchcore.adapters.retrieval.opensearch import backend, operational
    from fetchcore.application import index_operations, runtime_inspection

    monkeypatch.setattr(
        runtime_inspection.RuntimeInspectionService, "doctor",
        lambda self: _doctor(schema_compatibility=("not_checked", None)),
    )
    monkeypatch.setattr(backend, "OpenSearchIndexStore", lambda *a, **kw: FakeStore())
    monkeypatch.setattr(
        index_operations, "prepare_repository_index_runtime",
        lambda *a, **kw: SimpleNamespace(fingerprint="fp1"),
    )

    class StubOperationalIndex:
        def __init__(self, store: object, runtime: object) -> None:
            pass

        def observe(self) -> operational.OpenSearchObservation:
            return operational.OpenSearchObservation(
                "noop" if compatible else "rebuild_required",
                "current" if compatible else "incompatible",
                "snapshot_current" if compatible else "schema_mismatch",
                schema_compatible=compatible,
            )

    monkeypatch.setattr(operational, "OpenSearchOperationalIndex", StubOperationalIndex)
    report = inspect_readiness(RuntimeInspectionService(FetchCoreRuntimeConfig(
        backend="opensearch", workspace=tmp_path,
        repository="demo/repo", revision="abc",
    )))
    assert report.checks["schema"].state == ("ready" if compatible else "incompatible")
    assert report.ready is compatible


@pytest.mark.parametrize(
    ("compatible", "configured_dimensions"),
    [(True, None), (False, None), (True, 8)],
)
def test_qdrant_observation_and_live_embedding_set_readiness(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    compatible: bool, configured_dimensions: int | None,
) -> None:
    from fetchcore.adapters.retrieval.qdrant import backend, operational
    from fetchcore.application import index_operations, runtime_inspection
    from fetchcore.application.composition import EmbeddingProviderRegistry

    class StubProvider:
        provider_id = "ollama"
        model_id = "embeddinggemma"

        def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            assert texts == ("fetchcore readiness",)
            return (tuple(0.1 for _ in range(768)),)

    class StubStore:
        def load_snapshot(self, repository: str) -> SimpleNamespace:
            assert repository == "demo/repo"
            return SimpleNamespace(
                revision="abc", projection_fingerprint="projection",
                serializer_fingerprint="serializer", indexing_fingerprint="fp1",
                vector_dimensions=768,
            )

        def embedding_snapshot_matches(self, snapshot: object) -> bool:
            return True

    class StubOperationalIndex:
        def __init__(self, store: object, runtime: object) -> None:
            pass

        def observe(self) -> operational.QdrantObservation:
            return operational.QdrantObservation(
                "noop" if compatible else "rebuild_required",
                "current" if compatible else "incompatible",
                "up_to_date" if compatible else "schema_mismatch",
                schema_compatible=compatible,
            )

    monkeypatch.setattr(
        runtime_inspection.RuntimeInspectionService, "doctor",
        lambda self: _doctor(
            schema_compatibility=("not_checked", None),
            embedding_provider=("ok", None),
        ),
    )
    monkeypatch.setattr(EmbeddingProviderRegistry, "create", lambda *a, **kw: StubProvider())
    monkeypatch.setattr(backend, "QdrantVectorIndexStore", lambda *a, **kw: StubStore())
    monkeypatch.setattr(operational, "QdrantOperationalIndex", StubOperationalIndex)
    monkeypatch.setattr(
        index_operations, "prepare_repository_index_runtime",
        lambda *a, **kw: SimpleNamespace(fingerprint="fp1"),
    )
    report = inspect_readiness(RuntimeInspectionService(FetchCoreRuntimeConfig(
        backend="qdrant-vector", workspace=tmp_path,
        repository="demo/repo", revision="abc",
        retrieval=RetrievalConfig.from_mapping({"dense": {
            "enabled": True, "embedding": {"dimensions": configured_dimensions},
        }}),
    )))
    assert report.checks["schema"].state == ("ready" if compatible else "incompatible")
    assert report.checks["indexing_fingerprint"].state == "ready"
    assert report.checks["embedding"].state == (
        "incompatible" if configured_dimensions == 8 else "ready"
    )
    if configured_dimensions == 8:
        assert report.checks["embedding"].category == "vector_dimensions_mismatch"
    assert report.ready is (compatible and configured_dimensions != 8)


@dataclass(frozen=True)
class Snapshot:
    repository: str = "demo/repo"
    revision: str = "abc"
    projection_fingerprint: str = "projection"
    embedding_serializer_fingerprint: str | None = None
    indexing_fingerprint: str | None = "fp1"


class FakeStore:
    def __init__(self, snapshot: Snapshot | None = None, *, matches: bool = True) -> None:
        self.snapshot = snapshot or Snapshot()
        self.matches = matches

    def load_snapshot(self, repository: str) -> Snapshot:
        assert repository == "demo/repo"
        return self.snapshot

    def embedding_snapshot_matches(self, snapshot: Snapshot) -> bool:
        return self.matches


@pytest.mark.parametrize(
    ("snapshot_revision", "persisted_fp", "state", "wanted_currentness", "wanted_fingerprint"),
    [
        ("abc", "fp1", "ready", "ready", "ready"),
        ("older", "fp1", "changes_pending", "stale", "ready"),
        ("abc", "incompatible", "rebuild_required", "unknown", "incompatible"),
        ("abc", "fp1", "changes_pending", "stale", "ready"),
    ],
)
def test_snapshot_currentness_and_fingerprints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    snapshot_revision: str, persisted_fp: str, state: str,
    wanted_currentness: str, wanted_fingerprint: str,
) -> None:
    import fetchcore.adapters.retrieval.solr.index_contract as contract
    from fetchcore.application import indexing
    import fetchcore.application.index_operations as operations
    import fetchcore.application.runtime_inspection as inspection

    monkeypatch.setattr(inspection.RuntimeInspectionService, "doctor", lambda self: _doctor())
    runtime = SimpleNamespace(indexer=object(), fingerprint="fp1")
    store = FakeStore(Snapshot(revision=snapshot_revision, indexing_fingerprint=persisted_fp))
    monkeypatch.setattr(indexing, "build_index_context", lambda **kw: SimpleNamespace(store=store, runtime=runtime))
    monkeypatch.setattr(contract, "bind_indexing_contract_store", lambda store, indexer: store)
    monkeypatch.setattr(
        operations, "repository_index_status",
        lambda store, runtime: {"state": state, "reason": "indexing_contract_changed" if state == "rebuild_required" else "changes_pending"},
    )
    report = inspect_readiness(RuntimeInspectionService(FetchCoreRuntimeConfig(
        backend="solr", workspace=tmp_path, repository="demo/repo", revision="abc",
    )))
    assert report.checks["currentness"].state == wanted_currentness
    assert report.checks["indexing_fingerprint"].state == wanted_fingerprint
    assert report.ready == (state == "ready" and persisted_fp == "fp1")
    assert report.fingerprints["expected_indexing"] == "fp1"


def test_unknown_revision_is_not_proved_current(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import fetchcore.adapters.retrieval.solr.index_contract as contract
    from fetchcore.application import indexing
    import fetchcore.application.index_operations as operations
    import fetchcore.application.runtime_inspection as inspection
    monkeypatch.setattr(inspection.RuntimeInspectionService, "doctor", lambda self: _doctor())
    runtime = SimpleNamespace(indexer=object(), fingerprint="fp1")
    store = FakeStore()
    monkeypatch.setattr(indexing, "build_index_context", lambda **kw: SimpleNamespace(store=store, runtime=runtime))
    monkeypatch.setattr(contract, "bind_indexing_contract_store", lambda store, indexer: store)
    monkeypatch.setattr(operations, "repository_index_status", lambda store, runtime: {"state": "ready"})
    report = inspect_readiness(RuntimeInspectionService(FetchCoreRuntimeConfig(
        backend="solr", workspace=tmp_path, repository="demo/repo",
    )))
    assert report.checks["currentness"].state == "unknown"
    assert not report.ready


def test_dense_missing_or_incompatible_provider_never_silently_falls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import fetchcore.application.runtime_inspection as inspection
    monkeypatch.setattr(inspection.RuntimeInspectionService, "doctor", lambda self: _doctor(
        embedding_provider=("failed", "unsupported_capability"),
    ))
    config = FetchCoreRuntimeConfig(
        backend="solr", workspace=tmp_path, repository="demo/repo",
        retrieval=RetrievalConfig.from_mapping({"dense": {"enabled": True}}),
    )
    # Even when the provider is not available, no lexical success is fabricated.
    monkeypatch.setattr(inspection.RuntimeInspectionService, "_probe_solr", lambda self: (
        RuntimeCheck("backend_connectivity", "failed", "unavailable"),
        RuntimeCheck("schema_compatibility", "not_checked"),
    ))
    report = inspect_readiness(RuntimeInspectionService(config))
    assert report.mode == "dense"
    assert report.checks["embedding"].state == "unsupported"
    assert not report.ready


def test_local_scanner_requires_a_valid_workspace_and_no_embedding(
    tmp_path: Path,
) -> None:
    config = FetchCoreRuntimeConfig(
        backend="local-semantic", workspace=tmp_path, repository="demo/repo",
    )
    report = inspect_readiness(RuntimeInspectionService(config))
    assert report.ready
    assert report.checks["snapshot"].state == "not_required"
    assert report.checks["embedding"].state == "not_required"


def test_cli_readiness_exit_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from fetchcore import cli
    import fetchcore.application.runtime_inspection as inspection
    monkeypatch.setenv("FETCHCORE_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("FETCHCORE_BACKEND", "local-semantic")
    monkeypatch.setenv("FETCHCORE_REPOSITORY", "demo/repo")
    good = CliRunner().invoke(cli.main, ["readiness", "--json"])
    assert good.exit_code == 0, good.output
    assert '"ready": true' in good.output
    monkeypatch.setattr(inspection.RuntimeInspectionService, "doctor", lambda self: _doctor(
        backend_connectivity=("failed", "unavailable"),
    ))
    failed = CliRunner().invoke(cli.main, ["readiness", "--json"])
    assert failed.exit_code == 1, failed.output
    assert '"ready": false' in failed.output
    assert '"category": "unavailable"' in failed.output


@pytest.mark.parametrize(
    ("identity_matches", "vector_size", "expected"),
    [
        (False, 768, "embedding_identity_changed"),
        (True, 8, "vector_dimensions_mismatch"),
    ],
)
def test_solr_dense_snapshot_identity_and_live_dimensions_fail_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    identity_matches: bool, vector_size: int, expected: str,
) -> None:
    from fetchcore.application import indexing, index_operations, runtime_inspection
    import fetchcore.adapters.retrieval.solr.index_contract as contract
    from fetchcore.application.composition import EmbeddingProviderRegistry

    class StubProvider:
        provider_id = "ollama"
        model_id = "embeddinggemma"
        def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
            assert texts == ("fetchcore readiness",)
            return (tuple(0.1 for _ in range(vector_size)),)

    monkeypatch.setattr(
        EmbeddingProviderRegistry, "create", lambda self, config, **kwargs: StubProvider(),
    )
    monkeypatch.setattr(
        runtime_inspection.RuntimeInspectionService, "doctor",
        lambda self: _doctor(embedding_provider=("ok", None)),
    )
    runtime = SimpleNamespace(indexer=object(), fingerprint="fp1")
    fake = FakeStore(matches=identity_matches)
    monkeypatch.setattr(
        indexing, "build_index_context",
        lambda **kw: SimpleNamespace(store=fake, runtime=runtime),
    )
    monkeypatch.setattr(contract, "bind_indexing_contract_store", lambda store, indexer: fake)
    monkeypatch.setattr(index_operations, "repository_index_status", lambda store, runtime: {"state": "ready"})
    report = inspect_readiness(RuntimeInspectionService(FetchCoreRuntimeConfig(
        backend="solr", workspace=tmp_path, repository="demo/repo", revision="abc",
        retrieval=RetrievalConfig.from_mapping({"dense": {"enabled": True}}),
    )))
    assert report.mode == "dense"
    assert report.checks["snapshot"].state == "ready"
    assert report.checks["currentness"].state == "ready"
    assert report.checks["embedding"].state == "incompatible"
    assert report.checks["embedding"].category == expected
    assert not report.ready


def test_solr_configured_vector_dimensions_fail_before_provider_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    import fetchcore.application.runtime_inspection as inspection

    monkeypatch.setattr(
        inspection.RuntimeInspectionService,
        "doctor",
        lambda self: _doctor(embedding_provider=("ok", None)),
    )

    class ForbiddenProviders:
        def create(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("invalid configured dimensions must fail before provider probing")

    inspector = RuntimeInspectionService(
        FetchCoreRuntimeConfig(
            backend="solr",
            workspace=tmp_path,
            repository="demo/repo",
            revision="abc",
            retrieval=RetrievalConfig.from_mapping({
                "dense": {
                    "enabled": True,
                    "embedding": {
                        "provider": "ollama",
                        "model": "embeddinggemma",
                        "dimensions": 16,
                    },
                },
            }),
        ),
        providers=ForbiddenProviders(),  # type: ignore[arg-type]
    )
    report = inspect_readiness(inspector)

    assert report.mode == "dense"
    assert report.checks["embedding"].state == "incompatible"
    assert report.checks["embedding"].category == "vector_dimensions_mismatch"
    assert report.checks["snapshot"].state == "unknown"
    assert not report.ready
