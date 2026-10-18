from __future__ import annotations

import pytest

from fetchcore.search import KnowledgeSearchResponse
from fetchcore.telemetry import (
    IndexStageTelemetry,
    IndexSyncTelemetry,
    RetrievalStageTelemetry,
    RetrievalTelemetry,
)


def test_retrieval_telemetry_serializes_backend_neutral_shape() -> None:
    telemetry = RetrievalTelemetry(
        mode="hybrid",
        duration_ms=42,
        candidates_examined=15,
        candidates_returned=5,
        returned_chars=1200,
        stages=(
            RetrievalStageTelemetry(
                name="lexical",
                duration_ms=10,
                candidates_out=10,
            ),
            RetrievalStageTelemetry(
                name="dense",
                duration_ms=20,
                candidates_out=10,
            ),
            RetrievalStageTelemetry(
                name="fusion",
                duration_ms=2,
                candidates_in=20,
                candidates_out=15,
            ),
        ),
        fingerprints={"projection": "sha256:projection"},
        budget={"max_results": 5, "output_truncated": True},
    )

    response = KnowledgeSearchResponse(
        backend="fixture",
        status="found",
        duration_ms=42,
        telemetry=telemetry,
    )
    payload = response.to_mapping()

    assert payload["telemetry"] == telemetry.to_mapping()
    assert payload["telemetry"]["mode"] == "hybrid"  # type: ignore[index]
    assert payload["telemetry"]["candidates_examined"] == 15  # type: ignore[index]


def test_retrieval_telemetry_rejects_impossible_candidate_counts() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        RetrievalTelemetry(
            mode="lexical",
            duration_ms=1,
            candidates_examined=1,
            candidates_returned=2,
            returned_chars=0,
        )


def test_index_sync_telemetry_reports_embedding_throughput() -> None:
    telemetry = IndexSyncTelemetry(
        duration_ms=1000,
        backend_writes=10,
        backend_deletes=2,
        embedding_requests=2,
        embedding_vectors=10,
        embedding_duration_ms=500,
    )

    payload = telemetry.to_mapping()

    assert payload["embedding_throughput_vectors_per_second"] == 20.0
    assert payload["backend_writes"] == 10
    assert payload["backend_deletes"] == 2


def test_index_stages_preserve_only_measured_phases_and_roundtrip() -> None:
    stages = IndexStageTelemetry(
        snapshot_load_ms=0,
        previous_documents_load_ms=27_819,
        canonical_delta_index_ms=915,
    )
    assert stages.to_mapping() == {
        "snapshot_load_ms": 0,
        "previous_documents_load_ms": 27_819,
        "canonical_delta_index_ms": 915,
    }
    assert IndexStageTelemetry.from_mapping(stages.to_mapping()) == stages
    assert IndexStageTelemetry(canonical_full_index_ms=3).to_mapping() == {
        "canonical_full_index_ms": 3,
    }


@pytest.mark.parametrize("value", [None, True, -1, 1.5, "17", float("nan")])
def test_index_stages_reject_unmeasured_or_invalid_fake_durations(value: object) -> None:
    with pytest.raises(ValueError, match="Invalid indexing stage"):
        IndexStageTelemetry.from_mapping({"snapshot_load_ms": value})


def test_index_stages_reject_unknown_metrics_without_silently_dropping_them() -> None:
    with pytest.raises(ValueError, match="Unknown indexing stage"):
        IndexStageTelemetry.from_mapping({"backend_magic_ms": 2})
    with pytest.raises(ValueError, match="Invalid indexing stage"):
        IndexStageTelemetry(snapshot_load_ms=-1)
