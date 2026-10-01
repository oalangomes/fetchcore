"""Typed backend-neutral query signals used by FetchCore retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Literal, cast


QueryClassification = Literal[
    "direct_reference",
    "exact_identifier",
    "path",
    "architecture_concept",
    "functional_objective",
    "behavioral_constraint",
    "completion_instruction",
    "domain_concept",
    "semantic_phrase",
    "semantic_term",
    "repository_identity",
    "scope_hint",
    "product_identity",
    "generic_instruction",
]
QuerySignalType = Literal["exact", "semantic", "reference", "hint"]

_QUERY_CLASSIFICATIONS = frozenset(
    {
        "direct_reference",
        "exact_identifier",
        "path",
        "architecture_concept",
        "functional_objective",
        "behavioral_constraint",
        "completion_instruction",
        "domain_concept",
        "semantic_phrase",
        "semantic_term",
        "repository_identity",
        "scope_hint",
        "product_identity",
        "generic_instruction",
    }
)
_QUERY_SIGNAL_TYPES = frozenset({"exact", "semantic", "reference", "hint"})


@dataclass(frozen=True)
class QuerySignal:
    """Typed acquisition signal without authority or satisfaction semantics."""

    value: str
    type: QuerySignalType
    classification: QueryClassification
    priority: int
    weight: float
    origin: str
    source: str
    section: str
    signals: tuple[str, ...] = ()
    derived_from: str | None = None
    protected: bool = False
    required: bool = False

    @classmethod
    def from_mapping(cls, data: object) -> QuerySignal:
        if not isinstance(data, dict):
            raise ValueError("QuerySignal must be an object")

        signal_type = _string_value(data, "type", "")
        if signal_type not in _QUERY_SIGNAL_TYPES:
            raise ValueError("QuerySignal type is invalid")
        classification = _string_value(data, "classification", "")
        if classification not in _QUERY_CLASSIFICATIONS:
            raise ValueError("QuerySignal classification is invalid")

        return cls(
            value=_string_value(data, "value", ""),
            type=cast(QuerySignalType, signal_type),
            classification=cast(QueryClassification, classification),
            priority=_int_value(data, "priority", 0),
            weight=_float_value(data, "weight", 0.0),
            origin=_string_value(data, "origin", ""),
            source=_string_value(data, "source", ""),
            section=_string_value(data, "section", ""),
            signals=_string_list_value(data, "signals"),
            derived_from=_optional_string_value(data, "derived_from"),
            protected=_bool_value(data, "protected", False),
            required=_bool_value(data, "required", False),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "value": self.value,
            "type": self.type,
            "classification": self.classification,
            "priority": self.priority,
            "weight": self.weight,
            "origin": self.origin,
            "source": self.source,
            "section": self.section,
            "signals": list(self.signals),
            "derived_from": self.derived_from,
            "protected": self.protected,
            "required": self.required,
        }


def _string_value(data: dict[object, object], field: str, default: str) -> str:
    value = data.get(field, default)
    if not isinstance(value, str):
        raise ValueError(f"QuerySignal {field} must be a string")
    return value


def _optional_string_value(data: dict[object, object], field: str) -> str | None:
    value = data.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"QuerySignal {field} must be a string or null")
    return value


def _int_value(data: dict[object, object], field: str, default: int) -> int:
    value = data.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"QuerySignal {field} must be an integer")
    return value


def _float_value(data: dict[object, object], field: str, default: float) -> float:
    value = data.get(field, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"QuerySignal {field} must be a finite number")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"QuerySignal {field} must be a finite number") from exc
    if not isfinite(result):
        raise ValueError(f"QuerySignal {field} must be a finite number")
    return result


def _bool_value(data: dict[object, object], field: str, default: bool) -> bool:
    value = data.get(field, default)
    if not isinstance(value, bool):
        raise ValueError(f"QuerySignal {field} must be boolean")
    return value


def _string_list_value(data: dict[object, object], field: str) -> tuple[str, ...]:
    value = data.get(field, [])
    if not isinstance(value, list):
        raise ValueError(f"QuerySignal {field} must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"QuerySignal {field} must contain only strings")
    return tuple(value)
