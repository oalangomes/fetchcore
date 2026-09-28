from __future__ import annotations

import pytest

from fetchcore.signals import QuerySignal
from fetchcore.reranking import RerankingConfigurationError
from fetchcore.reranking_query import (
    RERANK_QUERY_SERIALIZER_ID,
    RERANK_QUERY_SERIALIZER_VERSION,
    RerankQuerySerializer,
)


def _signal(value: str, signal_type: str) -> QuerySignal:
    return QuerySignal(
        value=value,
        type=signal_type,  # type: ignore[arg-type]
        classification="semantic_term",
        priority=1,
        weight=1.0,
        origin="planner",
        source="fixture",
        section="query",
    )


def test_serializer_preserves_typed_groups_and_reference_signals() -> None:
    serialization = RerankQuerySerializer().serialize(
        exact_terms=("LegacyExact",),
        semantic_terms=("legacy semantic",),
        hints=("controller",),
        signals=(
            _signal("NeedleIdentifier", "exact"),
            _signal("src/orders.py", "reference"),
            _signal("order lookup", "semantic"),
            _signal("service layer", "hint"),
        ),
    )

    assert serialization.serializer_id == RERANK_QUERY_SERIALIZER_ID
    assert serialization.serializer_version == RERANK_QUERY_SERIALIZER_VERSION
    assert serialization.groups == (
        ("exact", ("NeedleIdentifier", "LegacyExact")),
        ("reference", ("src/orders.py",)),
        ("semantic", ("order lookup", "legacy semantic")),
        ("hint", ("service layer", "controller")),
    )
    assert serialization.text == (
        '{"contract":"agentsorch.rerank-query","version":"1","groups":{'
        '"exact":["NeedleIdentifier","LegacyExact"],'
        '"reference":["src/orders.py"],'
        '"semantic":["order lookup","legacy semantic"],'
        '"hint":["service layer","controller"]}}'
    )
    assert serialization.fingerprint.startswith("sha256:")


def test_serializer_deduplicates_exact_values_without_collapsing_case() -> None:
    serialization = RerankQuerySerializer().serialize(
        exact_terms=("Needle", " Needle ", "needle"),
        signals=(_signal("Needle", "exact"),),
    )

    assert serialization.groups[0] == ("exact", ("Needle", "needle"))


def test_serializer_is_deterministic_and_semantic_changes_change_fingerprint() -> None:
    serializer = RerankQuerySerializer()
    baseline = serializer.serialize(exact_terms=("NeedleIdentifier",))
    replay = serializer.serialize(exact_terms=("NeedleIdentifier",))
    changed = serializer.serialize(exact_terms=("OtherIdentifier",))

    assert baseline == replay
    assert baseline.fingerprint != changed.fingerprint


def test_serializer_metadata_round_trip_shape_is_auditable() -> None:
    serialization = RerankQuerySerializer().serialize(
        exact_terms=("NeedleIdentifier",),
        hints=("controller",),
    )

    assert serialization.to_mapping() == {
        "serializer_id": "agentsorch.rerank-query",
        "serializer_version": "1",
        "fingerprint": serialization.fingerprint,
        "groups": {
            "exact": ["NeedleIdentifier"],
            "reference": [],
            "semantic": [],
            "hint": ["controller"],
        },
        "text": serialization.text,
    }


def test_serializer_rejects_empty_scoring_query() -> None:
    with pytest.raises(RerankingConfigurationError) as exc_info:
        RerankQuerySerializer().serialize()

    assert exc_info.value.category == "invalid_input"


def test_serializer_rejects_unknown_runtime_signal_type() -> None:
    malformed = _signal("NeedleIdentifier", "unexpected")

    with pytest.raises(RerankingConfigurationError) as exc_info:
        RerankQuerySerializer().serialize(signals=(malformed,))

    assert exc_info.value.metadata == {"signal_type": "unexpected"}
