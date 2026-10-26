"""Operator-owned MongoDB deployment settings."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from fetchcore.projection import PROJECTION_VERSION
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.projection_matching import (
    DEFAULT_FIELD_BOOSTS,
    DEFAULT_SIGNAL_TYPE_BOOSTS,
    validate_projection_v3_controls,
)


@dataclass(frozen=True)
class MongoDBSettings:
    uri: str = field(default="mongodb://localhost:27017", repr=False)
    database: str = "fetchcore"
    collection: str = "repository_knowledge"
    search_index: str = "fetchcore-search"
    vector_index: str = "fetchcore-vector"
    timeout_seconds: float = 10.0
    batch_size: int = 200
    vector_dimensions: int = 768
    projection_version: str = PROJECTION_VERSION
    field_boosts: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_FIELD_BOOSTS))
    signal_type_boosts: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SIGNAL_TYPE_BOOSTS)
    )
    diagnostic_candidate_window: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> MongoDBSettings:
        data = dict(value or {})
        allowed = set(cls.__dataclass_fields__)
        if data.keys() - allowed:
            raise KnowledgeSearchError("Unknown MongoDB settings", category="schema_mismatch")
        uri = data.get("uri", cls.__dataclass_fields__["uri"].default)
        if (
            not isinstance(uri, str)
            or not uri.startswith(("mongodb://", "mongodb+srv://"))
            or not uri.split("://", 1)[1].split("/", 1)[0]
            or any(char.isspace() for char in uri)
        ):
            raise KnowledgeSearchError("Invalid MongoDB connection URI", category="schema_mismatch")
        names: dict[str, str] = {}
        for key in ("database", "collection", "search_index", "vector_index"):
            raw = data.get(key, getattr(cls, key))
            if (
                not isinstance(raw, str) or not raw.strip()
                or any(char in raw for char in '\0/\\ "$*<>:|?')
                or (key == "database" and "." in raw)
                or raw.startswith("system.")
            ):
                raise KnowledgeSearchError(
                    f"Invalid MongoDB {key}", category="schema_mismatch"
                )
            names[key] = raw
        timeout = data.get("timeout_seconds", cls.timeout_seconds)
        if (
            isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0.05 <= timeout <= 300
        ):
            raise KnowledgeSearchError("Invalid MongoDB timeout", category="schema_mismatch")
        numbers: dict[str, int] = {}
        for key, ceiling in (("batch_size", 5000), ("vector_dimensions", 8192)):
            raw = data.get(key, getattr(cls, key))
            if type(raw) is not int or not 1 <= raw <= ceiling:
                raise KnowledgeSearchError(
                    f"Invalid MongoDB {key}", category="schema_mismatch"
                )
            numbers[key] = raw
        if names["search_index"] == names["vector_index"]:
            raise KnowledgeSearchError(
                "MongoDB search and vector index names must differ", category="schema_mismatch"
            )
        controls = validate_projection_v3_controls(data, backend_name="MongoDB")
        return cls(
            uri=uri, database=names["database"], collection=names["collection"],
            search_index=names["search_index"], vector_index=names["vector_index"],
            timeout_seconds=float(timeout), batch_size=numbers["batch_size"],
            vector_dimensions=numbers["vector_dimensions"],
            projection_version=controls.projection_version,
            field_boosts=controls.field_boosts, signal_type_boosts=controls.signal_type_boosts,
            diagnostic_candidate_window=controls.diagnostic_candidate_window,
        )
