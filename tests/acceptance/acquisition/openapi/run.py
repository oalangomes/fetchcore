"""Deterministic S2 OpenAPI operation recovery on frozen synthetic S0 fixtures."""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer


def evaluate(*, repeats: int = 5) -> dict[str, object]:
    if repeats < 1:
        raise ValueError("benchmark repeats must be positive")
    fixture = Path(__file__).resolve().parents[1] / "api-safety" / "fixtures"
    truth = json.loads(
        (fixture.parent / "ground-truth.json").read_text(encoding="utf-8")
    )
    config = RepositoryIndexConfig(repository="fetchcore/s2-fixture", revision="s2-fixture")
    baseline = RepositoryIndexer(config)
    active = OpenApiRepositoryIndexer(config)
    base = baseline.index(fixture)
    measurements: list[float] = []
    result = None
    for run in range(repeats + 1):
        began = time.perf_counter()
        result = active.index(fixture)
        if run > 0:
            measurements.append(round((time.perf_counter() - began) * 1000, 3))
    assert result is not None
    records: set[tuple[str, str, str]] = set()
    observed: list[ApiEvidenceV1] = []
    for document in result.documents:
        for envelope in document.evidence.semantic:
            if envelope.namespace != "api_contract":
                continue
            fact = ApiEvidenceV1.from_envelope(envelope)
            observed.append(fact)
            if fact.kind == "api_operation" and fact.method and fact.route:
                records.add((document.path, fact.method, fact.route))
    expected = {
        (str(item["source"]), *(str(item["operation"]).split(" ", 1)))
        for item in truth["queries"] if item.get("operation")
        and item.get("kind") in {"swagger", "openapi"}
    }
    matched = records & expected
    sentinel = str(truth["security_sentinel"])
    all_serialized = json.dumps([doc.to_mapping() for doc in result.documents])
    return {
        "protocol": "fetchcore.api-extraction-s2",
        "mode": "explicit-opt-in",
        "baseline_documents": len(base.documents),
        "sanitized_documents": len(result.documents),
        "expected_operations": len(expected),
        "recovered_operations": len(matched),
        "operation_ground_truth_recovery": round(len(matched) / len(expected), 5),
        "additional_operations": len(records - expected),
        "schemas": sum(fact.kind == "api_schema" for fact in observed),
        "references": sum(fact.kind == "api_reference" for fact in observed),
        "postman_quarantined": not any(
            doc.path.endswith(".postman_collection.json")
            or doc.path.endswith(".postman_environment.json")
            for doc in result.documents
        ),
        "secret_sentinel_present": sentinel in all_serialized,
        "distinct_indexing_fingerprint": (
            indexing_contract_fingerprint(baseline)
            != indexing_contract_fingerprint(active)
        ),
        "indexing_duration_ms_p50": round(statistics.median(measurements), 3),
        "indexing_duration_ms_max": round(max(measurements), 3),
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), sort_keys=True, indent=2))
