from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("type", "unknown", "type is invalid"),
        ("classification", "unknown", "classification is invalid"),
        ("type", 1, "type must be a string"),
        ("classification", None, "classification must be a string"),
    ],
)
def test_query_signal_rejects_invalid_closed_vocabulary_values(
    field: str,
    value: object,
    message: str,
) -> None:
    mapping = _signal().to_mapping()
    mapping[field] = value

    with pytest.raises(ValueError, match=message):
        QuerySignal.from_mapping(mapping)


def test_query_signal_preserves_defaults_for_absent_optional_representation_fields() -> None:
    mapping = _signal().to_mapping()
    for field in ("priority", "weight", "signals", "derived_from", "protected", "required"):
        mapping.pop(field)

    restored = QuerySignal.from_mapping(mapping)

    assert restored.priority == 0
    assert restored.weight == 0.0
    assert restored.signals == ()
    assert restored.derived_from is None
    assert restored.protected is False
    assert restored.required is False


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("priority", "10", "priority must be an integer"),
        ("priority", 10.0, "priority must be an integer"),
        ("priority", True, "priority must be an integer"),
        ("weight", "1.0", "weight must be a finite number"),
        ("weight", True, "weight must be a finite number"),
        ("weight", float("nan"), "weight must be a finite number"),
        ("weight", float("inf"), "weight must be a finite number"),
        ("weight", 10**1000, "weight must be a finite number"),
        ("protected", "false", "protected must be boolean"),
        ("protected", 0, "protected must be boolean"),
        ("required", "true", "required must be boolean"),
        ("value", 123, "value must be a string"),
        ("derived_from", 123, "derived_from must be a string or null"),
        ("signals", ["symbol", 123], "signals must contain only strings"),
    ],
)
def test_query_signal_rejects_silent_primitive_coercions(
    field: str,
    value: object,
    message: str,
) -> None:
    mapping = _signal().to_mapping()
    mapping[field] = value

    with pytest.raises(ValueError, match=message):
        QuerySignal.from_mapping(mapping)


def test_query_signal_normalizes_integer_weight_to_float() -> None:
    mapping = _signal().to_mapping()
    mapping["weight"] = 1

    restored = QuerySignal.from_mapping(mapping)

    assert restored.weight == 1.0
    assert isinstance(restored.weight, float)


@pytest.mark.parametrize("field", ["type", "classification"])
def test_query_signal_rejects_missing_closed_vocabulary_fields(field: str) -> None:
    mapping = _signal().to_mapping()
    mapping.pop(field)

    with pytest.raises(ValueError, match=f"{field} is invalid"):
        QuerySignal.from_mapping(mapping)


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


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("category", 1, "category must be a string"),
        ("subtype", None, "subtype must be a string"),
        ("value", 1, "value must be a string"),
        ("source_ref", 1, "source_ref must be a string"),
        ("slice_ref", 1, "slice_ref must be a string"),
        ("line_start", "10", "line_start must be an integer"),
        ("line_start", 10.0, "line_start must be an integer"),
        ("line_start", True, "line_start must be an integer"),
    ],
)
def test_evidence_observation_rejects_invalid_primitive_types(
    field: str,
    value: object,
    message: str,
) -> None:
    mapping = EvidenceObservation(
        category="identifier",
        subtype="symbol",
        value="MedicationHistoryService",
        source_ref="src/service.py",
    ).to_mapping()
    mapping[field] = value

    with pytest.raises(ValueError, match=message):
        EvidenceObservation.from_mapping(mapping)


def test_evidence_observation_rejects_non_string_attribute_keys() -> None:
    mapping = EvidenceObservation(
        category="identifier",
        subtype="symbol",
        value="MedicationHistoryService",
        source_ref="src/service.py",
    ).to_mapping()
    mapping["attributes"] = {123: "value"}

    with pytest.raises(ValueError, match="attribute names must be strings"):
        EvidenceObservation.from_mapping(mapping)


def test_evidence_observation_keeps_semantic_validation_in_dataclass() -> None:
    with pytest.raises(ValueError, match="category is invalid"):
        EvidenceObservation.from_mapping(
            {
                "category": "unknown",
                "subtype": "symbol",
                "value": "MedicationHistoryService",
                "source_ref": "src/service.py",
            }
        )

    with pytest.raises(ValueError, match="incompatible"):
        EvidenceObservation.from_mapping(
            {
                "category": "identifier",
                "subtype": "method",
                "value": "GET",
                "source_ref": "src/service.py",
            }
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


@pytest.mark.parametrize("field", ["channels", "matched_values", "derivation"])
def test_query_match_evidence_rejects_non_string_sequence_members(field: str) -> None:
    mapping = QueryMatchEvidence(
        signal=_signal(),
        match_type="structural",
        channels=("symbol",),
        matched_values=("MedicationHistoryService",),
        source_ref="src/service.py",
    ).to_mapping()
    mapping[field] = ["foo", 123]

    with pytest.raises(ValueError, match="must contain only strings"):
        QueryMatchEvidence.from_mapping(mapping)


@pytest.mark.parametrize("value", ["0.5", True, 10**1000])
def test_query_match_evidence_rejects_invalid_strength_representation(value: object) -> None:
    mapping = QueryMatchEvidence(
        signal=_signal(),
        match_type="structural",
        channels=("symbol",),
        matched_values=("MedicationHistoryService",),
        source_ref="src/service.py",
    ).to_mapping()
    mapping["strength"] = value

    with pytest.raises(ValueError, match="strength must be a number"):
        QueryMatchEvidence.from_mapping(mapping)


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_query_match_evidence_preserves_score_domain_validation(value: float) -> None:
    mapping = QueryMatchEvidence(
        signal=_signal(),
        match_type="structural",
        channels=("symbol",),
        matched_values=("MedicationHistoryService",),
        source_ref="src/service.py",
    ).to_mapping()
    mapping["strength"] = value

    with pytest.raises(ValueError, match="between 0 and 1"):
        QueryMatchEvidence.from_mapping(mapping)


def test_query_match_evidence_normalizes_integer_strength_to_float() -> None:
    mapping = QueryMatchEvidence(
        signal=_signal(),
        match_type="structural",
        channels=("symbol",),
        matched_values=("MedicationHistoryService",),
        source_ref="src/service.py",
    ).to_mapping()
    mapping["strength"] = 1

    restored = QueryMatchEvidence.from_mapping(mapping)

    assert restored.strength == 1.0
    assert isinstance(restored.strength, float)


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
    for relative in (
        "retrieval/signals.py",
        "retrieval/evidence.py",
        "retrieval/contracts.py",
        "retrieval/config.py",
    ):
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


@pytest.mark.parametrize(
    ("mapping", "message"),
    [
        ({"dense": {"embedding": {"provider": 123}}}, "provider must be a string"),
        ({"dense": {"embedding": {"model": False}}}, "model must be a string"),
        ({"hybrid": {"strategy": 123}}, "strategy must be a string"),
        ({"mode": 123}, "retrieval.mode must be a string"),
        ({"mode": None}, "retrieval.mode must be a string"),
    ],
)
def test_retrieval_config_rejects_silent_string_coercions(
    mapping: dict[object, object],
    message: str,
) -> None:
    with pytest.raises(RetrievalConfigurationError, match=message):
        RetrievalConfig.from_mapping(cast(Mapping[str, object], mapping))


@pytest.mark.parametrize(
    ("mapping", "path"),
    [
        ({123: "value"}, "retrieval"),
        ({"dense": {123: "value"}}, "retrieval.dense"),
        ({"reranking": {"scorer": {123: "value"}}}, "retrieval.reranking.scorer"),
        (
            {"reranking": {"parameters": {123: "value"}}},
            "retrieval.reranking.parameters",
        ),
    ],
)
def test_retrieval_config_rejects_non_string_mapping_keys(
    mapping: dict[object, object],
    path: str,
) -> None:
    with pytest.raises(
        RetrievalConfigurationError,
        match=f"{path} field names must be strings",
    ):
        RetrievalConfig.from_mapping(cast(Mapping[str, object], mapping))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_batch_size", "7"),
        ("request_batch_size", 7.0),
        ("request_batch_size", True),
        ("timeout_seconds", "45"),
        ("timeout_seconds", True),
        ("timeout_seconds", 10**1000),
    ],
)
def test_retrieval_config_rejects_incompatible_numeric_types(
    field: str,
    value: object,
) -> None:
    with pytest.raises(RetrievalConfigurationError):
        RetrievalConfig.from_mapping(
            {"dense": {"embedding": {field: value}}}
        )
