from __future__ import annotations

import json
from pathlib import Path

import pytest

from fetchcore.baseline import (
    BASELINE_ARTIFACT_SCHEMA_VERSION,
    BASELINE_GROUND_TRUTH_SCHEMA_VERSION,
    BASELINE_METRICS_SCHEMA_VERSION,
    BASELINE_V1_ARMS,
    BASELINE_V1_METRICS_SPEC,
    BaselineArm,
    BaselineConfigurationError,
    BaselineEvidenceAlternative,
    BaselineEvidenceRegion,
    BaselineEvidenceRequirement,
    BaselineGroundTruth,
    BaselineQueryCase,
    BaselineRunner,
    build_baseline_artifact,
    capture_baseline_environment,
    evaluate_retrieval,
    load_baseline_suite,
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


def _result(
    identifier: str,
    *,
    source_ref: str | None = None,
    line_start: int | None = 1,
    line_end: int | None = 10,
) -> SearchResult:
    return SearchResult(
        id=identifier,
        source=f"{identifier}.py",
        source_ref=source_ref or f"repository://repo/{identifier}.py",
        knowledge_type="code",
        content=f"class {identifier}: pass",
        score=1.0,
        line_start=line_start,
        line_end=line_end,
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


def _requirement(
    requirement_id: str,
    source_ref: str,
    line_start: int,
    line_end: int,
) -> BaselineEvidenceRequirement:
    return BaselineEvidenceRequirement(
        id=requirement_id,
        alternatives=(
            BaselineEvidenceAlternative(
                regions=(
                    BaselineEvidenceRegion(
                        source_ref=source_ref,
                        line_start=line_start,
                        line_end=line_end,
                    ),
                )
            ),
        ),
    )


def test_evaluate_retrieval_measures_evidence_not_only_source_identity() -> None:
    response = _response(
        (
            _result(
                "a",
                source_ref="repository://repo/a.py",
                line_start=10,
                line_end=19,
            ),
            _result(
                "b",
                source_ref="repository://repo/b.py",
                line_start=1,
                line_end=5,
            ),
            _result(
                "c",
                source_ref="repository://repo/c.py",
                line_start=30,
                line_end=39,
            ),
        ),
        candidates_examined=8,
    )
    truth = BaselineGroundTruth(
        query_id="q1",
        requirements=(
            _requirement("a-evidence", "repository://repo/a.py", 12, 15),
            _requirement("c-evidence", "repository://repo/c.py", 32, 35),
        ),
    )

    observed = evaluate_retrieval(response, truth, iteration=1, k=3)

    assert observed.source_recall_at_k == 1.0
    assert observed.evidence_recall_at_k == 1.0
    assert observed.evidence_ground_truth_recovered == 2
    assert observed.evidence_ground_truth_total == 2
    assert observed.span_coverage_at_k == 1.0
    assert observed.evidence_reciprocal_rank == 1.0
    assert observed.evidence_ndcg_at_k == pytest.approx(0.919721, abs=1e-6)
    assert observed.useful_context_line_ratio == pytest.approx(0.32, abs=1e-6)
    assert observed.returned_line_count == 25
    assert observed.candidates_examined == 8
    assert observed.candidates_returned == 3
    assert observed.result_spans[0].source_ref == "repository://repo/a.py"
    assert observed.result_spans[0].line_start == 10
    assert observed.result_spans[0].line_end == 19
    assert observed.telemetry["fingerprints"] == {
        "projection": "sha256:projection-v3"
    }


def test_source_recall_does_not_claim_evidence_when_wrong_span_is_returned() -> None:
    source_ref = "repository://repo/shared.py"
    response = _response(
        (_result("wrong-region", source_ref=source_ref, line_start=1, line_end=10),)
    )
    truth = BaselineGroundTruth(
        query_id="q1",
        requirements=(_requirement("needed", source_ref, 50, 60),),
    )

    observed = evaluate_retrieval(response, truth, iteration=1, k=1)

    assert observed.source_recall_at_k == 1.0
    assert observed.evidence_recall_at_k == 0.0
    assert observed.span_coverage_at_k == 0.0
    assert observed.evidence_reciprocal_rank == 0.0
    assert observed.evidence_ndcg_at_k == 0.0
    assert observed.useful_context_line_ratio == 0.0


def test_evidence_can_be_completed_by_multiple_chunks() -> None:
    source_ref = "repository://repo/shared.py"
    response = _response(
        (
            _result("first-half", source_ref=source_ref, line_start=10, line_end=14),
            _result("second-half", source_ref=source_ref, line_start=15, line_end=19),
        )
    )
    truth = BaselineGroundTruth(
        query_id="q1",
        requirements=(_requirement("needed", source_ref, 10, 19),),
    )

    observed = evaluate_retrieval(response, truth, iteration=1, k=2)

    assert observed.evidence_recall_at_k == 1.0
    assert observed.span_coverage_at_k == 1.0
    assert observed.evidence_reciprocal_rank == 1.0
    assert observed.evidence_ndcg_at_k == 1.0
    assert observed.useful_context_line_ratio == 1.0


def test_duplicate_returned_spans_consume_context_without_duplicate_gain() -> None:
    source_ref = "repository://repo/shared.py"
    response = _response(
        (
            _result("chunk-a", source_ref=source_ref, line_start=10, line_end=19),
            _result("chunk-b", source_ref=source_ref, line_start=10, line_end=19),
        )
    )
    truth = BaselineGroundTruth(
        query_id="q1",
        requirements=(_requirement("needed", source_ref, 12, 15),),
    )

    observed = evaluate_retrieval(response, truth, iteration=1, k=2)

    assert observed.evidence_recall_at_k == 1.0
    assert observed.span_coverage_at_k == 1.0
    assert observed.evidence_ndcg_at_k == 1.0
    assert observed.returned_line_count == 20
    assert observed.useful_context_line_ratio == pytest.approx(0.2, abs=1e-6)


def test_evaluate_retrieval_fails_closed_when_result_spans_are_missing() -> None:
    response = _response(
        (_result("a", line_start=None, line_end=None),)
    )
    truth = BaselineGroundTruth(
        query_id="q1",
        requirements=(
            _requirement("needed", "repository://repo/a.py", 1, 2),
        ),
    )

    with pytest.raises(BaselineConfigurationError, match="line_start"):
        evaluate_retrieval(response, truth, iteration=1, k=1)


def test_baseline_artifact_aggregates_evidence_metrics_and_fingerprints() -> None:
    truth = BaselineGroundTruth(
        query_id="q1",
        requirements=(
            _requirement("needed", "repository://repo/a.py", 1, 2),
        ),
    )
    observations = tuple(
        evaluate_retrieval(
            _response(
                (_result("a", line_start=1, line_end=2),),
                duration_ms=duration,
            ),
            truth,
            iteration=iteration,
            k=1,
        )
        for iteration, duration in enumerate((10, 20, 30, 40), start=1)
    )
    gt_fingerprint = mapping_fingerprint({"suite": "fixture-v2"})

    artifact = build_baseline_artifact(
        arm=BaselineArm("fixture-lexical", "fixture", "lexical"),
        corpus={"repository": "repo", "revision": "abc123"},
        config={"max_results": 10},
        observations=observations,
        ground_truth_fingerprint=gt_fingerprint,
    )

    assert artifact.schema_version == BASELINE_ARTIFACT_SCHEMA_VERSION
    assert artifact.ground_truth_schema_version == BASELINE_GROUND_TRUTH_SCHEMA_VERSION
    assert artifact.ground_truth_fingerprint == gt_fingerprint
    assert artifact.metrics_schema_version == BASELINE_METRICS_SCHEMA_VERSION
    assert artifact.metrics_fingerprint == mapping_fingerprint(BASELINE_V1_METRICS_SPEC)
    assert artifact.aggregate["duration_ms"] == {"p50": 20, "p95": 40, "p99": 40}
    assert artifact.aggregate["evidence_recall_at_k_mean"] == 1.0
    assert artifact.aggregate["span_coverage_at_k_mean"] == 1.0
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


def test_frozen_v1_arm_matrix_is_complete_and_unique() -> None:
    expected = (
        ("local-lexical", "local-semantic", "lexical"),
        ("solr-lexical", "solr", "lexical"),
        ("solr-dense", "solr", "dense"),
        ("solr-hybrid", "solr", "hybrid"),
        ("opensearch-lexical", "opensearch", "lexical"),
        ("opensearch-dense", "opensearch", "dense"),
        ("opensearch-hybrid", "opensearch", "hybrid"),
        ("qdrant-dense", "qdrant-vector", "dense"),
        ("qdrant-hybrid", "qdrant-vector", "hybrid"),
    )

    observed = tuple((arm.id, arm.backend, arm.mode) for arm in BASELINE_V1_ARMS)

    assert observed == expected
    assert len({arm.id for arm in BASELINE_V1_ARMS}) == len(BASELINE_V1_ARMS)


def test_baseline_arm_is_parsed_from_manifest_mapping() -> None:
    arm = BaselineArm.from_mapping(
        {"id": "fixture-hybrid", "backend": "fixture", "mode": "hybrid"}
    )

    assert arm == BaselineArm("fixture-hybrid", "fixture", "hybrid")


@pytest.mark.parametrize(
    "value, message",
    [
        ({"backend": "fixture", "mode": "lexical"}, "non-empty id and backend"),
        ({"id": "fixture", "mode": "lexical"}, "non-empty id and backend"),
        ({"id": None, "backend": "fixture", "mode": "lexical"}, "non-empty id"),
        (
            {"id": "fixture-invalid", "backend": "fixture", "mode": "invalid"},
            "unsupported mode",
        ),
    ],
)
def test_baseline_arm_manifest_mapping_fails_closed(
    value: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(BaselineConfigurationError, match=message):
        BaselineArm.from_mapping(value)


def test_baseline_manifest_matches_frozen_arm_matrix() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads(
        (root / "benchmarks/baseline-v1/manifest.json").read_text(encoding="utf-8")
    )

    assert tuple(
        (item["id"], item["backend"], item["mode"])
        for item in manifest["arms"]
    ) == tuple(
        (arm.id, arm.backend, arm.mode)
        for arm in BASELINE_V1_ARMS
    )


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
        return _response(
            (
                _result(
                    "target",
                    source_ref="repository://repo/target.py",
                    line_start=10,
                    line_end=20,
                ),
            ),
            duration_ms=5,
        )


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
                requirements=(
                    _requirement(
                        "target-region",
                        "repository://repo/target.py",
                        12,
                        15,
                    ),
                ),
            ),
        ),
        corpus={"repository": "repo", "revision": "abc123"},
        ground_truth_fingerprint=mapping_fingerprint({"suite": "fixture-v2"}),
        config_identity={"projection": "3"},
    )

    assert backend.calls == 4
    assert len(artifact.observations) == 3
    assert artifact.aggregate["run_count"] == 3
    assert artifact.config["repetitions"] == 3
    assert artifact.config["warmups"] == 1


def test_baseline_suite_loads_evidence_regions_as_canonical_source_refs(
    tmp_path: Path,
) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
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
                        "required_evidence": [
                            {
                                "id": "needle-region",
                                "alternatives": [
                                    {
                                        "regions": [
                                            {
                                                "path": "src/needle.py",
                                                "line_start": 10,
                                                "line_end": 20,
                                            }
                                        ]
                                    }
                                ],
                            }
                        ],
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
    region = suite.cases[0].requirements[0].alternatives[0].regions[0]
    assert region.source_ref == "repository://owner/repo/src/needle.py"
    assert (region.line_start, region.line_end) == (10, 20)
    assert suite.fingerprint.startswith("sha256:")


def test_evidence_alternative_rejects_overlapping_regions() -> None:
    source_ref = "repository://repo/shared.py"

    with pytest.raises(BaselineConfigurationError, match="overlapping"):
        BaselineEvidenceAlternative(
            regions=(
                BaselineEvidenceRegion(source_ref, 10, 20),
                BaselineEvidenceRegion(source_ref, 20, 30),
            )
        )


def test_suite_validates_regions_against_workspace(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "needle.py").write_text("a\nb\nc\n", encoding="utf-8")
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "corpus": {
                    "repository": "owner/repo",
                    "revision": "abc123",
                },
                "queries": [
                    {
                        "id": "q1",
                        "intent": {"semantic_terms": ["needle"]},
                        "required_evidence": [
                            {
                                "id": "needle",
                                "alternatives": [
                                    {
                                        "regions": [
                                            {
                                                "path": "src/needle.py",
                                                "line_start": 2,
                                                "line_end": 3,
                                            }
                                        ]
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_baseline_suite(suite_path)
    suite.validate_workspace_regions(tmp_path)

    bad_region = BaselineEvidenceRegion(
        "repository://owner/repo/src/needle.py",
        2,
        4,
    )
    bad_suite = type(suite)(
        schema_version=suite.schema_version,
        corpus=suite.corpus,
        cases=(
            BaselineQueryCase(
                query_id="q1",
                intent=suite.cases[0].intent,
                requirements=(
                    BaselineEvidenceRequirement(
                        id="needle",
                        alternatives=(
                            BaselineEvidenceAlternative(regions=(bad_region,)),
                        ),
                    ),
                ),
            ),
        ),
        fingerprint=suite.fingerprint,
    )
    with pytest.raises(BaselineConfigurationError, match="exceeds file length"):
        bad_suite.validate_workspace_regions(tmp_path)


def test_baseline_suite_rejects_source_only_v1_schema(tmp_path: Path) -> None:
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
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
    truth = BaselineGroundTruth(
        query_id="q1",
        requirements=(
            _requirement("needed", "repository://repo/a.py", 1, 2),
        ),
    )
    observation = evaluate_retrieval(
        _response((_result("a", line_start=1, line_end=2),), duration_ms=10),
        truth,
        iteration=1,
        k=1,
    )

    artifact = build_baseline_artifact(
        arm=BaselineArm("fixture-lexical", "fixture", "lexical"),
        corpus={"repository": "repo", "revision": "abc123"},
        config={"max_results": 10},
        observations=(observation,),
        ground_truth_fingerprint=mapping_fingerprint({"suite": "fixture-v2"}),
        environment=environment.to_mapping(),
    )

    assert artifact.environment["profile_id"] == "fixture-v1"
    assert artifact.environment["services"] == {"solr": "9.x"}


def test_repository_baseline_v1_suite_is_evidence_region_frozen() -> None:
    root = Path(__file__).resolve().parents[1]
    suite = load_baseline_suite(root / "benchmarks/baseline-v1/ground-truth.json")

    assert suite.schema_version == 2
    assert suite.corpus["repository"] == "oalangomes/NeuroTrack_MS"
    assert suite.corpus["revision"] == "74ce3244956a65baed61f1dae6f498cb2be4f3b7"
    assert len(suite.cases) == 20
    assert len({case.query_id for case in suite.cases}) == 20
    assert sum(len(case.requirements) for case in suite.cases) == 42
    assert suite.fingerprint.startswith("sha256:")



def test_baseline_suite_validates_evidence_regions_against_workspace(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "needle.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "corpus": {
                    "repository": "owner/repo",
                    "revision": "abc123",
                },
                "queries": [
                    {
                        "id": "q1",
                        "intent": {"semantic_terms": ["needle"]},
                        "required_evidence": [
                            {
                                "id": "needle-region",
                                "alternatives": [
                                    {
                                        "regions": [
                                            {
                                                "path": "src/needle.py",
                                                "line_start": 2,
                                                "line_end": 3,
                                            }
                                        ]
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_baseline_suite(suite_path)
    suite.validate_workspace_regions(tmp_path)


def test_baseline_suite_rejects_evidence_region_beyond_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src" / "needle.py"
    source.parent.mkdir(parents=True)
    source.write_text("one\ntwo\nthree\n", encoding="utf-8")
    suite_path = tmp_path / "suite.json"
    suite_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "corpus": {
                    "repository": "owner/repo",
                    "revision": "abc123",
                },
                "queries": [
                    {
                        "id": "q1",
                        "intent": {"semantic_terms": ["needle"]},
                        "required_evidence": [
                            {
                                "id": "needle-region",
                                "alternatives": [
                                    {
                                        "regions": [
                                            {
                                                "path": "src/needle.py",
                                                "line_start": 2,
                                                "line_end": 4,
                                            }
                                        ]
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    suite = load_baseline_suite(suite_path)
    with pytest.raises(BaselineConfigurationError, match="exceeds file length"):
        suite.validate_workspace_regions(tmp_path)
