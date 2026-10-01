"""Source observations and query-match evidence owned by FetchCore."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal, cast

from fetchcore.retrieval.signals import QuerySignal


EvidenceObservationCategory = Literal[
    "identifier",
    "path",
    "technical_role",
    "route",
    "reference",
    "dependency",
]
EvidenceObservationSubtype = Literal[
    "symbol",
    "lexical_identifier",
    "content_identifier",
    "filename_identifier",
    "file",
    "filename",
    "segment",
    "technical_role",
    "method",
    "path",
    "mechanism",
    "import",
    "direct_reference",
    "relation_target",
    "dependency_target",
]

_OBSERVATION_SUBTYPES: dict[str, frozenset[str]] = {
    "identifier": frozenset(
        {"symbol", "lexical_identifier", "content_identifier", "filename_identifier"}
    ),
    "path": frozenset({"file", "filename", "segment"}),
    "technical_role": frozenset({"technical_role"}),
    "route": frozenset({"method", "path", "mechanism"}),
    "reference": frozenset({"import", "direct_reference", "relation_target"}),
    "dependency": frozenset({"dependency_target"}),
}


@dataclass(frozen=True)
class EvidenceObservation:
    """Source-derived fact that grants neither authority nor satisfaction."""

    category: EvidenceObservationCategory
    subtype: EvidenceObservationSubtype
    value: str
    source_ref: str
    slice_ref: str | None = None
    revision: str | None = None
    checksum: str | None = None
    line_start: int | None = None
    line_end: int | None = None
    provenance: str = "provider_normalized"
    attributes: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.category not in _OBSERVATION_SUBTYPES:
            raise ValueError("EvidenceObservation category is invalid")
        if self.subtype not in _OBSERVATION_SUBTYPES[self.category]:
            raise ValueError("EvidenceObservation subtype is incompatible with category")
        if not self.value.strip() or not self.source_ref.strip():
            raise ValueError("EvidenceObservation value/source_ref must not be empty")
        if not self.provenance.strip():
            raise ValueError("EvidenceObservation provenance must not be empty")
        if self.line_start is not None and self.line_start < 1:
            raise ValueError("EvidenceObservation line_start must be positive")
        if self.line_end is not None and self.line_end < 1:
            raise ValueError("EvidenceObservation line_end must be positive")
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            raise ValueError("EvidenceObservation line range is invalid")
        if len(self.attributes) > 16 or any(
            not str(key).strip() or len(str(key)) > 64 for key in self.attributes
        ):
            raise ValueError("EvidenceObservation attributes must be bounded and named")
        if any(
            not isinstance(value, (str, int, float, bool, type(None)))
            or isinstance(value, str)
            and len(value) > 256
            for value in self.attributes.values()
        ):
            raise ValueError("EvidenceObservation attributes must contain bounded scalars")

    @classmethod
    def from_mapping(cls, data: object) -> EvidenceObservation:
        if not isinstance(data, Mapping):
            raise ValueError("EvidenceObservation must be an object")
        raw_attributes = data.get("attributes", {})
        if not isinstance(raw_attributes, Mapping):
            raise ValueError("EvidenceObservation attributes must be an object")
        if any(not isinstance(key, str) for key in raw_attributes):
            raise ValueError("EvidenceObservation attribute names must be strings")
        return cls(
            category=cast(
                EvidenceObservationCategory,
                _string_value(data.get("category", ""), "category"),
            ),
            subtype=cast(
                EvidenceObservationSubtype,
                _string_value(data.get("subtype", ""), "subtype"),
            ),
            value=_string_value(data.get("value", ""), "value"),
            source_ref=_string_value(data.get("source_ref", ""), "source_ref"),
            slice_ref=_optional_string(data.get("slice_ref"), "slice_ref"),
            revision=_optional_string(data.get("revision"), "revision"),
            checksum=_optional_string(data.get("checksum"), "checksum"),
            line_start=_optional_int(data.get("line_start"), "line_start"),
            line_end=_optional_int(data.get("line_end"), "line_end"),
            provenance=_string_value(
                data.get("provenance", "provider_normalized"), "provenance"
            ),
            attributes=dict(raw_attributes),
        )

    def to_mapping(self) -> dict[str, object]:
        data = asdict(self)
        return {
            key: value
            for key, value in data.items()
            if value not in (None, "", (), {}, [])
        }


@dataclass(frozen=True)
class QueryMatchEvidence:
    """Explain how one query signal matched without granting evidence authority."""

    signal: QuerySignal
    match_type: str
    channels: tuple[str, ...]
    matched_values: tuple[str, ...]
    source_ref: str
    strength: float | None = None
    normalized_contribution: float | None = None
    provenance: str = "provider_normalized"
    derivation: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.match_type.strip() or not self.source_ref.strip():
            raise ValueError("QueryMatchEvidence match_type/source_ref must not be empty")
        if not self.channels or any(not item.strip() for item in self.channels):
            raise ValueError("QueryMatchEvidence requires named neutral channels")
        if not self.matched_values or any(
            not item.strip() for item in self.matched_values
        ):
            raise ValueError("QueryMatchEvidence requires matched structural values")
        for field_name, value in (
            ("strength", self.strength),
            ("normalized_contribution", self.normalized_contribution),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(
                    f"QueryMatchEvidence {field_name} must be between 0 and 1"
                )
        if not self.provenance.strip():
            raise ValueError("QueryMatchEvidence provenance must not be empty")
        if (
            len(self.channels) > 32
            or len(self.matched_values) > 64
            or len(self.derivation) > 32
            or any(
                len(item) > 256
                for item in (*self.channels, *self.matched_values, *self.derivation)
            )
        ):
            raise ValueError("QueryMatchEvidence audit details must be bounded")

    @classmethod
    def from_mapping(cls, data: object) -> QueryMatchEvidence:
        if not isinstance(data, Mapping):
            raise ValueError("QueryMatchEvidence must be an object")
        return cls(
            signal=QuerySignal.from_mapping(data.get("signal")),
            match_type=_string_value(data.get("match_type", ""), "match_type"),
            channels=_string_tuple(data.get("channels", []), "channels"),
            matched_values=_string_tuple(
                data.get("matched_values", []), "matched_values"
            ),
            source_ref=_string_value(data.get("source_ref", ""), "source_ref"),
            strength=_optional_float(data.get("strength"), "strength"),
            normalized_contribution=_optional_float(
                data.get("normalized_contribution"), "normalized_contribution"
            ),
            provenance=_string_value(
                data.get("provenance", "provider_normalized"), "provenance"
            ),
            derivation=_string_tuple(data.get("derivation", []), "derivation"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "signal": self.signal.to_mapping(),
            "match_type": self.match_type,
            "channels": list(self.channels),
            "matched_values": list(self.matched_values),
            "source_ref": self.source_ref,
            "strength": self.strength,
            "normalized_contribution": self.normalized_contribution,
            "provenance": self.provenance,
            "derivation": list(self.derivation),
        }


def _string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list")
    if any(not isinstance(item, str) for item in value):
        raise ValueError(f"{field_name} must contain only strings")
    return tuple(value)


def _string_value(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _string_value(value, field_name)


def _optional_int(value: object, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field_name} must be an integer")
    return value


def _optional_float(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a number")
    try:
        return float(value)
    except OverflowError as exc:
        raise ValueError(f"{field_name} must be a number") from exc
