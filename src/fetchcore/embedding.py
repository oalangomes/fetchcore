"""Backend-neutral embedding and Projection v3 serialization contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from time import sleep
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectedKnowledgeRecord,
    projection_contract_fingerprint,
)
from fetchcore.search import KnowledgeSearchError, SearchIntent


VECTOR_EMBEDDING_SERIALIZATION_VERSION = "2"
_OLLAMA_TRANSIENT_RETRY_DELAYS = (0.25, 1.0)


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


@dataclass(frozen=True)
class OllamaEmbeddingSettings:
    """Transport/model identity for the neutral Ollama embedding provider."""

    url: str = "http://localhost:11434"
    model: str = "embeddinggemma"
    timeout_seconds: float = 30.0
    request_batch_size: int = 32


class OllamaEmbeddingProvider:
    """Local Ollama embedding provider using POST /api/embed."""

    provider_id = "ollama"

    def __init__(
        self,
        settings: OllamaEmbeddingSettings | None = None,
        *,
        url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        request_batch_size: int | None = None,
    ) -> None:
        """Initialize OllamaEmbeddingProvider with its explicit dependencies."""
        resolved = settings or OllamaEmbeddingSettings()
        self.url = (url or resolved.url).rstrip("/")
        self.model_id = (model or resolved.model).strip()
        self.timeout_seconds = (
            resolved.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        self.request_batch_size = (
            resolved.request_batch_size
            if request_batch_size is None
            else request_batch_size
        )
        if not self.model_id:
            raise KnowledgeSearchError(
                "embedding model must not be empty",
                category="schema_mismatch",
            )
        if self.timeout_seconds <= 0 or not math.isfinite(self.timeout_seconds):
            raise KnowledgeSearchError(
                "embedding timeout_seconds must be positive",
                category="schema_mismatch",
            )
        if self.request_batch_size < 1:
            raise KnowledgeSearchError(
                "embedding request_batch_size must be positive",
                category="schema_mismatch",
            )

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed the supplied texts using bounded transport batches while preserving order."""
        if not texts:
            return ()
        vectors: list[tuple[float, ...]] = []
        for offset in range(0, len(texts), self.request_batch_size):
            batch = texts[offset : offset + self.request_batch_size]
            vectors.extend(
                self._embed_batch(
                    batch,
                    batch_offset=offset,
                    total_input_count=len(texts),
                )
            )
        return tuple(vectors)

    def _embed_batch(
        self,
        texts: tuple[str, ...],
        *,
        batch_offset: int,
        total_input_count: int,
    ) -> tuple[tuple[float, ...], ...]:
        request = Request(
            f"{self.url}/api/embed",
            data=json.dumps(
                {
                    "model": self.model_id,
                    "input": list(texts),
                    "truncate": True,
                },
                separators=(",", ":"),
            ).encode("utf-8"),
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        diagnostics = {
            "embedding_model": self.model_id,
            "input_count": len(texts),
            "batch_offset": batch_offset,
            "total_input_count": total_input_count,
            "max_input_chars": max((len(text) for text in texts), default=0),
            "total_input_chars": sum(len(text) for text in texts),
        }
        raw: bytes | None = None
        for attempt, retry_delay in enumerate(
            (*_OLLAMA_TRANSIENT_RETRY_DELAYS, None),
            start=1,
        ):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
            except HTTPError as exc:
                metadata: dict[str, object] = {
                    "http_status": exc.code,
                    "attempt": attempt,
                    **diagnostics,
                }
                provider_error = _ollama_http_error_message(exc)
                if provider_error is not None:
                    metadata["provider_error"] = provider_error
                if (
                    retry_delay is not None
                    and exc.code == 400
                    and _ollama_transient_embedding_error(provider_error)
                ):
                    sleep(retry_delay)
                    continue
                detail = f": {provider_error}" if provider_error is not None else ""
                raise KnowledgeSearchError(
                    f"embedding request failed with HTTP {exc.code}{detail}",
                    category="embedding_failed",
                    metadata=metadata,
                ) from exc
            except TimeoutError as exc:
                raise KnowledgeSearchError(
                    "embedding request timed out",
                    category="timeout",
                    metadata=diagnostics,
                ) from exc
            except URLError as exc:
                raise KnowledgeSearchError(
                    "embedding provider is unavailable",
                    category="unavailable",
                    metadata=diagnostics,
                ) from exc
            break
        assert raw is not None

        payload = _json_object(raw, source="embedding provider", operation="/api/embed")
        raw_embeddings = payload.get("embeddings")
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
            raise KnowledgeSearchError(
                "embedding provider returned an unexpected vector count",
                category="response_parsing_failure",
                metadata=diagnostics,
            )
        vectors: list[tuple[float, ...]] = []
        for raw_vector in raw_embeddings:
            if not isinstance(raw_vector, list):
                raise KnowledgeSearchError(
                    "embedding provider returned an invalid vector",
                    category="response_parsing_failure",
                    metadata=diagnostics,
                )
            vector = tuple(_finite_float(item, "embedding value") for item in raw_vector)
            if not vector:
                raise KnowledgeSearchError(
                    "embedding provider returned an empty vector",
                    category="response_parsing_failure",
                    metadata=diagnostics,
                )
            vectors.append(vector)
        return tuple(vectors)


def _ollama_http_error_message(exc: HTTPError) -> str | None:
    try:
        raw = exc.read(4096)
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("error")
    if not isinstance(value, str):
        return None
    message = value.strip()
    return message[:512] if message else None


def _ollama_transient_embedding_error(message: str | None) -> bool:
    """Return whether an Ollama HTTP 400 describes a transient local transport failure."""

    if message is None:
        return False

    normalized = message.casefold()

    if "health resp:" in normalized:
        return True

    if "do embedding request:" in normalized and (
        "eof" in normalized or "connect" in normalized
    ):
        return True

    return (
        "dial tcp 127.0.0.1:" in normalized
        and "connectex:" in normalized
        and "/tokenize" in normalized
    )


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


def _json_object(raw: bytes, *, source: str, operation: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeSearchError(
            f"{source} returned invalid JSON",
            category="response_parsing_failure",
            metadata={"operation": operation},
        ) from exc
    if not isinstance(value, dict):
        raise KnowledgeSearchError(
            f"{source} returned an unexpected response shape",
            category="response_parsing_failure",
            metadata={"operation": operation},
        )
    return {str(key): item for key, item in value.items()}


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            f"{name} must be numeric",
            category="response_parsing_failure",
        ) from exc
    if not math.isfinite(result):
        raise KnowledgeSearchError(
            f"{name} must be finite",
            category="response_parsing_failure",
        )
    return result


def _string_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)
