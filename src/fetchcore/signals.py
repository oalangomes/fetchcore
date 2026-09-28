"""Typed backend-neutral query signals used by FetchCore retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


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
    def from_mapping(cls, data: object) -> "QuerySignal":
        if not isinstance(data, dict):
            raise ValueError("QuerySignal must be an object")
        raw_signals = data.get("signals", [])
        if not isinstance(raw_signals, list):
            raise ValueError("QuerySignal signals must be a list")
        return cls(
            value=str(data.get("value", "")),
            type=str(data.get("type", "")),  # type: ignore[arg-type]
            classification=str(data.get("classification", "")),  # type: ignore[arg-type]
            priority=int(str(data.get("priority", 0))),
            weight=float(str(data.get("weight", 0.0))),
            origin=str(data.get("origin", "")),
            source=str(data.get("source", "")),
            section=str(data.get("section", "")),
            signals=tuple(str(item) for item in raw_signals),
            derived_from=(
                str(data["derived_from"])
                if data.get("derived_from") is not None
                else None
            ),
            protected=bool(data.get("protected", False)),
            required=bool(data.get("required", False)),
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
