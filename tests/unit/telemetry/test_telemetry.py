from __future__ import annotations

import pytest

from fetchcore.search import KnowledgeSearchResponse
from fetchcore.telemetry import (
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
