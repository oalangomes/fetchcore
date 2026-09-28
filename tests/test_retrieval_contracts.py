from __future__ import annotations

from pathlib import Path

import pytest

from fetchcore.evidence import EvidenceObservation, QueryMatchEvidence
from fetchcore.retrieval import (
    RetrievalBackendCapabilities,
    RetrievalConfig,
    RetrievalConfigurationError,
    validate_backend_retrieval,
)
from fetchcore.search import SearchIntent, SearchResult, SearchScope
from fetchcore.signals import QuerySignal


def _signal() -> QuerySignal:
    return QuerySignal(
        value="MedicationHistoryService",
        type="exact",
        classification="exact_identifier",
        priority=10,
        weight=1.0,
        origin="caller",
        source="query",
        section="subject",
        protected=True,
        required=True,
    )


def test_query_signal_round_trip() -> None:
    signal = _signal()
    assert QuerySignal.from_mapping(signal.to_mapping()) == signal


def test_search_intent_preserves_typed_signals_and_stable_terms() -> None:
    signal = _signal()
    intent = SearchIntent(
        exact_terms=("MedicationHistoryService", "MedicationHistoryService"),
        semantic_terms=("medication history",),
        hints=("service",),
        signals=(signal,),
    )
    assert intent.terms == ("MedicationHistoryService", "medication history")
    assert intent.to_mapping()["signals"] == [signal.to_mapping()]


def test_evidence_observation_is_bounded_source_fact() -> None:
    observed = EvidenceObservation(
        category="identifier",
        subtype="symbol",
        value="MedicationHistoryService",
        source_ref="src/service.py",
        line_start=10,
        line_end=20,
    )
    assert EvidenceObservation.from_mapping(observed.to_mapping()) == observed

    with pytest.raises(ValueError, match="incompatible"):
        EvidenceObservation(
            category="identifier",
            subtype="method",
            value="GET",
            source_ref="src/service.py",
        )


def test_query_match_evidence_round_trip() -> None:
    match = QueryMatchEvidence(
        signal=_signal(),
        match_type="structural",
        channels=("symbol",),
        matched_values=("MedicationHistoryService",),
        source_ref="src/service.py",
        strength=1.0,
        normalized_contribution=0.5,
    )
    assert QueryMatchEvidence.from_mapping(match.to_mapping()) == match


def test_search_result_is_pre_consumer_boundary() -> None:
    result = SearchResult(
        id="hit-1",
        source="repository",
        source_ref="src/service.py",
        knowledge_type="code",
        content="class MedicationHistoryService: ...",
        score=0.9,
        capabilities=("implementation",),
        capability_sources={"implementation": "provider_classification"},
        matched_signals=(_signal(),),
    )
    assert result.source_identity == "src/service.py"
    assert result.to_mapping()["source_ref"] == "src/service.py"

    with pytest.raises(ValueError, match="capability_sources"):
        SearchResult(
            id="bad",
            source="repository",
            source_ref="src/bad.py",
            knowledge_type="code",
            content="bad",
            score=0.1,
            capabilities=("implementation",),
        )


def test_search_scope_serializes_backend_neutral_retrieval_config() -> None:
    config = RetrievalConfig.from_mapping(
        {
            "dense": {"enabled": True},
            "hybrid": {"enabled": True, "strategy": "rrf"},
        }
    )
    scope = SearchScope(
        workspace=Path("."),
        revision="abc123",
        repository="repo",
        knowledge_types=("code",),
        max_results=80,
        retrieval=config,
    )
    mapping = scope.to_mapping()
    assert mapping["repository"] == "repo"
    assert mapping["retrieval"]["mode"] == "hybrid"  # type: ignore[index]


def test_hybrid_without_dense_fails_closed() -> None:
    with pytest.raises(RetrievalConfigurationError):
        RetrievalConfig.from_mapping(
            {
                "dense": {"enabled": False},
                "hybrid": {"enabled": True},
            }
        )


def test_backend_capability_validation_fails_closed() -> None:
    config = RetrievalConfig.from_mapping({"dense": {"enabled": True}})
    capabilities = RetrievalBackendCapabilities(lexical=True, dense=False)

    with pytest.raises(RetrievalConfigurationError) as exc:
        validate_backend_retrieval(
            config,
            backend_id="fixture",
            capabilities=capabilities,
        )

    assert exc.value.category == "unsupported_capability"


def test_contract_modules_do_not_import_agentsorch() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "fetchcore"
    for relative in ("signals.py", "evidence.py", "search.py", "retrieval.py"):
        content = (root / relative).read_text(encoding="utf-8")
        assert "agentsorch" not in content


def test_retrieval_config_round_trip_preserves_derived_mode() -> None:
    config = RetrievalConfig.from_mapping(
        {
            "dense": {"enabled": True},
            "hybrid": {"enabled": True, "strategy": "rrf"},
        }
    )

    assert RetrievalConfig.from_mapping(config.to_mapping()) == config


def test_retrieval_config_rejects_inconsistent_serialized_mode() -> None:
    with pytest.raises(RetrievalConfigurationError, match="retrieval.mode"):
        RetrievalConfig.from_mapping(
            {
                "dense": {"enabled": True},
                "hybrid": {"enabled": False, "strategy": "rrf"},
                "mode": "hybrid",
            }
        )
