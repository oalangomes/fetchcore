from __future__ import annotations

import pytest

from fetchcore.projection import ProjectionV3Compiler
from fetchcore.retrieval.projection_matching import (
    match_projection_record,
    query_match_evidence,
    validate_projection_v3_controls,
)
from fetchcore.repository.indexing import KnowledgeDocument, KnowledgeProvenance
from fetchcore.search import KnowledgeSearchError, SearchIntent
from fetchcore.signals import QuerySignal


def _document() -> KnowledgeDocument:
    source_ref = "repo://example/repo/src/example.py"
    chunk_ref = f"{source_ref}#symbol:example:0:segment:0"
    return KnowledgeDocument(
        id="kdoc-projection-core",
        source_type="repository",
        source_ref=source_ref,
        chunk_ref=chunk_ref,
        repository="example/repo",
        branch="main",
        revision="rev-a",
        path="src/example.py",
        filename="example.py",
        language="python",
        knowledge_type="code",
        technical_role="service",
        symbols=("example",),
        symbol_types=("function",),
        content="def example():\n    return True\n",
        line_start=1,
        line_end=2,
        checksum="chunk-checksum",
        provenance=KnowledgeProvenance(
            source_ref=source_ref,
            chunk_ref=chunk_ref,
            repository="example/repo",
            revision="rev-a",
            path="src/example.py",
            line_start=1,
            line_end=2,
            source_checksum="source-checksum",
        ),
        metadata={},
        indexed_at="2026-09-17T00:00:00+00:00",
    )


def _signal() -> QuerySignal:
    return QuerySignal(
        value="example",
        type="exact",
        classification="exact_identifier",
        priority=100,
        weight=1.0,
        origin="caller",
        source="query",
        section="subject",
        protected=True,
        required=True,
    )


@pytest.mark.parametrize("value", [3, True, []])
def test_projection_controls_reject_non_string_version(value: object) -> None:
    with pytest.raises(KnowledgeSearchError, match="projection_version must be a string") as raised:
        validate_projection_v3_controls({"projection_version": value}, backend_name="test")
    assert raised.value.category == "schema_mismatch"


@pytest.mark.parametrize("field", ["field_boosts", "signal_type_boosts"])
@pytest.mark.parametrize("value", ["1.5", True, float("nan"), float("inf"), float("-inf")])
def test_projection_controls_reject_incompatible_boosts(field: str, value: object) -> None:
    name = "content" if field == "field_boosts" else "exact"
    with pytest.raises(KnowledgeSearchError) as raised:
        validate_projection_v3_controls({field: {name: value}}, backend_name="test")
    assert raised.value.category == "schema_mismatch"


@pytest.mark.parametrize("field", ["field_boosts", "signal_type_boosts"])
def test_projection_controls_reject_non_string_mapping_keys(field: str) -> None:
    with pytest.raises(KnowledgeSearchError) as raised:
        validate_projection_v3_controls({field: {123: 2.0}}, backend_name="test")
    assert raised.value.category == "schema_mismatch"


@pytest.mark.parametrize("value", ["100", 100.0, True])
def test_projection_controls_reject_incompatible_window(value: object) -> None:
    with pytest.raises(KnowledgeSearchError, match="must be an integer") as raised:
        validate_projection_v3_controls({"diagnostic_candidate_window": value}, backend_name="test")
    assert raised.value.category == "schema_mismatch"


def test_projection_controls_accept_integer_and_float_boosts() -> None:
    controls = validate_projection_v3_controls(
        {
            "field_boosts": {"content": 1},
            "signal_type_boosts": {"exact": 1.5},
            "diagnostic_candidate_window": 100,
        },
        backend_name="test",
    )
    assert controls.field_boosts["content"] == 1.0
    assert controls.signal_type_boosts["exact"] == 1.5
    assert controls.diagnostic_candidate_window == 100


def test_retrieval_projection_matching_uses_query_and_evidence_contracts() -> None:
    mapping = ProjectionV3Compiler().compile(_document()).to_mapping()
    controls = validate_projection_v3_controls({}, backend_name="fetchcore-test")
    match = match_projection_record(
        SearchIntent(signals=(_signal(),)),
        mapping,
        controls,
    )

    assert "example" in match.matched_terms
    assert match.matched_signals == (_signal(),)
    assert match.channels
    evidence = query_match_evidence(
        match,
        source_ref=_document().source_ref,
        provenance="fetchcore_projection_v3_match",
    )
    assert evidence
    assert {item.provenance for item in evidence} == {
        "fetchcore_projection_v3_match"
    }
