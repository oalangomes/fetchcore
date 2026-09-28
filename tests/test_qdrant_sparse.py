from __future__ import annotations

from fetchcore.backends.qdrant_sparse import (
    SPARSE_ANALYZER_FINGERPRINT,
    intent_sparse_terms,
    matched_sparse_terms,
    projection_sparse_terms,
    projection_sparse_vector,
    sparse_vector_from_terms,
)
from fetchcore.search import SearchIntent


def _document() -> dict[str, object]:
    return {
        "identifiers_exact": ["medicationusagehistoryservice", "getusagehistory"],
        "content_identifiers_exact": ["getusagehistory"],
        "symbols": ["MedicationUsageHistoryService", "getUsageHistory"],
        "filename": "MedicationUsageHistoryService.ts",
        "path": "src/services/MedicationUsageHistoryService.ts",
        "path_segments": ["src", "services", "MedicationUsageHistoryService.ts"],
        "technical_role": "service",
        "relation_targets": ["medicationUsageController"],
        "content": "class MedicationUsageHistoryService { getUsageHistory() {} }",
    }


def test_sparse_projection_is_deterministic_and_camel_case_aware() -> None:
    first = projection_sparse_vector(_document())
    second = projection_sparse_vector(_document())

    assert first == second
    assert "getusagehistory" in first.terms
    assert "usage" in first.terms
    assert "history" in first.terms
    assert tuple(sorted(first.indices)) == first.indices
    assert len(first.indices) == len(set(first.indices))
    assert set(first.values) == {1.0}
    assert len(SPARSE_ANALYZER_FINGERPRINT) == 64


def test_sparse_query_uses_neutral_intent_without_backend_flags() -> None:
    terms = intent_sparse_terms(
        SearchIntent(
            exact_terms=("getUsageHistory",),
            semantic_terms=("medication usage history",),
            hints=("service",),
        )
    )

    assert {"getusagehistory", "medication", "usage", "history", "service"} <= set(
        terms
    )


def test_sparse_matched_terms_are_real_query_document_intersection() -> None:
    intent = SearchIntent(
        exact_terms=("getUsageHistory",),
        semantic_terms=("medication history",),
    )

    matched = matched_sparse_terms(intent, _document())

    assert "getusagehistory" in matched
    assert "medication" in matched
    assert "history" in matched
    assert "unrelated" not in matched


def test_sparse_term_hashing_fails_closed_on_local_collision(monkeypatch) -> None:
    import fetchcore.backends.qdrant_sparse as sparse

    monkeypatch.setattr(sparse, "_term_index", lambda _term: 7)

    try:
        sparse_vector_from_terms(("alpha", "beta"))
    except ValueError as exc:
        assert "hash collision" in str(exc)
    else:  # pragma: no cover - explicit fail-closed assertion
        raise AssertionError("expected deterministic sparse collision failure")


def test_projection_sparse_terms_ignore_missing_fields() -> None:
    assert projection_sparse_terms({"content": "SimpleThing"}) == (
        "simple",
        "simplething",
        "thing",
    )
