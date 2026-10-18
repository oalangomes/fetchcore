"""Optional artifact gate for the controlled, read-only Solr ANN experiment."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.mark.solr_integration
@pytest.mark.parametrize("arm", ["solr-dense", "solr-hybrid"])
def test_solr_ann_bench_recovers_expected_spans_without_recall_loss(arm: str) -> None:
    root = os.environ.get("FETCHCORE_SOLR_ANN_BENCH_ARTIFACTS")
    if not root:
        pytest.skip("set FETCHCORE_SOLR_ANN_BENCH_ARTIFACTS to the paired run root")
    base = Path(root)
    control = json.loads((base / "control" / "V0.1" / f"{arm}.json").read_text())
    candidate = json.loads((base / "candidate" / "V0.1" / f"{arm}.json").read_text())
    assert control["ground_truth_fingerprint"] == candidate["ground_truth_fingerprint"]
    assert control["metrics_fingerprint"] == candidate["metrics_fingerprint"]
    assert control["corpus"] == candidate["corpus"]
    assert control["config"]["identity"]["backend_settings"].get("ann_search_breadth") is None
    assert candidate["config"]["identity"]["backend_settings"]["ann_search_breadth"] == 160

    def by_key(artifact: dict[str, object]) -> dict[tuple[str, int], dict[str, object]]:
        return {
            (row["query_id"], row["iteration"]): row
            for row in artifact["observations"]
        }

    before = by_key(control)
    after = by_key(candidate)
    assert before.keys() == after.keys()
    assert len(before) == 400
    assert len({query_id for query_id, _ in before}) == 20
    targeted = {"health-controller", "health-controller-tests"}
    for (query_id, iteration), row in after.items():
        previous = before[(query_id, iteration)]
        assert previous["telemetry"]["metadata"]["ann_top_k_requested"] == 80
        assert row["telemetry"]["metadata"]["ann_top_k_requested"] == 160
        assert row["telemetry"]["metadata"]["query_vector_sha256"] == previous[
            "telemetry"
        ]["metadata"]["query_vector_sha256"]
        assert row["telemetry"]["metadata"]["dense_candidates_acquired"] <= 80
        assert row["candidates_returned"] <= 20
        if query_id in targeted:
            assert row["evidence_recall_at_k"] == 1.0
        else:
            assert row["evidence_recall_at_k"] >= previous["evidence_recall_at_k"]
    assert all(
        row["evidence_recall_at_k"] == 1.0
        for (query_id, _), row in after.items()
        if query_id == "health-resource-collection"
    )
