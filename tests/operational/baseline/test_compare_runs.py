"""Offline comparison stays deterministic and fails closed on broken artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

from fetchcore.baseline import (
    BASELINE_V1_ARMS,
    BaselineSuite,
    RetrievalQualityObservation,
    RetrievalResultSpan,
    build_baseline_artifact,
    build_context_yield_artifact,
)

BENCH = Path(__file__).resolve().parents[3] / "benchmarks" / "baseline-v1"
SPEC = importlib.util.spec_from_file_location("compare_runs", BENCH / "compare_runs.py")
assert SPEC is not None and SPEC.loader is not None
compare_runs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(compare_runs)


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate_order: tuple[str, ...] = ("good", "bad"),
) -> tuple[Path, Path]:
    raw = json.loads((BENCH / "ground-truth.json").read_text(encoding="utf-8"))
    raw["queries"] = raw["queries"][:1]
    raw["queries"][0]["required_evidence"] = raw["queries"][0]["required_evidence"][:1]
    _write(tmp_path / "ground-truth.json", raw)
    _write(tmp_path / "manifest.json", {})
    monkeypatch.setattr(compare_runs, "ROOT", tmp_path)
    suite = BaselineSuite.from_mapping(raw)
    region = suite.cases[0].requirements[0].alternatives[0].regions[0]
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    for directory, revision, order in (
        (baseline, "a" * 40, ("good", "bad")),
        (candidate, "b" * 40, candidate_order),
    ):
        directory.mkdir()
        artifacts = []
        for arm in BASELINE_V1_ARMS:
            fingerprints = {} if arm.id == "local-lexical" else {"projection": "sha256:projection"}
            if arm.mode in ("dense", "hybrid"):
                fingerprints["embedding_serializer"] = "sha256:serializer"
            if arm.mode == "hybrid":
                fingerprints["fusion_input"] = "sha256:input"
                fingerprints["fusion_output"] = "sha256:output"
            if arm.id == "qdrant-hybrid":
                fingerprints["sparse_analyzer"] = "sparse-version"
            spans = tuple(
                RetrievalResultSpan(
                    rank=rank,
                    id=name,
                    source_ref=region.source_ref if name == "good" else "repository://test/other",
                    line_start=region.line_start if name == "good" else 1,
                    line_end=region.line_end if name == "good" else 1,
                )
                for rank, name in enumerate(order, 1)
            )
            found = "good" in order
            observations = tuple(
                RetrievalQualityObservation(
                    query_id=suite.cases[0].query_id,
                    iteration=iteration,
                    source_recall_at_k=float(found),
                    evidence_recall_at_k=float(found),
                    evidence_ground_truth_recovered=int(found),
                    evidence_ground_truth_total=1,
                    span_coverage_at_k=float(found),
                    evidence_reciprocal_rank=(1 / (order.index("good") + 1)) if found else 0,
                    evidence_ndcg_at_k=float(found),
                    useful_context_line_ratio=0.5 if found else 0,
                    duration_ms=10 + iteration,
                    candidates_examined=2,
                    candidates_returned=len(spans),
                    returned_chars=30,
                    returned_line_count=sum(span.line_count for span in spans),
                    result_spans=spans,
                    telemetry={"fingerprints": fingerprints},
                )
                for iteration in range(1, 21)
            )
            artifact = build_baseline_artifact(
                arm=arm,
                corpus=suite.corpus,
                config={
                    "k": 20,
                    "warmups": 1,
                    "repetitions": 20,
                    "identity": {
                        "fetchcore_revision": revision,
                        "corpus_revision": suite.corpus["revision"],
                        "manifest_fingerprint": compare_runs.mapping_fingerprint({}),
                    },
                },
                observations=observations,
                ground_truth_fingerprint=suite.fingerprint,
            ).to_mapping()
            _write(directory / f"{arm.id}.json", artifact)
            artifacts.append(artifact)
        _write(
            directory / "context-yield.json",
            build_context_yield_artifact(suite=suite, retrieval_artifacts=artifacts),
        )
        _write(
            directory / "run.json",
            {
                "schema_version": 1,
                "protocol": "fetchcore.baseline-v1",
                "status": "complete",
                "fetchcore_revision": revision,
                "corpus": suite.corpus,
                "arms": [arm.to_mapping() for arm in BASELINE_V1_ARMS],
                "artifacts": [f"{arm.id}.json" for arm in BASELINE_V1_ARMS],
                "prepare_index": False,
                "indexing_artifact": None,
            },
        )
    return baseline, candidate


def test_order_only_and_offline_determinism(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline, candidate = _fixture(tmp_path, monkeypatch, candidate_order=("bad", "good"))
    first = compare_runs.compare(baseline, candidate)
    arm = first["arms"]["qdrant-hybrid"]
    query = next(iter(arm["queries"].values()))
    assert query["ranking"]["ordering_only"] is True
    assert query["ranking"]["top_k_membership_changed"] is False
    assert query["ranking"]["lost_evidence"] == []
    assert query["metrics"]["evidence_recall_at_k"]["delta"] == 0
    assert query["metrics"]["evidence_reciprocal_rank"]["delta"] == -0.5
    assert json.dumps(first, sort_keys=True) == json.dumps(
        compare_runs.compare(baseline, candidate), sort_keys=True
    )


def test_real_evidence_loss(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    baseline, candidate = _fixture(tmp_path, monkeypatch, candidate_order=("bad",))
    query = next(
        iter(compare_runs.compare(baseline, candidate)["arms"]["qdrant-hybrid"]["queries"].values())
    )
    assert query["metrics"]["evidence_recall_at_k"]["delta"] == -1
    assert query["ranking"]["lost_evidence"] == ["owner-scoped-update"]


@pytest.mark.parametrize(
    "mutation,error",
    [
        ("schema", "incompatible schema_version"),
        ("fingerprint", "config fingerprint mismatch"),
        ("metrics", "incompatible metrics_fingerprint"),
        ("projection", "projection fingerprint mismatch"),
        ("incomplete", "cannot read"),
        ("context", "context yield differs"),
    ],
)
def test_rejects_corrupt_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
    error: str,
) -> None:
    baseline, candidate = _fixture(tmp_path, monkeypatch)
    target = candidate / "qdrant-hybrid.json"
    if mutation == "incomplete":
        target.unlink()
    elif mutation == "context":
        target = candidate / "context-yield.json"
        data = json.loads(target.read_text(encoding="utf-8"))
        data["arms"][0]["observations"][0]["budgets"][0]["context_yield"] = 0
        _write(target, data)
    else:
        data = json.loads(target.read_text(encoding="utf-8"))
        if mutation == "schema":
            data["schema_version"] = 99
        elif mutation == "metrics":
            data["metrics_fingerprint"] = "sha256:wrong"
        elif mutation == "projection":
            for observation in data["observations"]:
                observation["telemetry"]["fingerprints"]["projection"] = "sha256:changed"
            data["aggregate"]["fingerprints"]["projection"] = ["sha256:changed"]
        else:
            data["config_fingerprint"] = "sha256:wrong"
        _write(target, data)
    with pytest.raises(compare_runs.ComparisonError, match=error):
        compare_runs.compare(baseline, candidate)


def test_no_indexing_telemetry_or_service_required(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline, candidate = _fixture(tmp_path, monkeypatch)
    assert not (candidate / "indexing.json").exists()
    import socket

    def blocked(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("network access is forbidden")

    monkeypatch.setattr(socket, "create_connection", blocked)
    assert len(compare_runs.compare(baseline, candidate)["arms"]) == 9
