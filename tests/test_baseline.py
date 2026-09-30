from __future__ import annotations

import json
from pathlib import Path

import pytest

from fetchcore.baseline import (
    BASELINE_V1_ARMS,
    BaselineArm,
    BaselineConfigurationError,
    BaselineGroundTruth,
    BaselineQueryCase,
    BaselineRunner,
    capture_baseline_environment,
    load_baseline_suite,
    build_baseline_artifact,
    evaluate_retrieval,
    mapping_fingerprint,
    percentile,
    validate_baseline_arm,
)
from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.search import (
    KnowledgeSearchResponse,
    SearchIntent,
    SearchResult,
    SearchScope,
)
from fetchcore.telemetry import RetrievalTelemetry


def _result(identifier: str, *, source_ref: str | None = None) -> SearchResult:
    return SearchResult(
        id=identifier,
        source=f"{identifier}.py",
        source_ref=source_ref or f"repository://repo/{identifier}.py",
        knowledge_type="code",
        content=f"class {identifier}: pass",
        score=1.0,
    )


def _response(
    results: tuple[SearchResult, ...],
    *,
    duration_ms: int = 10,
    candidates_examined: int | None = None,
) -> KnowledgeSearchResponse:
    telemetry = RetrievalTelemetry(
        mode="lexical",
        duration_ms=duration_ms,
        candidates_examined=(
            len(results) if candidates_examined is None else candidates_examined
        ),
        candidates_returned=len(results),
        returned_chars=sum(len(item.content) for item in results),
        fingerprints={"projection": "sha256:projection-v3"},
    )
    return KnowledgeSearchResponse(
        backend="fixture",
        status="found",
        results=results,
        duration_ms=duration_ms,
        telemetry=telemetry,
    )


def test_evaluate_retrieval_computes_quality_and_efficiency_metrics() -> None:
    response = _response(
        (_result("a"), _result("b"), _result("c")),
        candidates_examined=8,
    )
    truth = BaselineGroundTruth(query_id="q1", relevant=("a", "c"))

    observed = evaluate_retrieval(response, truth, iteration=1, k=3)

    assert observed.recall_at_k == 1.0
    assert observed.ground_truth_recovered == 2
    assert observed.reciprocal_rank == 1.0
    assert observed.ndcg_at_k == pytest.approx(0.919721, abs=1e-6)
    assert observed.useful_evidence_density == pytest.approx(0.666667, abs=1e-6)
    assert observed.candidates_examined == 8
    assert observed.candidates_returned == 3
    assert observed.telemetry["fingerprints"] == {
        "projection": "sha256:projection-v3"
    }


def test_evaluate_retrieval_deduplicates_relevant_identity_gain() -> None:
    shared_ref = "repository://repo/src/shared.py"
    response = _response(
        (
            _result("chunk-a", source_ref=shared_ref),
            _result("chunk-b", source_ref=shared_ref),
            _result("other"),
        )
    )
    truth = BaselineGroundTruth(
        query_id="q1",
        relevant=(shared_ref,),
        identity_field="source_ref",
    )

    observed = evaluate_retrieval(response, truth, iteration=1, k=3)

    assert observed.recall_at_k == 1.0
    assert observed.ground_truth_recovered == 1
    assert observed.reciprocal_rank == 1.0
    assert observed.ndcg_at_k == 1.0
    assert observed.useful_evidence_density == pytest.approx(0.333333, abs=1e-6)


def test_baseline_artifact_aggregates_repeated_latency_and_fingerprints() -> None:
    truth = BaselineGroundTruth(query_id="q1", relevant=("a",))
    observations = tuple(
        evaluate_retrieval(
            _response((_result("a"),), duration_ms=duration),
            truth,
            iteration=iteration,
            k=1,
        )
        for iteration, duration in enumerate((10, 20, 30, 40), start=1)
    )

    artifact = build_baseline_artifact(
        arm=BaselineArm("fixture-lexical", "fixture", "lexical"),
        corpus={"repository": "repo", "revision": "abc123"},
        config={"max_results": 10},
        observations=observations,
    )

    assert artifact.aggregate["duration_ms"] == {"p50": 20, "p95": 40, "p99": 40}
    assert artifact.aggregate["fingerprints"] == {
        "projection": ["sha256:projection-v3"]
    }
    assert artifact.config_fingerprint == mapping_fingerprint({"max_results": 10})
    json.dumps(artifact.to_mapping(), sort_keys=True)


def test_percentile_uses_deterministic_nearest_rank() -> None:
    assert percentile((40, 10, 30, 20), 0.50) == 20
    assert percentile((40, 10, 30, 20), 0.95) == 40
    assert percentile((40, 10, 30, 20), 0.99) == 40


def test_baseline_capability_validation_fails_closed() -> None:
    arm = BaselineArm("fixture-hybrid", "fixture", "hybrid")

    with pytest.raises(BaselineConfigurationError, match="unsupported"):
        validate_baseline_arm(
            arm,
            RetrievalBackendCapabilities(lexical=True, dense=False, hybrid=False),
        )


def test_frozen_v1_arm_ids_are_unique() -> None:
    assert len({arm.id for arm in BASELINE_V1_ARMS}) == len(BASELINE_V1_ARMS)


class _FixtureBackend:
    backend_id = "fixture"
    retrieval_capabilities = RetrievalBackendCapabilities(lexical=True)

    def __init__(self) -> None:
        self.calls = 0

    def search(
        self,
        intent: SearchIntent,
        scope: SearchScope,
        requirements: tuple[object, ...],
    ) -> KnowledgeSearchResponse:
        del intent, scope, requirements
        self.calls += 1
        return _response((_result("target"),), duration_ms=5)


def test_baseline_runner_excludes_warmups_and_records_measured_runs(
    tmp_path: Path,
) -> None:
    backend = _FixtureBackend()
    runner = BaselineRunner(
        backend=backend,  # type: ignore[arg-type]
        arm=BaselineArm("fixture-lexical", "fixture", "lexical"),
        scope=SearchScope(
            workspace=tmp_path,
            revision="abc123",
            repository="repo",
            knowledge_types=("code",),
            max_results=5,
        ),
        repetitions=3,
        warmups=1,
        k=5,
    )

    artifact = runner.run(
        (
            BaselineQueryCase(
                query_id="q1",
                intent=SearchIntent(exact_terms=("target",)),
                relevant=("target",),
            ),
        ),
        corpus={"repository": "repo", "revision": "abc123"},
        config_identity={"projection": "3"},
    )

    assert backend.calls == 4
    assert len(artifact.observations) == 3
    assert artifact.aggregate["run_count"] == 3
    assert artifact.config["repetitions"] == 3
    assert artifact.config["warmups"] == 1



def test_baseline_suite_loads_path_ground_truth_as_canonical_source_refs(
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus": {
                    "repository": "owner/repo",
                    "revision": "abc123",
                },
                "queries": [
                    {
                        "id": "q1",
                        "intent": {
                            "exact_terms": ["NeedleService"],
                            "semantic_terms": ["needle implementation"],
                            "hints": ["service"],
                        },
                        "relevant_paths": ["src/needle.py"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_baseline_suite(suite_path)

    assert suite.corpus == {"repository": "owner/repo", "revision": "abc123"}
    assert suite.cases[0].query_id == "q1"
    assert suite.cases[0].intent.exact_terms == ("NeedleService",)
    assert suite.cases[0].relevant == (
        "repository://owner/repo/src/needle.py",
    )


def test_baseline_suite_rejects_unsupported_schema(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "corpus": {"repository": "owner/repo", "revision": "abc123"},
                "queries": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BaselineConfigurationError, match="schema_version"):
        load_baseline_suite(suite_path)


def test_baseline_environment_is_recorded_in_artifact() -> None:
    environment = capture_baseline_environment(
        "fixture-v1",
        services={"solr": "9.x"},
        embedding={
            "provider": "ollama",
            "model": "embeddinggemma",
            "dimensions": 768,
        },
    )
    observation = evaluate_retrieval(
        _response((_result("a"),), duration_ms=10),
        BaselineGroundTruth(query_id="q1", relevant=("a",)),
        iteration=1,
        k=1,
    )

    artifact = build_baseline_artifact(
        arm=BaselineArm("fixture-lexical", "fixture", "lexical"),
        corpus={"repository": "repo", "revision": "abc123"},
        config={"max_results": 10},
        observations=(observation,),
        environment=environment.to_mapping(),
    )

    assert artifact.environment["profile_id"] == "fixture-v1"
    assert artifact.environment["services"] == {"solr": "9.x"}


def test_repository_baseline_v1_suite_is_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    suite = load_baseline_suite(root / "benchmarks/baseline-v1/ground-truth.json")

    assert suite.schema_version == 1
    assert suite.corpus["repository"] == "oalangomes/NeuroTrack_MS"
    assert suite.corpus["revision"] == "74ce3244956a65baed61f1dae6f498cb2be4f3b7"
    assert len(suite.cases) == 20
    assert len({case.query_id for case in suite.cases}) == 20
