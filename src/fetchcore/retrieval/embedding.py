"""Backend-neutral embedding and Projection v3 serialization contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Protocol

from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectedKnowledgeRecord,
    projection_contract_fingerprint,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent


VECTOR_EMBEDDING_SERIALIZATION_VERSION = "2"


class ProjectionEmbeddingMode(StrEnum):
    """Document representation sent to an embedding provider."""

    RAW = "raw"
    PATH_CONTENT = "path-content"
    STRUCTURAL = "structural"
    PROJECTION_V3 = "projection-v3"


_PROJECTION_V3_DOCUMENT_FIELDS = (
    "source_type",
    "knowledge_type",
    "language",
    "technical_role",
    "path",
    "filename",
    "symbols",
    "symbol_types",
    "identifiers_exact",
    "content_identifiers_exact",
    "path_segments",
    "route_methods",
    "route_paths",
    "route_mechanisms",
    "import_targets",
    "dependency_targets",
    "relation_types",
    "relation_targets",
    "relation_tokens",
    "documentation_text",
    "structural_tokens",
    "content",
)
_DOCUMENT_FIELDS_BY_MODE = {
    ProjectionEmbeddingMode.RAW: ("content",),
    ProjectionEmbeddingMode.PATH_CONTENT: ("path", "filename", "content"),
    ProjectionEmbeddingMode.STRUCTURAL: (
        "path",
        "filename",
        "technical_role",
        "symbols",
        "symbol_types",
        "identifiers",
        "content",
    ),
    ProjectionEmbeddingMode.PROJECTION_V3: _PROJECTION_V3_DOCUMENT_FIELDS,
}
_DOCUMENT_SOURCE_FIELDS = {"identifiers": "identifiers_exact"}


def vector_embedding_serializer_fingerprint(
    mode: ProjectionEmbeddingMode | str = ProjectionEmbeddingMode.PROJECTION_V3,
) -> str:
    """Return the deterministic fingerprint of the embedding serialization contract."""
    parsed_mode = ProjectionEmbeddingMode(mode)
    payload = {
        "fields": _DOCUMENT_FIELDS_BY_MODE[parsed_mode],
        "mode": parsed_mode.value,
        "version": VECTOR_EMBEDDING_SERIALIZATION_VERSION,
    }
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


class EmbeddingProvider(Protocol):
    """Backend-neutral contract for deterministic text embedding providers."""
    provider_id: str
    model_id: str

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed the supplied texts using the configured embedding identity."""
        ...


class ProjectionV3EmbeddingSerializer:
    """Deterministic embedding text derived only from Projection v3 fields."""

    def __init__(
        self,
        projection_version: str = PROJECTION_VERSION,
        mode: ProjectionEmbeddingMode | str = ProjectionEmbeddingMode.PROJECTION_V3,
    ) -> None:
        """Initialize ProjectionV3EmbeddingSerializer with its explicit dependencies."""
        self.projection_version = projection_version
        self.projection_fingerprint = projection_contract_fingerprint(projection_version)
        self.mode = ProjectionEmbeddingMode(mode)
        self.document_fields = _DOCUMENT_FIELDS_BY_MODE[self.mode]
        self.serializer_fingerprint = vector_embedding_serializer_fingerprint(self.mode)

    def serialize_record(self, record: ProjectedKnowledgeRecord) -> str:
        """Serialize one Projection v3 record into deterministic embedding text."""
        fields = record.to_mapping()
        self._validate_projection(fields)
        if self.mode is ProjectionEmbeddingMode.RAW:
            rendered = "\n".join(_string_values(fields.get("content"))).strip()
            if not rendered:
                raise KnowledgeSearchError(
                    "Projection v3 record produced empty embedding text",
                    category="schema_mismatch",
                )
            return rendered
        lines: list[str] = []
        for field_name in self.document_fields:
            source_field = _DOCUMENT_SOURCE_FIELDS.get(field_name, field_name)
            values = _string_values(fields.get(source_field))
            if not values:
                continue
            lines.append(f"{field_name}:")
            lines.extend(values)
        rendered = "\n".join(lines).strip()
        if not rendered:
            raise KnowledgeSearchError(
                "Projection v3 record produced empty embedding text",
                category="schema_mismatch",
            )
        return rendered

    def serialize_query(self, intent: SearchIntent) -> str:
        """Serialize search intent into deterministic embedding query text."""
        from fetchcore.retrieval.config import RetrievalBackendCapabilities
        from fetchcore.retrieval.translation import validate_intent_translation
        validate_intent_translation(intent, channel="dense",
                                    capabilities=RetrievalBackendCapabilities(canonical_query=True))
        groups: dict[str, list[str]] = {
            "exact": [],
            "reference": [],
            "semantic": [],
            "hint": [],
        }
        seen: set[tuple[str, str]] = set()
        signal_key: tuple[str, str]
        for signal in intent.signals:
            value = signal.value.strip()
            signal_key = (signal.type, value.casefold())
            if value and signal.type in groups and signal_key not in seen:
                seen.add(signal_key)
                groups[signal.type].append(value)
        for signal_type, values in (
            ("exact", intent.exact_terms),
            ("semantic", intent.semantic_terms),
            ("hint", intent.hints),
        ):
            for raw_value in values:
                value = raw_value.strip()
                signal_key = (signal_type, value.casefold())
                if value and signal_key not in seen:
                    seen.add(signal_key)
                    groups[signal_type].append(value)
        lines: list[str] = []
        for signal_type in ("exact", "reference", "semantic", "hint"):
            serialized_values = groups[signal_type]
            if serialized_values:
                lines.append(f"{signal_type}:")
                lines.extend(serialized_values)
        rendered = "\n".join(lines).strip()
        if intent.canonical_query:
            rendered = intent.canonical_query + ("\n" + rendered if rendered else "")
        if not rendered:
            raise KnowledgeSearchError(
                "vector search requires at least one query signal",
                category="schema_mismatch",
            )
        return rendered

    def _validate_projection(self, fields: Mapping[str, object]) -> None:
        if fields.get("projection_version") != self.projection_version:
            raise KnowledgeSearchError(
                "vector serializer received an incompatible Projection version",
                category="schema_mismatch",
            )
        if fields.get("projection_fingerprint") != self.projection_fingerprint:
            raise KnowledgeSearchError(
                "vector serializer received an incompatible Projection fingerprint",
                category="schema_mismatch",
                metadata={"rebuild_required": True},
            )


def validate_vector_dimensions(
    vectors: tuple[tuple[float, ...], ...],
) -> int:
    """Validate vector shape consistency and return the shared dimensions."""
    if not vectors or not vectors[0]:
        raise KnowledgeSearchError(
            "embedding provider returned no usable vectors",
            category="embedding_failed",
        )
    dimensions = len(vectors[0])
    if any(len(vector) != dimensions for vector in vectors):
        raise KnowledgeSearchError(
            "embedding provider returned inconsistent vector dimensions",
            category="schema_mismatch",
        )
    return dimensions


def _string_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def __getattr__(name: str) -> object:
    """Keep historical Ollama imports without coupling contracts to the adapter."""
    if name in {"OllamaEmbeddingSettings", "OllamaEmbeddingProvider"}:
        from fetchcore.backends import ollama

        return getattr(ollama, name)
    raise AttributeError(name)
