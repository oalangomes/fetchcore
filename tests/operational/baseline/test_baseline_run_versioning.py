from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider
from fetchcore.adapters.retrieval.qdrant.backend import QdrantVectorSettings
from fetchcore.application import BackendRegistry
from fetchcore.retrieval.contracts import SearchScope


ROOT = Path(__file__).resolve().parents[3]
RUN_PATH = ROOT / "benchmarks" / "baseline-v1" / "run.py"
SPEC = importlib.util.spec_from_file_location("fetchcore_baseline_v1_run", RUN_PATH)
assert SPEC is not None
assert SPEC.loader is not None
RUN_MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = RUN_MODULE
SPEC.loader.exec_module(RUN_MODULE)
BENCH_SPEC = importlib.util.spec_from_file_location("fetchcore_baseline_v1_bench", RUN_PATH.with_name("run_bench.py"))
assert BENCH_SPEC is not None and BENCH_SPEC.loader is not None
BENCH_MODULE = importlib.util.module_from_spec(BENCH_SPEC)
sys.modules[BENCH_SPEC.name] = BENCH_MODULE
BENCH_SPEC.loader.exec_module(BENCH_MODULE)


def test_new_bench_run_config_changes_only_in_memory_manifest(tmp_path: Path) -> None:
    frozen = RUN_MODULE._load_json(RUN_MODULE.MANIFEST_PATH)
    before = RUN_MODULE.MANIFEST_PATH.read_bytes()
    corpus = {"repository": "oalangomes/NeuroTrack_MS", "revision": "a" * 40}
    resource = "fc-bench-neurotrack_ms-" + hashlib.sha256(
        corpus["repository"].encode()
    ).hexdigest()[:16]
    config = {
        "schema_version": 1, "environment": "bench", **corpus,
        "owner": "test-run", "baseline_id": "bench-smoke", "provisioning": "reuse_only",
        "cleanup": "retain", "performance_comparable": False,
        "dedicated": False, "exclusive_host": False,
        "infrastructure_project": "fetchcore-shared",
        "endpoints": {"solr": "http://127.0.0.1:18983/solr",
                      "opensearch": "http://127.0.0.1:19200",
                      "qdrant": "http://127.0.0.1:16333",
                      "ollama": "http://127.0.0.1:11434"},
        "resources": {"solr": resource, "opensearch": resource,
                      "qdrant-vector": resource},
        "embedding": {"provider": "ollama", "model": "embeddinggemma", "dimensions": 768},
    }
    path = tmp_path / "run.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    parsed = BENCH_MODULE.load_run_config(path, corpus)
    effective = BENCH_MODULE.configured_manifest(frozen, parsed)
    assert effective["backend_settings"]["solr"]["collection"] == resource
    assert effective["environment"]["service_endpoints"]["solr"].endswith(":18983/solr")
    assert frozen["backend_settings"]["solr"]["collection"] == "fetchcore-knowledge"
    assert RUN_MODULE.MANIFEST_PATH.read_bytes() == before
    config["resources"]["solr"] = "fc-dev-neurotrack"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(RUN_MODULE.BaselineConfigurationError, match="repository routing"):
        BENCH_MODULE.load_run_config(path, corpus)


def test_solr_ann_breadth_changes_only_copied_bench_manifest() -> None:
    frozen = RUN_MODULE._load_json(RUN_MODULE.MANIFEST_PATH)
    effective = json.loads(json.dumps(frozen))
    BENCH_MODULE.configured_solr_ann_breadth(effective, 160)
    assert effective["backend_settings"]["solr"]["ann_search_breadth"] == 160
    assert effective["measurement"] == frozen["measurement"]
    assert "ann_search_breadth" not in frozen["backend_settings"]["solr"]
    for invalid in (79, 321):
        with pytest.raises(RUN_MODULE.BaselineConfigurationError):
            BENCH_MODULE.configured_solr_ann_breadth(effective, invalid)


def test_new_bench_identity_records_observed_projection_without_mutating_artifacts(tmp_path: Path) -> None:
    run = {"arms": [{"id": "solr-lexical", "backend": "solr"}]}
    indexing = {"indexing_fingerprint": "sha256:indexing"}
    retrieval = {"observations": [{"telemetry": {"fingerprints": {
        "projection": "sha256:projection",
    }}}]}
    for name, payload in (("run.json", run), ("indexing.json", indexing),
                          ("solr-lexical.json", retrieval)):
        (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")
    original = (tmp_path / "solr-lexical.json").read_bytes()
    assert BENCH_MODULE.observed_fingerprints(tmp_path) == {
        "indexing": "sha256:indexing",
        "retrieval": {"solr-lexical": {"projection": ["sha256:projection"]}},
    }
    assert (tmp_path / "solr-lexical.json").read_bytes() == original


class _FixtureBenchmarkAdapter:
    backend_id = "fixture"
    indexing_artifact_key = "fixture-index"

    def __init__(self) -> None:
        self.prepared_modes: frozenset[str] = frozenset()

    def build_settings(self, context: Any) -> dict[str, object]:
        return {"endpoint": context.endpoints["fixture"]}

    def probe_service_versions(self, context: Any) -> dict[str, str]:
        del context
        return {"fixture-service": "1.2.3"}

    def prepare_index(
        self,
        canonical: object,
        *,
        modes: frozenset[str],
        retrieval: object,
        context: Any,
    ) -> dict[str, object]:
        del canonical, retrieval, context
        self.prepared_modes = modes
        return {
            "mode": "fixture_rebuild",
            "duration_ms": 1,
            "telemetry": {"backend_writes": 2},
        }


def _fixture_context() -> object:
    return RUN_MODULE.BenchmarkBackendContext(
        endpoints={"fixture": "http://fixture.test"},
        embedding_identity={"provider": "fixture", "model": "fixture-model"},
        settings={"fixture": {}},
        diagnostic_candidate_window=12,
    )


def test_dense_arms_use_manifest_ollama_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FETCHCORE_OLLAMA_URL", "http://wrong-host:11434")
    context = RUN_MODULE.BenchmarkBackendContext(
        endpoints={
            "ollama": "http://benchmark-host:11434",
            "qdrant": "http://qdrant-host:6333",
        },
        embedding_identity={"provider": "ollama", "model": "embeddinggemma"},
        settings={"qdrant-vector": {}},
        diagnostic_candidate_window=80,
    )
    retrieval = RUN_MODULE._retrieval_for_mode(
        "dense", provider="ollama", model="embeddinggemma"
    )
    backends = BackendRegistry(providers=RUN_MODULE._embedding_providers(context))

    for backend_id in ("solr", "opensearch"):
        provider = backends.create(backend_id).embedding_factory(retrieval)
        assert isinstance(provider, OllamaEmbeddingProvider)
        assert provider.url == "http://benchmark-host:11434"

    qdrant = backends.create("qdrant-vector")
    settings = QdrantVectorSettings.from_mapping(
        RUN_MODULE.QdrantBenchmarkAdapter().build_settings(context)
    )
    provider = qdrant.embedding_factory(settings, retrieval.dense.embedding)
    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.url == "http://benchmark-host:11434"


def test_manifest_and_adapter_registry_are_the_backend_extension_boundary() -> None:
    arms = RUN_MODULE._manifest_arms(
        {
            "arms": [
                {"id": "fixture-lexical", "backend": "fixture", "mode": "lexical"},
                {"id": "fixture-dense", "backend": "fixture", "mode": "dense"},
            ]
        }
    )
    adapter = _FixtureBenchmarkAdapter()
    adapters = {adapter.backend_id: adapter}

    RUN_MODULE._validate_backend_adapters(arms, adapters)

    assert RUN_MODULE._select_arms(["fixture-dense"], arms) == (arms[1],)
    assert RUN_MODULE._backend_settings(
        arms[0], context=_fixture_context(), adapters=adapters
    ) == {"endpoint": "http://fixture.test"}
    assert RUN_MODULE._service_versions(
        arms, context=_fixture_context(), adapters=adapters
    ) == {"fixture-service": "1.2.3"}

    indexing = RUN_MODULE._prepare_backend_indexes(
        arms,
        canonical=object(),
        context=_fixture_context(),
        adapters=adapters,
    )

    assert adapter.prepared_modes == frozenset({"lexical", "dense"})
    assert indexing == {
        "fixture-index": {
            "mode": "fixture_rebuild",
            "duration_ms": 1,
            "telemetry": {"backend_writes": 2},
        }
    }


def test_manifest_backend_without_adapter_fails_explicitly() -> None:
    arms = RUN_MODULE._manifest_arms(
        {"arms": [{"id": "fixture-lexical", "backend": "fixture", "mode": "lexical"}]}
    )

    try:
        RUN_MODULE._validate_backend_adapters(arms, {})
    except RUN_MODULE.BaselineConfigurationError as exc:
        assert str(exc) == "baseline manifest has no benchmark adapter for: fixture"
    else:
        raise AssertionError("missing backend adapter should fail closed")


def test_allocate_run_output_dir_advances_monotonically(tmp_path: Path) -> None:
    (tmp_path / "V0.3").mkdir()
    (tmp_path / "V0.invalid").mkdir()
    (tmp_path / "legacy.json").write_text("{}", encoding="utf-8")

    version, output = RUN_MODULE._allocate_run_output_dir(tmp_path)

    assert version == "V0.4"
    assert output == tmp_path / "V0.4"
    assert output.is_dir()

    next_version, next_output = RUN_MODULE._allocate_run_output_dir(tmp_path)

    assert next_version == "V0.5"
    assert next_output == tmp_path / "V0.5"
    assert next_output.is_dir()


def test_resume_reuses_only_matching_artifact(tmp_path: Path) -> None:
    arm = RUN_MODULE._manifest_arms(
        {"arms": [{"id": "fixture-lexical", "backend": "fixture", "mode": "lexical"}]}
    )[0]
    scope = SearchScope(
        workspace=tmp_path,
        revision="revision",
        repository="owner/repo",
        knowledge_types=("code",),
        max_results=20,
    )
    suite = SimpleNamespace(
        fingerprint="sha256:ground-truth",
        corpus={"repository": "owner/repo", "revision": "revision"},
        cases=(object(), object()),
    )
    identity = {"fetchcore_revision": "source-revision"}
    environment = {"services": {"fixture": "1"}}
    measurement = {"repetitions": 2, "warmups": 1, "k": 20}
    config = {
        "scope": scope.to_mapping(),
        "arm": arm.to_mapping(),
        "repetitions": 2,
        "warmups": 1,
        "k": 20,
        "identity": identity,
    }
    artifact = {
        "schema_version": RUN_MODULE.BASELINE_ARTIFACT_SCHEMA_VERSION,
        "protocol": "fetchcore.baseline-v1",
        "ground_truth_schema_version": RUN_MODULE.BASELINE_GROUND_TRUTH_SCHEMA_VERSION,
        "ground_truth_fingerprint": suite.fingerprint,
        "metrics_schema_version": RUN_MODULE.BASELINE_METRICS_SCHEMA_VERSION,
        "metrics_fingerprint": RUN_MODULE.mapping_fingerprint(
            RUN_MODULE.BASELINE_V1_METRICS_SPEC
        ),
        "arm": arm.to_mapping(),
        "corpus": suite.corpus,
        "config": config,
        "config_fingerprint": RUN_MODULE.mapping_fingerprint(config),
        "environment": environment,
        "observations": [{}, {}, {}, {}],
        "aggregate": {"run_count": 4},
    }
    kwargs = {
        "arm": arm,
        "scope": scope,
        "identity": identity,
        "suite": suite,
        "environment": environment,
        "measurement": measurement,
    }

    RUN_MODULE._validate_resumed_artifact(artifact, **kwargs)

    incompatible = {**artifact, "config": {**config, "identity": {"fetchcore_revision": "other"}}}
    with pytest.raises(RUN_MODULE.BaselineConfigurationError, match="config"):
        RUN_MODULE._validate_resumed_artifact(incompatible, **kwargs)

    truncated = {**artifact, "observations": [{}]}
    with pytest.raises(RUN_MODULE.BaselineConfigurationError, match="observations"):
        RUN_MODULE._validate_resumed_artifact(truncated, **kwargs)


def test_resume_requires_existing_version_directory(tmp_path: Path) -> None:
    (tmp_path / "V0.2").mkdir()
    assert RUN_MODULE._resume_directory(tmp_path, "V0.2") == tmp_path / "V0.2"
    with pytest.raises(RUN_MODULE.BaselineConfigurationError, match="V0.N"):
        RUN_MODULE._resume_directory(tmp_path, "../other")
    with pytest.raises(RUN_MODULE.BaselineConfigurationError, match="does not exist"):
        RUN_MODULE._resume_directory(tmp_path, "V0.3")


def test_write_metrics_report_includes_retrieval_and_indexing_metrics(
    tmp_path: Path,
) -> None:
    run_manifest = {
        "run_version": "V0.4",
        "protocol": "fetchcore.baseline-v1",
        "status": "complete",
        "completed_at": "2026-10-01T18:00:00+00:00",
        "fetchcore_revision": "fetchcore-sha",
        "corpus": {
            "repository": "owner/repository",
            "revision": "corpus-sha",
        },
    }
    retrieval_artifacts = (
        {
            "arm": {"id": "solr-dense"},
            "aggregate": {
                "run_count": 400,
                "query_count": 20,
                "source_recall_at_k_mean": 0.9,
                "evidence_recall_at_k_mean": 0.816667,
                "span_coverage_at_k_mean": 0.848254,
                "evidence_mrr_mean": 0.746212,
                "evidence_ndcg_at_k_mean": 0.759794,
                "useful_context_line_ratio_mean": 0.046348,
                "evidence_ground_truth_recovered": 700,
                "duration_ms": {"p50": 33, "p95": 46, "p99": 54},
                "candidates_examined_mean": 80.0,
                "candidates_returned_mean": 20.0,
                "returned_chars_mean": 1000.5,
                "returned_line_count_mean": 120.25,
            },
        },
    )
    indexing_payload = {
        "canonical": {
            "files_discovered": 1189,
            "files_indexed": 894,
            "chunks_total": 9620,
            "duration_ms": 12359,
            "indexing_throughput_chunks_per_second": 778.38,
        },
        "backends": {
            "solr": {
                "mode": "full_rebuild",
                "duration_ms": 770005,
                "telemetry": {
                    "backend_writes": 9620,
                    "backend_deletes": 9620,
                    "embedding_duration_ms": 305202,
                    "embedding_vectors": 9620,
                    "embedding_throughput_vectors_per_second": 31.52,
                },
            }
        },
    }

    destination = RUN_MODULE._write_metrics_report(
        tmp_path,
        run_manifest=run_manifest,
        retrieval_artifacts=retrieval_artifacts,
        indexing_payload=indexing_payload,
    )

    assert destination == (tmp_path / "README.md").resolve()
    report = destination.read_text(encoding="utf-8")
    assert "# Baseline V0.4 metrics" in report
    assert "[solr-dense](solr-dense.json)" in report
    assert "0.816667" in report
    assert "33 ms | 46 ms | 54 ms" in report
    assert "| Arm | Observations | Queries |" in report
    assert "Files indexed: 894" in report
    assert "solr | full_rebuild | 770005 ms" in report
    assert "[run.json](run.json)" in report


def test_metrics_report_marks_indexing_as_not_requested() -> None:
    report = RUN_MODULE._render_metrics_report(
        run_manifest={
            "run_version": "V0.2",
            "protocol": "fetchcore.baseline-v1",
            "status": "complete",
            "completed_at": "2026-10-01T18:00:00+00:00",
            "fetchcore_revision": "fetchcore-sha",
            "corpus": {
                "repository": "owner/repository",
                "revision": "corpus-sha",
            },
        },
        retrieval_artifacts=(),
        indexing_payload=None,
    )

    assert "Index preparation was not requested for this run." in report
    assert "indexing.json" not in report


def test_metrics_report_discloses_lost_indexing_telemetry() -> None:
    report = RUN_MODULE._render_metrics_report(
        run_manifest={
            "run_version": "V0.2",
            "protocol": "fetchcore.baseline-v1",
            "status": "retrieval_complete",
            "completed_at": "2026-10-01T18:00:00+00:00",
            "fetchcore_revision": "source-revision",
            "corpus": {"repository": "owner/repo", "revision": "corpus-revision"},
            "prepare_index": True,
        },
        retrieval_artifacts=(),
        indexing_payload=None,
    )

    assert "telemetry was not preserved" in report
    assert "not complete for baseline promotion" in report
