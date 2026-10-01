"""Source observations and query-match evidence owned by FetchCore."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Literal

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
        return cls(
            category=str(data.get("category", "")),  # type: ignore[arg-type]
            subtype=str(data.get("subtype", "")),  # type: ignore[arg-type]
            value=str(data.get("value", "")),
            source_ref=str(data.get("source_ref", "")),
            slice_ref=(
                str(data["slice_ref"]) if data.get("slice_ref") is not None else None
            ),
            revision=(
                str(data["revision"]) if data.get("revision") is not None else None
            ),
            checksum=(
                str(data["checksum"]) if data.get("checksum") is not None else None
            ),
            line_start=(
                int(str(data["line_start"]))
                if data.get("line_start") is not None
                else None
            ),
            line_end=(
                int(str(data["line_end"]))
                if data.get("line_end") is not None
                else None
            ),
            provenance=str(data.get("provenance", "provider_normalized")),
            attributes={str(key): value for key, value in raw_attributes.items()},
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
            match_type=str(data.get("match_type", "")),
            channels=_string_tuple(data.get("channels", []), "channels"),
            matched_values=_string_tuple(
                data.get("matched_values", []), "matched_values"
            ),
            source_ref=str(data.get("source_ref", "")),
            strength=(
                float(str(data["strength"]))
                if data.get("strength") is not None
                else None
            ),
            normalized_contribution=(
                float(str(data["normalized_contribution"]))
                if data.get("normalized_contribution") is not None
                else None
            ),
            provenance=str(data.get("provenance", "provider_normalized")),
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
    return tuple(str(item) for item in value)
