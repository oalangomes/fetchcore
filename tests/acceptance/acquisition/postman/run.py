"""Frozen S3 request acquisition benchmark; safe, no network or external backends."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer
from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer


def evaluate(*, repeats: int = 5) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    root = Path(__file__).resolve().parents[1]
    fixture = root / "api-safety" / "fixtures"
    baseline_ground_truth = json.loads(
        (root / "api-safety" / "ground-truth.json").read_text(encoding="utf-8")
    )
    truth = json.loads(
        (root / "postman" / "ground-truth.json").read_text(encoding="utf-8")
    )
    config = RepositoryIndexConfig(
        repository="fetchcore/s3-fixture", revision="synthetic-fixture-revision",
    )
    prior = OpenApiRepositoryIndexer(config)
    combined = ApiContractRepositoryIndexer(config)
    before = prior.index(fixture)
    milliseconds: list[float] = []
    after = None
    for attempt in range(repeats + 1):
        started = time.perf_counter()
        after = combined.index(fixture)
        if attempt > 0:
            milliseconds.append((time.perf_counter() - started) * 1000.0)
    assert after is not None
    actual: set[tuple[str, str, str]] = set()
    operations: set[tuple[str, str]] = set()
    for doc in after.documents:
        for envelope in doc.evidence.semantic:
            if envelope.namespace != "api_contract":
                continue
            fact = ApiEvidenceV1.from_envelope(envelope)
            if fact.kind == "api_request" and fact.method and fact.route:
                actual.add((fact.locator.pointer, fact.method, fact.route))
            if fact.kind == "api_operation" and fact.method and fact.route:
                operations.add((fact.method, fact.route))
    expected = {
        (case["pointer"], case["method"], case["route"])
        for case in truth["requests"]
    }
    sentinel = baseline_ground_truth["security_sentinel"]
    serialized = json.dumps(after.to_mapping(), default=str)
    return {
        "protocol": "fetchcore.api-extraction-s3",
        "mode": "explicit-opt-in",
        "baseline_s2_canonical_documents": len(before.documents),
        "combined_canonical_documents": len(after.documents),
        "postman_requests_expected": len(expected),
        "postman_requests_recovered": len(expected & actual),
        "postman_requests_extra": len(actual - expected),
        "postman_request_ground_truth_recovery": round(
            len(expected & actual) / len(expected), 5
        ),
        "openapi_operations_preserved": len(operations),
        "environment_quarantined": not any(
            doc.path.endswith(".postman_environment.json")
            for doc in after.documents
        ),
        "secret_sentinel_present": sentinel in serialized,
        "changed_indexing_fingerprint": (
            indexing_contract_fingerprint(prior) != indexing_contract_fingerprint(combined)
        ),
        "indexing_duration_ms_p50": round(statistics.median(milliseconds), 3),
        "indexing_duration_ms_max": round(max(milliseconds), 3),
        "context_characters": sum(len(doc.content) for doc in after.documents),
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2, sort_keys=True))
