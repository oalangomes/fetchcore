"""Apache Solr projection, index lifecycle, and knowledge-search backend."""

from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn, Protocol, TypedDict, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from fetchcore.evidence import QueryMatchEvidence
from fetchcore.embedding import (
    VECTOR_EMBEDDING_SERIALIZATION_VERSION,
    EmbeddingProvider,
    OllamaEmbeddingProvider,
    OllamaEmbeddingSettings,
    ProjectionEmbeddingMode,
    ProjectionV3EmbeddingSerializer,
    validate_vector_dimensions,
)
from fetchcore.fusion import (
    DEFAULT_RRF_K,
    FusionCandidate,
    FusionChannelSignal,
    FusionInput,
    FusionKernel,
    FusionRequest,
)
from fetchcore.retrieval import RetrievalBackendCapabilities, RetrievalConfig
from fetchcore.search import (
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchRequirement,
    SearchResult,
)
from fetchcore.search import SearchScope as KnowledgeSearchScope
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexer,
    RepositoryIndexResult,
)
from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
    corpus_checksum as _corpus_checksum,
    extract_content_identifiers as _extract_content_identifiers,
    extract_identifiers as _extract_identifiers,
    projection_contract_fingerprint,
)
from fetchcore.projection_semantics import (
    DEFAULT_FIELD_BOOSTS,
    DEFAULT_SIGNAL_TYPE_BOOSTS,
    QUERY_CHANNELS_BY_SIGNAL_TYPE,
    QUERY_FIELDS_BY_SIGNAL_TYPE,
    SIGNAL_FIELD_FACTORS,
    ProjectionV3MatchResult,
    allowed_boost_fields,
    bounded_diagnostic_value,
    bounded_diagnostic_metadata,
    channel_query_fields,
    channel_strength,
    classify_capabilities,
    distribution,
    dedupe,
    executable_declaration_patterns,
    executable_symbol_types,
    float_value,
    hint_alignment_max,
    implementation_technical_roles,
    keyword_match_channels,
    match_projection_record,
    matches_structural_identity,
    max_diagnostic_relation_edges,
    max_diagnostic_signal_matches,
    max_diagnostic_value_chars,
    max_diagnostic_values_per_channel,
    neutral_match_channels,
    normalize_provider_scores,
    optional_int,
    optional_str,
    query_field_boost,
    query_match_evidence,
    query_match_contribution,
    query_signal_multiplier,
    query_type_counts,
    relationships,
    repository_source_documents,
    score_distribution,
    semantic_channel_strength,
    semantic_concepts,
    semantic_reuse_max,
    semantic_token_pattern,
    SIGNAL_FIELD_FACTORS_V3,
    structural_observations,
    structural_candidate_evidence,
    structural_channel_strength,
    string_values,
    technical_role_value,
    typed_query_entries,
    typed_query_mapping_telemetry,
    validate_projection_v3_controls,
)


SOLR_PROJECTION_VERSION = PROJECTION_VERSION
SolrProjectionCompiler = ProjectionV3Compiler
SolrCanonicalDocumentReader = ProjectionV3CanonicalDocumentReader
DEFAULT_SOLR_FIELD_BOOSTS = DEFAULT_FIELD_BOOSTS
DEFAULT_SOLR_SIGNAL_TYPE_BOOSTS = DEFAULT_SIGNAL_TYPE_BOOSTS
_SIGNAL_FIELD_FACTORS = SIGNAL_FIELD_FACTORS
_V3_QUERY_FIELDS_BY_SIGNAL_TYPE = QUERY_FIELDS_BY_SIGNAL_TYPE
_V3_SIGNAL_FIELD_FACTORS = SIGNAL_FIELD_FACTORS_V3
_SEMANTIC_TOKEN_PATTERN = semantic_token_pattern
_EXECUTABLE_SYMBOL_TYPES = executable_symbol_types
_EXECUTABLE_DECLARATION_PATTERNS = executable_declaration_patterns
_IMPLEMENTATION_TECHNICAL_ROLES = implementation_technical_roles
_MATCH_CHANNELS_BY_SIGNAL_TYPE = QUERY_CHANNELS_BY_SIGNAL_TYPE
_STRUCTURAL_CHANNEL_STRENGTH = structural_channel_strength
_SEMANTIC_CHANNEL_STRENGTH = semantic_channel_strength
_SEMANTIC_REUSE_MAX = semantic_reuse_max
_HINT_ALIGNMENT_MAX = hint_alignment_max
_MAX_DIAGNOSTIC_SIGNAL_MATCHES = max_diagnostic_signal_matches
_MAX_DIAGNOSTIC_VALUES_PER_CHANNEL = max_diagnostic_values_per_channel
_MAX_DIAGNOSTIC_RELATION_EDGES = max_diagnostic_relation_edges
_MAX_DIAGNOSTIC_VALUE_CHARS = max_diagnostic_value_chars
_ALLOWED_BOOST_FIELDS = allowed_boost_fields
_CHANNEL_QUERY_FIELDS = channel_query_fields
_KEYWORD_MATCH_CHANNELS = keyword_match_channels
_NEUTRAL_MATCH_CHANNELS = neutral_match_channels
_MatchEvidence = ProjectionV3MatchResult
_repository_source_documents = repository_source_documents
_typed_query_entries = typed_query_entries
_query_field_boost = query_field_boost
_query_signal_multiplier = query_signal_multiplier
_typed_query_mapping_telemetry = typed_query_mapping_telemetry
_solr_structural_observations = structural_observations
_solr_relationships = relationships
_solr_query_match_contribution = query_match_contribution
_matches = match_projection_record
_capabilities = classify_capabilities
_structural_candidate_evidence = structural_candidate_evidence
_channel_strength = channel_strength
_semantic_concepts = semantic_concepts
_matches_structural_identity = matches_structural_identity
_provider_relevance_scores = normalize_provider_scores
_diagnostic_metadata = bounded_diagnostic_metadata
_query_type_counts = query_type_counts
_score_distribution = score_distribution
_distribution = distribution
_string_values = string_values
_dedupe = dedupe
_bounded_diagnostic_value = bounded_diagnostic_value
_technical_role_value = technical_role_value
_float_value = float_value
_optional_int = optional_int
_optional_str = optional_str


_COLLECTION_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")
SOLR_VECTOR_FIELD = "embedding_vector"
SOLR_VECTOR_DIMENSIONS = 768
SOLR_VECTOR_FIELD_TYPE = f"knn_vector_{SOLR_VECTOR_DIMENSIONS}"
_REQUIRED_SCHEMA_FIELDS = frozenset(
    {
        "id",
        "record_kind",
        "source_type",
        "source_ref",
        "chunk_ref",
        "repository",
        "branch",
        "document_revision",
        "path",
        "filename",
        "language",
        "knowledge_type",
        "technical_role",
        "symbols",
        "symbol_types",
        "symbol_tokens",
        "content",
        "line_start",
        "line_end",
        "checksum",
        "source_checksum",
        "identifiers_exact",
        "content_identifiers_exact",
        "filename_exact",
        "path_text",
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
        "canonical_payload",
        SOLR_VECTOR_FIELD,
        "embedding_provider",
        "embedding_model",
        "vector_dimensions",
        "embedding_serializer_mode",
        "embedding_serializer_version",
        "embedding_serializer_fingerprint",
        "projection_fingerprint",
        "projection_version",
        "snapshot_revision",
        "corpus_checksum",
        "document_count",
        "snapshot_indexed_at",
        "snapshot_status",
    }
)
_VECTOR_SCHEMA_FIELDS = frozenset(
    {
        SOLR_VECTOR_FIELD,
        "embedding_provider",
        "embedding_model",
        "vector_dimensions",
        "embedding_serializer_mode",
        "embedding_serializer_version",
        "embedding_serializer_fingerprint",
    }
)
_CRITICAL_SCHEMA_FIELD_PROPERTIES: dict[str, dict[str, object]] = {
    "id": {"type": "string"},
    "record_kind": {"type": "string"},
    "repository": {"type": "string"},
    "identifiers_exact": {"type": "identifier_ci"},
    "content_identifiers_exact": {"type": "identifier_ci"},
    "filename_exact": {"type": "identifier_ci"},
    "symbols": {"type": "identifier_ci"},
    "symbol_tokens": {"type": "identifier_ci"},
    "content": {"type": "text_general"},
    "path_text": {"type": "text_general"},
    "path_segments": {"type": "identifier_ci"},
    "route_methods": {"type": "identifier_ci"},
    "route_paths": {"type": "identifier_ci"},
    "route_mechanisms": {"type": "identifier_ci"},
    "import_targets": {"type": "identifier_ci"},
    "dependency_targets": {"type": "identifier_ci"},
    "relation_types": {"type": "identifier_ci"},
    "relation_targets": {"type": "identifier_ci"},
    "relation_tokens": {"type": "identifier_ci"},
    "documentation_text": {"type": "text_general"},
    "structural_tokens": {"type": "identifier_ci"},
    "canonical_payload": {"type": "string", "indexed": False, "stored": True},
    SOLR_VECTOR_FIELD: {"type": SOLR_VECTOR_FIELD_TYPE, "indexed": True, "stored": True},
    "vector_dimensions": {"type": "pint"},
    "projection_fingerprint": {"type": "string"},
}


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def extract_identifiers(document: KnowledgeDocument) -> tuple[str, ...]:
    """Compatibility export for the projection compiler identifier policy."""

    return _extract_identifiers(document)


def extract_content_identifiers(document: KnowledgeDocument) -> tuple[str, ...]:
    """Compatibility export for source-aware exact identifier extraction."""

    return _extract_content_identifiers(document)


def corpus_checksum(documents: Iterable[KnowledgeDocument]) -> str:
    """Compatibility wrapper for the backend-neutral snapshot checksum."""

    return _corpus_checksum(documents)


def _solr_query_matches(
    match: ProjectionV3MatchResult,
    *,
    source_ref: str,
) -> tuple[QueryMatchEvidence, ...]:
    """Compatibility wrapper retaining the frozen Solr match provenance."""

    return query_match_evidence(
        match,
        source_ref=source_ref,
        provenance="solr_projection_v3_match",
    )


@dataclass(frozen=True)
class SolrSettings:
    """Validated backend settings kept outside business and governance logic."""

    url: str = "http://localhost:8983/solr"
    collection: str = "fetchcore-knowledge"
    timeout_seconds: float = 10.0
    projection_version: str = SOLR_PROJECTION_VERSION
    field_boosts: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_SOLR_FIELD_BOOSTS))
    signal_type_boosts: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SOLR_SIGNAL_TYPE_BOOSTS)
    )
    batch_size: int = 200
    diagnostic_candidate_window: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> SolrSettings:
        """Build the value from a mapping while validating its public contract."""
        data = dict(value or {})
        url = str(data.get("url", cls.url)).rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise KnowledgeSearchError(
                "Solr URL must be an absolute HTTP(S) URL",
                category="schema_mismatch",
            )
        collection = str(data.get("collection", cls.collection)).strip()
        if not _COLLECTION_PATTERN.fullmatch(collection):
            raise KnowledgeSearchError(
                "Solr collection contains unsupported characters",
                category="schema_mismatch",
            )
        try:
            timeout = float(
                cast(float | int | str, data.get("timeout_seconds", cls.timeout_seconds))
            )
            batch_size = int(cast(float | int | str, data.get("batch_size", cls.batch_size)))
        except (TypeError, ValueError) as exc:
            raise KnowledgeSearchError(
                "Solr timeout_seconds and batch_size must be numeric",
                category="schema_mismatch",
            ) from exc
        if not 0.05 <= timeout <= 300:
            raise KnowledgeSearchError(
                "Solr timeout_seconds must be between 0.05 and 300",
                category="schema_mismatch",
            )
        if not 1 <= batch_size <= 5_000:
            raise KnowledgeSearchError(
                "Solr batch_size must be between 1 and 5000",
                category="schema_mismatch",
            )
        controls = validate_projection_v3_controls(data, backend_name="Solr")
        return cls(
            url=url,
            collection=collection,
            timeout_seconds=timeout,
            projection_version=controls.projection_version,
            field_boosts=controls.field_boosts,
            signal_type_boosts=controls.signal_type_boosts,
            batch_size=batch_size,
            diagnostic_candidate_window=controls.diagnostic_candidate_window,
        )


class SolrTransport(Protocol):
    """Small transport seam used by both the backend and index lifecycle."""

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Execute a GET request against the configured backend."""
        ...

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Execute a POST request against the configured backend."""
        ...


class UrllibSolrTransport:
    """Dependency-free JSON transport with stable error categorization."""

    def __init__(self, settings: SolrSettings) -> None:
        """Initialize UrllibSolrTransport with its explicit dependencies."""
        self.settings = settings
        self.base_url = f"{settings.url}/{quote(settings.collection, safe='')}"

    def get(
        self,
        path: str,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Execute a GET request against the configured backend."""
        return self._request("GET", path, params=params)

    def post(
        self,
        path: str,
        payload: object,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        """Execute a POST request against the configured backend."""
        return self._request("POST", path, payload=payload, params=params)

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        params: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        query = urlencode(params or {}, doseq=True)
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{query}"
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code == 404:
                category = "collection_missing"
            elif exc.code in {400, 409, 422}:
                category = "schema_mismatch"
            elif path.lstrip("/") == "update":
                category = "index_update_failure"
            else:
                category = "unavailable"
            raise KnowledgeSearchError(
                f"Solr request failed with HTTP {exc.code}",
                category=category,
                metadata={"http_status": exc.code, "operation": path.lstrip("/")},
            ) from exc
        except TimeoutError as exc:
            raise KnowledgeSearchError(
                "Solr request timed out",
                category="timeout",
                metadata={"operation": path.lstrip("/")},
            ) from exc
        except URLError as exc:
            category = "timeout" if isinstance(exc.reason, TimeoutError) else "unavailable"
            raise KnowledgeSearchError(
                "Solr is unavailable",
                category=category,
                metadata={"operation": path.lstrip("/")},
            ) from exc
        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgeSearchError(
                "Solr returned an invalid JSON response",
                category="response_parsing_failure",
                metadata={"operation": path.lstrip("/")},
            ) from exc
        if not isinstance(decoded, dict):
            raise KnowledgeSearchError(
                "Solr returned an unexpected response shape",
                category="response_parsing_failure",
                metadata={"operation": path.lstrip("/")},
            )
        return cast(dict[str, object], decoded)


class SolrDocumentMapper:
    """Compatibility facade over the explicit compiler and canonical reader."""

    def __init__(self, projection_version: str = SOLR_PROJECTION_VERSION) -> None:
        """Initialize SolrDocumentMapper with its explicit dependencies."""
        self.projection_version = projection_version
        self.compiler = ProjectionV3Compiler(projection_version)
        self.reader = ProjectionV3CanonicalDocumentReader(projection_version)

    def to_solr(
        self,
        document: KnowledgeDocument,
    ) -> dict[str, object]:
        """Serialize canonical knowledge into the compatibility Solr representation."""
        return self.compiler.compile(document).to_solr()

    def from_solr(self, value: Mapping[str, object]) -> KnowledgeDocument:
        """Read canonical knowledge from the compatibility Solr representation."""
        return self.reader.read(value)


@dataclass(frozen=True)
class SolrIndexSnapshot:
    """Persisted identity and compatibility metadata for a Solr repository snapshot."""
    repository: str
    branch: str | None
    revision: str
    corpus_checksum: str
    document_count: int
    projection_version: str
    indexed_at: str
    status: str = "ready"
    embedding_provider: str | None = None
    embedding_model: str | None = None
    vector_dimensions: int | None = None
    embedding_serializer_mode: str | None = None
    embedding_serializer_version: str | None = None
    embedding_serializer_fingerprint: str | None = None
    projection_fingerprint: str | None = field(
        default_factory=projection_contract_fingerprint
    )

    @property
    def id(self) -> str:
        """Return the deterministic persisted snapshot identifier."""
        digest = hashlib.sha256(self.repository.encode("utf-8")).hexdigest()
        return f"snapshot:{digest}"

    def to_solr(self) -> dict[str, object]:
        """Serialize canonical knowledge into the compatibility Solr representation."""
        return {
            "id": self.id,
            "record_kind": "snapshot",
            "repository": self.repository,
            "branch": self.branch,
            "snapshot_revision": self.revision,
            "corpus_checksum": self.corpus_checksum,
            "document_count": self.document_count,
            **(
                {"embedding_provider": self.embedding_provider}
                if self.embedding_provider is not None
                else {}
            ),
            **(
                {"embedding_model": self.embedding_model}
                if self.embedding_model is not None
                else {}
            ),
            **(
                {"vector_dimensions": self.vector_dimensions}
                if self.vector_dimensions is not None
                else {}
            ),
            **(
                {"embedding_serializer_mode": self.embedding_serializer_mode}
                if self.embedding_serializer_mode is not None
                else {}
            ),
            **(
                {"embedding_serializer_version": self.embedding_serializer_version}
                if self.embedding_serializer_version is not None
                else {}
            ),
            **(
                {"embedding_serializer_fingerprint": self.embedding_serializer_fingerprint}
                if self.embedding_serializer_fingerprint is not None
                else {}
            ),
            "projection_fingerprint": self.projection_fingerprint,
            "projection_version": self.projection_version,
            "snapshot_indexed_at": self.indexed_at,
            "snapshot_status": self.status,
        }

    @classmethod
    def from_solr(cls, value: Mapping[str, object]) -> SolrIndexSnapshot:
        """Read canonical knowledge from the compatibility Solr representation."""
        try:
            return cls(
                repository=str(value["repository"]),
                branch=None if value.get("branch") is None else str(value["branch"]),
                revision=str(value["snapshot_revision"]),
                corpus_checksum=str(value["corpus_checksum"]),
                document_count=int(cast(float | int | str, value["document_count"])),
                projection_version=str(value["projection_version"]),
                indexed_at=str(value["snapshot_indexed_at"]),
                status=str(value["snapshot_status"]),
                embedding_provider=(
                    None
                    if value.get("embedding_provider") is None
                    else str(value["embedding_provider"])
                ),
                embedding_model=(
                    None
                    if value.get("embedding_model") is None
                    else str(value["embedding_model"])
                ),
                vector_dimensions=(
                    None
                    if value.get("vector_dimensions") is None
                    else int(cast(float | int | str, value["vector_dimensions"]))
                ),
                embedding_serializer_mode=(
                    None
                    if value.get("embedding_serializer_mode") is None
                    else str(value["embedding_serializer_mode"])
                ),
                embedding_serializer_version=(
                    None
                    if value.get("embedding_serializer_version") is None
                    else str(value["embedding_serializer_version"])
                ),
                embedding_serializer_fingerprint=(
                    None
                    if value.get("embedding_serializer_fingerprint") is None
                    else str(value["embedding_serializer_fingerprint"])
                ),
                projection_fingerprint=(
                    None
                    if value.get("projection_fingerprint") is None
                    else str(value["projection_fingerprint"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KnowledgeSearchError(
                "Solr snapshot record is invalid",
                category="response_parsing_failure",
            ) from exc


@dataclass(frozen=True)
class SolrIndexSyncResult:
    """Observable result of a Solr index synchronization."""
    mode: str
    snapshot: SolrIndexSnapshot
    added: int
    updated: int
    deleted: int
    unchanged: int
    duration_ms: int

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "mode": self.mode,
            "snapshot": self.snapshot.to_solr(),
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "duration_ms": self.duration_ms,
        }


class _SelectResult(TypedDict):
    docs: list[dict[str, object]]
    num_found: int


@dataclass(frozen=True)
class _StoredProjection:
    records: tuple[dict[str, object], ...]
    snapshot: SolrIndexSnapshot | None
    documents: tuple[KnowledgeDocument, ...]


@dataclass(frozen=True)
class _SolrEmbeddingIdentity:
    provider: str
    model: str
    dimensions: int
    serializer_mode: str
    serializer_version: str
    serializer_fingerprint: str


class SolrIndexStore:
    """Persistent projection lifecycle over one standalone Solr collection."""

    def __init__(
        self,
        transport: SolrTransport,
        settings: SolrSettings,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        clock: Callable[[], str] = _utc_now,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize SolrIndexStore with its explicit dependencies."""
        self.transport = transport
        self.settings = settings
        self.mapper = SolrDocumentMapper(settings.projection_version)
        self.compiler = self.mapper.compiler
        self.projection_fingerprint = projection_contract_fingerprint(
            settings.projection_version
        )
        self.embedding_provider = embedding_provider
        self.embedding_serializer = (
            ProjectionV3EmbeddingSerializer(
                settings.projection_version,
                ProjectionEmbeddingMode.PROJECTION_V3,
            )
            if embedding_provider is not None
            else None
        )
        self.clock = clock
        self.timer = timer

    def health(self) -> dict[str, object]:
        """Return backend health information required by the lifecycle."""
        return self.transport.get("admin/ping", {"wt": "json"})

    def validate_schema(self, *, require_vectors: bool = False) -> None:
        """Execute the public validate_schema operation."""
        response = self.transport.get("schema", {"wt": "json"})
        schema = response.get("schema")
        if not isinstance(schema, dict) or schema.get("uniqueKey") != "id":
            raise KnowledgeSearchError(
                "Solr schema has an incompatible unique key",
                category="schema_mismatch",
            )
        raw_fields = schema.get("fields", [])
        if not isinstance(raw_fields, list):
            raise KnowledgeSearchError(
                "Solr schema field response is invalid",
                category="schema_mismatch",
            )
        fields = {
            str(item.get("name")): cast(dict[str, object], item)
            for item in raw_fields
            if isinstance(item, dict) and item.get("name") is not None
        }
        required_fields = (
            _REQUIRED_SCHEMA_FIELDS
            if require_vectors
            else _REQUIRED_SCHEMA_FIELDS - _VECTOR_SCHEMA_FIELDS
        )
        critical_properties = (
            _CRITICAL_SCHEMA_FIELD_PROPERTIES
            if require_vectors
            else {
                name: expected
                for name, expected in _CRITICAL_SCHEMA_FIELD_PROPERTIES.items()
                if name not in _VECTOR_SCHEMA_FIELDS
            }
        )
        missing = sorted(required_fields - fields.keys())
        incompatible: list[dict[str, object]] = []
        for name, expected in critical_properties.items():
            actual = fields.get(name)
            if actual is None:
                continue
            mismatches = {
                property_name: {
                    "expected": expected_value,
                    "actual": actual.get(property_name),
                }
                for property_name, expected_value in expected.items()
                if actual.get(property_name) != expected_value
            }
            if mismatches:
                incompatible.append({"field": name, "properties": mismatches})
        vector_field_type_mismatch: dict[str, object] | None = None
        if require_vectors:
            raw_field_types = schema.get("fieldTypes", [])
            if not isinstance(raw_field_types, list):
                raise KnowledgeSearchError(
                    "Solr schema field type response is invalid",
                    category="schema_mismatch",
                )
            field_types = {
                str(item.get("name")): cast(dict[str, object], item)
                for item in raw_field_types
                if isinstance(item, dict) and item.get("name") is not None
            }
            vector_type = field_types.get(SOLR_VECTOR_FIELD_TYPE)
            if vector_type is None:
                vector_field_type_mismatch = {
                    "field_type": SOLR_VECTOR_FIELD_TYPE,
                    "reason": "missing",
                }
            else:
                try:
                    vector_dimension = int(
                        cast(float | int | str, vector_type.get("vectorDimension"))
                    )
                except (TypeError, ValueError):
                    vector_dimension = -1
                actual_class = str(vector_type.get("class", ""))
                actual_similarity = str(vector_type.get("similarityFunction", ""))
                actual_algorithm = str(vector_type.get("knnAlgorithm", "hnsw"))
                actual_encoding = str(vector_type.get("vectorEncoding", "FLOAT32"))
                vector_type_mismatches: dict[str, dict[str, object]] = {}
                if not actual_class.endswith(".DenseVectorField"):
                    vector_type_mismatches["class"] = {
                        "expected": "DenseVectorField",
                        "actual": actual_class,
                    }
                if vector_dimension != SOLR_VECTOR_DIMENSIONS:
                    vector_type_mismatches["vectorDimension"] = {
                        "expected": SOLR_VECTOR_DIMENSIONS,
                        "actual": vector_type.get("vectorDimension"),
                    }
                if actual_similarity.casefold() != "cosine":
                    vector_type_mismatches["similarityFunction"] = {
                        "expected": "cosine",
                        "actual": actual_similarity,
                    }
                if actual_algorithm.casefold() != "hnsw":
                    vector_type_mismatches["knnAlgorithm"] = {
                        "expected": "hnsw",
                        "actual": actual_algorithm,
                    }
                if actual_encoding.upper() != "FLOAT32":
                    vector_type_mismatches["vectorEncoding"] = {
                        "expected": "FLOAT32",
                        "actual": actual_encoding,
                    }
                if vector_type_mismatches:
                    vector_field_type_mismatch = {
                        "field_type": SOLR_VECTOR_FIELD_TYPE,
                        "properties": vector_type_mismatches,
                    }
        if missing or incompatible or vector_field_type_mismatch is not None:
            raise KnowledgeSearchError(
                "Solr schema is incompatible with the knowledge projection",
                category="schema_mismatch",
                metadata={
                    "missing_fields": missing,
                    "incompatible_fields": incompatible,
                    "vector_field_type": vector_field_type_mismatch,
                },
            )

    def load_snapshot(self, repository: str) -> SolrIndexSnapshot:
        """Load and validate the active repository snapshot."""
        if not repository.strip():
            raise KnowledgeSearchError(
                "Solr snapshot lookup requires a repository identity",
                category="schema_mismatch",
            )
        filters = ["record_kind:snapshot", _term_filter("repository", repository)]
        response = self._select(
            {"q": "*:*", "fq": filters, "rows": 2, "fl": "*", "sort": "id asc"}
        )
        docs = response["docs"]
        if not docs:
            raise KnowledgeSearchError(
                "Solr has no active index snapshot",
                category="snapshot_missing",
                metadata={"repository": repository},
            )
        if len(docs) != 1:
            raise KnowledgeSearchError(
                "Solr repository scope does not select exactly one snapshot",
                category="snapshot_missing",
                metadata={"repository": repository, "snapshot_count": len(docs)},
            )
        snapshot = SolrIndexSnapshot.from_solr(docs[0])
        self._ensure_snapshot_compatible(snapshot)
        if snapshot.status != "ready":
            raise KnowledgeSearchError(
                "Solr snapshot is not ready",
                category="index_stale",
                metadata={"snapshot_status": snapshot.status},
            )
        return snapshot

    def inspect_projection(self, repository: str) -> dict[str, object]:
        """Expose persisted projection compatibility without accepting it as ready."""

        filters = ["record_kind:snapshot", _term_filter("repository", repository)]
        response = self._select({"q": "*:*", "fq": filters, "rows": 2, "fl": "*", "sort": "id asc"})
        docs = response["docs"]
        actual_version: str | None = None
        actual_fingerprint: str | None = None
        status: str | None = None
        if len(docs) == 1:
            snapshot = SolrIndexSnapshot.from_solr(docs[0])
            actual_version = snapshot.projection_version
            actual_fingerprint = snapshot.projection_fingerprint
            status = snapshot.status
        compatible = (
            len(docs) == 1
            and actual_version == self.settings.projection_version
            and actual_fingerprint == self.projection_fingerprint
            and status == "ready"
        )
        return {
            "expected_projection_version": self.settings.projection_version,
            "actual_projection_version": actual_version,
            "expected_projection_fingerprint": self.projection_fingerprint,
            "actual_projection_fingerprint": actual_fingerprint,
            "snapshot_status": status,
            "rebuild_required": not compatible,
        }

    def _ensure_snapshot_compatible(self, snapshot: SolrIndexSnapshot) -> None:
        version_matches = snapshot.projection_version == self.settings.projection_version
        fingerprint_matches = snapshot.projection_fingerprint == self.projection_fingerprint
        if version_matches and fingerprint_matches:
            return
        raise KnowledgeSearchError(
            "Solr snapshot projection contract is incompatible",
            category="schema_mismatch",
            metadata={
                "expected_projection_version": self.settings.projection_version,
                "actual_projection_version": snapshot.projection_version,
                "expected_projection_fingerprint": self.projection_fingerprint,
                "actual_projection_fingerprint": snapshot.projection_fingerprint,
                "rebuild_required": True,
            },
        )

    def load_documents(self, repository: str) -> tuple[KnowledgeDocument, ...]:
        """Load canonical documents persisted for the repository snapshot."""
        docs: list[KnowledgeDocument] = []
        start = 0
        while True:
            response = self._select(
                {
                    "q": "*:*",
                    "fq": ["record_kind:knowledge", _term_filter("repository", repository)],
                    "rows": self.settings.batch_size,
                    "start": start,
                    "fl": (
                        "record_kind,projection_version,projection_fingerprint,canonical_payload"
                    ),
                    "sort": "id asc",
                }
            )
            page = response["docs"]
            docs.extend(self.mapper.from_solr(item) for item in page)
            start += len(page)
            if not page or start >= response["num_found"]:
                break
        return tuple(docs)

    def _load_projection_records(self, repository: str) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        start = 0
        while True:
            response = self._select(
                {
                    "q": "*:*",
                    "fq": [_term_filter("repository", repository)],
                    "rows": self.settings.batch_size,
                    "start": start,
                    "fl": "*",
                    "sort": "id asc",
                }
            )
            page = response["docs"]
            records.extend(_without_solr_internal_fields(item) for item in page)
            start += len(page)
            if not page or start >= response["num_found"]:
                break
        return tuple(records)

    def full_rebuild(self, result: RepositoryIndexResult) -> SolrIndexSyncResult:
        """Replace the repository projection with a complete rebuild."""
        self._ensure_complete(result)
        if self.embedding_provider is not None:
            self.validate_schema(require_vectors=True)
        started = self.timer()
        snapshot = self._snapshot(result)
        # A prior process may have stopped after commit=false staging. Solr's
        # update log must be clean before deriving the active replacement set.
        self._rollback()
        previous = self._load_stored_projection(result.repository)
        projected_documents = self._project_documents(result.documents)
        staged_records = (*projected_documents, snapshot.to_solr())
        try:
            self._delete_ids(record["id"] for record in previous.records)
            self._upsert_records(projected_documents)
            self._stage_snapshot(snapshot)
            self._validate_staged_projection(
                expected_records=staged_records,
                previous_records=previous.records,
                expected_snapshot=snapshot,
            )
        except Exception as exc:  # noqa: BLE001 - discard must cover partial staging
            self._discard_staged_after_failure(
                repository=result.repository,
                previous=previous,
                operation="full_rebuild",
                cause=exc,
            )
        try:
            self._commit()
            self._validate_projected_state(result.documents, snapshot)
        except Exception as exc:  # noqa: BLE001 - compensation must cover any failed mutation
            self._restore_after_failure(
                repository=result.repository,
                previous=previous,
                operation="full_rebuild",
                cause=exc,
            )
        return SolrIndexSyncResult(
            mode="full_rebuild",
            snapshot=snapshot,
            added=len(result.documents),
            updated=0,
            deleted=0,
            unchanged=0,
            duration_ms=max(0, round((self.timer() - started) * 1_000)),
        )

    def incremental_sync(self, result: RepositoryIndexResult) -> SolrIndexSyncResult:
        """Persist a validated incremental repository index update."""
        self._ensure_complete(result)
        if self.embedding_provider is not None:
            self.validate_schema(require_vectors=True)
        # Direct store callers must not bypass the coordinator's rebuild gate.
        active_snapshot = self.load_snapshot(result.repository)
        if not self.embedding_snapshot_matches(active_snapshot):
            raise KnowledgeSearchError(
                "Solr embedding identity changed; a full rebuild is required",
                category="index_stale",
                metadata={
                    "repository": result.repository,
                    "rebuild_required": True,
                    "reason": "embedding_identity_changed",
                },
            )
        started = self.timer()
        changes = result.changes
        upserts = (*changes.added, *(item.document for item in changes.updated))
        snapshot = self._snapshot(result)
        previous = self._load_stored_projection(result.repository)
        try:
            self._upsert_documents(upserts)
            if changes.removed:
                self.transport.post(
                    "update",
                    {"delete": [{"id": item.id} for item in changes.removed]},
                    {"commit": "false", "wt": "json"},
                )
            self._stage_snapshot(snapshot)
            self._commit()
            self._validate_projected_state(result.documents, snapshot)
        except Exception as exc:  # noqa: BLE001 - compensation must cover any failed mutation
            self._restore_after_failure(
                repository=result.repository,
                previous=previous,
                operation="incremental_sync",
                cause=exc,
            )
        return SolrIndexSyncResult(
            mode="incremental",
            snapshot=snapshot,
            added=len(changes.added),
            updated=len(changes.updated),
            deleted=len(changes.removed),
            unchanged=len(changes.unchanged),
            duration_ms=max(0, round((self.timer() - started) * 1_000)),
        )

    def reset(self, repository: str | None = None) -> None:
        """Remove persisted projection state for the requested repository."""
        query = "*:*" if repository is None else _term_filter("repository", repository)
        self.transport.post(
            "update", {"delete": {"query": query}}, {"commit": "true", "wt": "json"}
        )

    def _upsert_documents(
        self,
        documents: Iterable[KnowledgeDocument],
    ) -> None:
        self._upsert_records(self._project_documents(tuple(documents)))

    def _project_documents(
        self,
        documents: Iterable[KnowledgeDocument],
    ) -> tuple[dict[str, object], ...]:
        source = tuple(documents)
        if not source:
            return ()
        projected: list[dict[str, object]] = []
        for offset in range(0, len(source), self.settings.batch_size):
            batch = source[offset : offset + self.settings.batch_size]
            records = tuple(self.compiler.compile(document) for document in batch)
            payloads = [record.to_solr() for record in records]
            if self.embedding_provider is not None:
                assert self.embedding_serializer is not None
                texts = tuple(
                    self.embedding_serializer.serialize_record(record)
                    for record in records
                )
                vectors = self.embedding_provider.embed(texts)
                if len(vectors) != len(records):
                    raise KnowledgeSearchError(
                        "embedding provider returned an incomplete Solr vector batch",
                        category="embedding_failed",
                    )
                dimensions = validate_vector_dimensions(vectors)
                if dimensions != SOLR_VECTOR_DIMENSIONS:
                    raise KnowledgeSearchError(
                        "Solr embedding dimensions do not match the frozen vector schema",
                        category="schema_mismatch",
                        metadata={
                            "expected_dimensions": SOLR_VECTOR_DIMENSIONS,
                            "actual_dimensions": dimensions,
                            "rebuild_required": True,
                        },
                    )
                for payload, vector in zip(payloads, vectors, strict=True):
                    payload[SOLR_VECTOR_FIELD] = list(vector)
            projected.extend(payloads)
        return tuple(projected)

    def _upsert_records(self, records: Iterable[Mapping[str, object]]) -> None:
        batch: list[dict[str, object]] = []
        for record in records:
            batch.append(dict(record))
            if len(batch) == self.settings.batch_size:
                self.transport.post("update", batch, {"commit": "false", "wt": "json"})
                batch = []
        if batch:
            self.transport.post("update", batch, {"commit": "false", "wt": "json"})

    def _delete_repository(self, repository: str) -> None:
        self.transport.post(
            "update",
            {"delete": {"query": _repository_query(repository)}},
            {"commit": "false", "wt": "json"},
        )

    def _delete_ids(self, ids: Iterable[object]) -> None:
        unique = tuple(sorted({str(item) for item in ids}))
        if not unique:
            return
        for offset in range(0, len(unique), self.settings.batch_size):
            batch = unique[offset : offset + self.settings.batch_size]
            self.transport.post(
                "update",
                {"delete": [{"id": item} for item in batch]},
                {"commit": "false", "wt": "json"},
            )

    def _stage_snapshot(self, snapshot: SolrIndexSnapshot) -> None:
        self.transport.post(
            "update",
            [snapshot.to_solr()],
            {"commit": "false", "wt": "json"},
        )

    def _commit(self) -> None:
        self.transport.post("update", {"commit": {}}, {"wt": "json"})

    def _rollback(self) -> None:
        self.transport.post("update", {"rollback": {}}, {"wt": "json"})

    def _validate_staged_projection(
        self,
        *,
        expected_records: tuple[dict[str, object], ...],
        previous_records: tuple[dict[str, object], ...],
        expected_snapshot: SolrIndexSnapshot,
    ) -> None:
        expected_by_id = {
            str(record["id"]): _comparable_projection_record(record) for record in expected_records
        }
        inspected_ids = tuple(
            sorted(
                {
                    *expected_by_id,
                    *(str(record["id"]) for record in previous_records),
                }
            )
        )
        actual_records = self._load_realtime_records(inspected_ids)
        actual_by_id = {
            str(record["id"]): _comparable_projection_record(record) for record in actual_records
        }
        if actual_by_id != expected_by_id:
            raise KnowledgeSearchError(
                "Solr staged projection does not match the complete compiled corpus",
                category="index_update_failure",
                metadata={
                    "expected_record_count": len(expected_by_id),
                    "actual_record_count": len(actual_by_id),
                    "missing_ids": sorted(expected_by_id.keys() - actual_by_id.keys()),
                    "unexpected_ids": sorted(actual_by_id.keys() - expected_by_id.keys()),
                    "mismatched_ids": sorted(
                        record_id
                        for record_id in expected_by_id.keys() & actual_by_id.keys()
                        if expected_by_id[record_id] != actual_by_id[record_id]
                    ),
                },
            )
        snapshot_type = type(expected_snapshot)
        staged_snapshot = snapshot_type.from_solr(actual_by_id[expected_snapshot.id])
        self._ensure_snapshot_compatible(staged_snapshot)
        if staged_snapshot != expected_snapshot or staged_snapshot.status != "ready":
            raise KnowledgeSearchError(
                "Solr staged snapshot does not describe a complete ready projection",
                category="index_update_failure",
            )

    def _load_realtime_records(
        self,
        ids: tuple[str, ...],
    ) -> tuple[dict[str, object], ...]:
        records: list[dict[str, object]] = []
        realtime_batch_size = min(50, self.settings.batch_size)
        for offset in range(0, len(ids), realtime_batch_size):
            batch = ids[offset : offset + realtime_batch_size]
            payload = self.transport.get(
                "get",
                {"ids": ",".join(batch), "fl": "*", "wt": "json"},
            )
            response = payload.get("response")
            if not isinstance(response, dict) or not isinstance(response.get("docs"), list):
                raise KnowledgeSearchError(
                    "Solr realtime-get response is invalid",
                    category="response_parsing_failure",
                )
            for item in cast(list[object], response["docs"]):
                if not isinstance(item, dict):
                    raise KnowledgeSearchError(
                        "Solr realtime-get returned a non-object record",
                        category="response_parsing_failure",
                    )
                records.append(_without_solr_internal_fields(item))
        return tuple(records)

    def _discard_staged_after_failure(
        self,
        *,
        repository: str,
        previous: _StoredProjection,
        operation: str,
        cause: Exception,
    ) -> NoReturn:
        try:
            self._rollback()
            restored = self._load_projection_records(repository)
            if _projection_records_checksum(restored) != _projection_records_checksum(
                previous.records
            ):
                raise KnowledgeSearchError(
                    "Solr rollback did not preserve the prior ready projection",
                    category="index_update_failure",
                )
        except Exception as rollback_error:
            raise KnowledgeSearchError(
                "Solr rebuild staging failed and rollback could not preserve the prior projection",
                category="index_update_failure",
                metadata={
                    "operation": operation,
                    "rollback": "failed",
                    "activation": "not_attempted",
                    "cause_category": _error_category(cause),
                    "rollback_category": _error_category(rollback_error),
                },
            ) from rollback_error
        raise KnowledgeSearchError(
            "Solr rebuild staging failed; the prior ready projection remains active",
            category="index_update_failure",
            metadata={
                "operation": operation,
                "rollback": "succeeded",
                "activation": "not_attempted",
                "prior_ready_snapshot_preserved": previous.snapshot is not None,
                "cause_category": _error_category(cause),
            },
        ) from cause

    def _validate_projected_state(
        self,
        expected_documents: tuple[KnowledgeDocument, ...],
        expected_snapshot: SolrIndexSnapshot,
    ) -> None:
        persisted_documents = self.load_documents(expected_snapshot.repository)
        persisted_snapshot = self.load_snapshot(expected_snapshot.repository)
        actual_checksum = corpus_checksum(persisted_documents)
        if len(persisted_documents) != len(expected_documents):
            raise KnowledgeSearchError(
                "Solr projected document count does not match the canonical corpus",
                category="index_update_failure",
                metadata={
                    "expected": len(expected_documents),
                    "actual": len(persisted_documents),
                },
            )
        expected_checksum = corpus_checksum(expected_documents)
        if actual_checksum != expected_checksum:
            raise KnowledgeSearchError(
                "Solr projected corpus checksum does not match the canonical corpus",
                category="index_update_failure",
                metadata={
                    "expected_corpus_checksum": expected_checksum,
                    "actual_corpus_checksum": actual_checksum,
                },
            )
        if persisted_snapshot != expected_snapshot:
            raise KnowledgeSearchError(
                "Solr active snapshot does not describe the persisted corpus",
                category="index_update_failure",
                metadata={
                    "expected_snapshot_revision": expected_snapshot.revision,
                    "actual_snapshot_revision": persisted_snapshot.revision,
                },
            )

    def _load_stored_projection(self, repository: str) -> _StoredProjection:
        records = self._load_projection_records(repository)
        raw_snapshot = next(
            (
                SolrIndexSnapshot.from_solr(record)
                for record in records
                if record.get("record_kind") == "snapshot"
            ),
            None,
        )
        try:
            snapshot: SolrIndexSnapshot | None = self.load_snapshot(repository)
        except KnowledgeSearchError as exc:
            if exc.category not in {
                "snapshot_missing",
                "schema_mismatch",
                "response_parsing_failure",
                "index_stale",
            }:
                raise
            snapshot = raw_snapshot
        try:
            documents = self.load_documents(repository)
        except KnowledgeSearchError as exc:
            if exc.category not in {"schema_mismatch", "response_parsing_failure"}:
                raise
            documents = ()
        return _StoredProjection(
            records=records,
            snapshot=snapshot,
            documents=documents,
        )

    def _restore_after_failure(
        self,
        *,
        repository: str,
        previous: _StoredProjection,
        operation: str,
        cause: Exception,
    ) -> NoReturn:
        try:
            self._delete_repository(repository)
            self._upsert_records(previous.records)
            self._commit()
            if previous.snapshot is not None and previous.documents:
                self._validate_projected_state(previous.documents, previous.snapshot)
            elif _projection_records_checksum(
                self._load_projection_records(repository)
            ) != _projection_records_checksum(previous.records):
                raise KnowledgeSearchError(
                    "Solr rollback did not restore the previous raw projection",
                    category="index_update_failure",
                )
        except Exception as rollback_error:
            raise KnowledgeSearchError(
                "Solr index update failed and rollback could not restore the previous projection",
                category="index_update_failure",
                metadata={
                    "operation": operation,
                    "rollback": "failed",
                    "cause_category": _error_category(cause),
                    "rollback_category": _error_category(rollback_error),
                },
            ) from rollback_error
        raise KnowledgeSearchError(
            "Solr index update failed; the previous projection was restored",
            category="index_update_failure",
            metadata={
                "operation": operation,
                "rollback": "succeeded",
                "cause_category": _error_category(cause),
            },
        ) from cause

    def _snapshot(self, result: RepositoryIndexResult) -> SolrIndexSnapshot:
        identity = self._embedding_snapshot_identity()
        return SolrIndexSnapshot(
            repository=result.repository,
            branch=result.branch,
            revision=result.revision,
            corpus_checksum=corpus_checksum(result.documents),
            document_count=len(result.documents),
            projection_version=self.settings.projection_version,
            indexed_at=self.clock(),
            embedding_provider=None if identity is None else identity.provider,
            embedding_model=None if identity is None else identity.model,
            vector_dimensions=None if identity is None else identity.dimensions,
            embedding_serializer_mode=None if identity is None else identity.serializer_mode,
            embedding_serializer_version=(
                None if identity is None else identity.serializer_version
            ),
            embedding_serializer_fingerprint=(
                None if identity is None else identity.serializer_fingerprint
            ),
            projection_fingerprint=self.projection_fingerprint,
        )

    def _embedding_snapshot_identity(self) -> _SolrEmbeddingIdentity | None:
        if self.embedding_provider is None:
            return None
        assert self.embedding_serializer is not None
        return _SolrEmbeddingIdentity(
            provider=self.embedding_provider.provider_id,
            model=self.embedding_provider.model_id,
            dimensions=SOLR_VECTOR_DIMENSIONS,
            serializer_mode=self.embedding_serializer.mode.value,
            serializer_version=VECTOR_EMBEDDING_SERIALIZATION_VERSION,
            serializer_fingerprint=self.embedding_serializer.serializer_fingerprint,
        )

    def embedding_snapshot_matches(self, snapshot: SolrIndexSnapshot) -> bool:
        """Return whether persisted embedding identity matches the active configuration."""
        expected = self._embedding_snapshot_identity()
        if expected is None:
            return all(
                value is None
                for value in (
                    snapshot.embedding_provider,
                    snapshot.embedding_model,
                    snapshot.vector_dimensions,
                    snapshot.embedding_serializer_mode,
                    snapshot.embedding_serializer_version,
                    snapshot.embedding_serializer_fingerprint,
                )
            )
        return (
            snapshot.embedding_provider == expected.provider
            and snapshot.embedding_model == expected.model
            and snapshot.vector_dimensions == expected.dimensions
            and snapshot.embedding_serializer_mode == expected.serializer_mode
            and snapshot.embedding_serializer_version == expected.serializer_version
            and snapshot.embedding_serializer_fingerprint == expected.serializer_fingerprint
        )

    @staticmethod
    def _ensure_complete(result: RepositoryIndexResult) -> None:
        if (
            not result.changes.removal_detection_complete
            or not result.telemetry.scan_traversal_complete
        ):
            raise KnowledgeSearchError(
                "repository observation is incomplete; active Solr snapshot was not changed",
                category="unsafe_snapshot",
                metadata={
                    "removal_detection_complete": (result.changes.removal_detection_complete),
                    "scan_traversal_complete": result.telemetry.scan_traversal_complete,
                    "blockers": list(result.telemetry.removal_detection_blockers),
                },
            )

    def _select(self, params: Mapping[str, object]) -> _SelectResult:
        payload = self.transport.get("select", {**params, "wt": "json"})
        response = payload.get("response")
        if not isinstance(response, dict):
            raise KnowledgeSearchError(
                "Solr select response is missing response metadata",
                category="response_parsing_failure",
            )
        raw_docs = response.get("docs", [])
        raw_num_found = response.get("numFound", 0)
        if not isinstance(raw_docs, list):
            raise KnowledgeSearchError(
                "Solr select documents have an invalid shape",
                category="response_parsing_failure",
            )
        docs: list[dict[str, object]] = []
        for item in raw_docs:
            if not isinstance(item, dict):
                raise KnowledgeSearchError(
                    "Solr select returned a non-object document",
                    category="response_parsing_failure",
                )
            docs.append(cast(dict[str, object], item))
        try:
            num_found = int(raw_num_found)
        except (TypeError, ValueError) as exc:
            raise KnowledgeSearchError(
                "Solr select numFound is invalid",
                category="response_parsing_failure",
            ) from exc
        return _SelectResult(docs=docs, num_found=num_found)


class SolrKnowledgeIndex:
    """Coordinates RepositoryIndexer with persisted Solr previous state."""

    def __init__(self, store: SolrIndexStore, indexer: RepositoryIndexer) -> None:
        """Initialize SolrKnowledgeIndex with its explicit dependencies."""
        self.store = store
        self.indexer = indexer

    def sync(self, workspace: Path) -> SolrIndexSyncResult:
        """Synchronize the repository projection with canonical source state."""
        repository = self.indexer.resolve_repository_identity(workspace)
        try:
            snapshot = self.store.load_snapshot(repository)
            previous = self.store.load_documents(snapshot.repository)
        except KnowledgeSearchError as exc:
            if exc.category not in {
                "snapshot_missing",
                "schema_mismatch",
                "response_parsing_failure",
            }:
                raise
            result = self.indexer.index(workspace)
            return self.store.full_rebuild(result)
        result = self.indexer.index(workspace, previous_documents=previous)
        if result.repository != snapshot.repository:
            raise KnowledgeSearchError(
                "repository identity differs from the active Solr snapshot",
                category="snapshot_revision_mismatch",
            )
        if not self.store.embedding_snapshot_matches(snapshot):
            return self.store.full_rebuild(result)
        return self.store.incremental_sync(result)

    def rebuild(self, workspace: Path) -> SolrIndexSyncResult:
        """Rebuild the repository projection from canonical source state."""
        return self.store.full_rebuild(self.indexer.index(workspace))


def solr_embedding_provider(retrieval: RetrievalConfig) -> EmbeddingProvider:
    """Resolve the neutral embedding identity used by Solr dense retrieval."""

    embedding = retrieval.dense.embedding
    if embedding.provider != "ollama":
        raise KnowledgeSearchError(
            "configured embedding provider is not supported by Solr dense retrieval",
            category="unsupported_capability",
            metadata={
                "backend": "solr",
                "capability": f"embedding_provider:{embedding.provider}",
            },
        )
    return OllamaEmbeddingProvider(
        OllamaEmbeddingSettings(
            url=os.environ.get("OLLAMA_URL", "http://localhost:11434"),
            model=embedding.model,
        ),
        timeout_seconds=embedding.timeout_seconds,
        request_batch_size=embedding.request_batch_size,
    )


class SolrBackend:
    """BM25 + structured-fields implementation of KnowledgeSearchBackend."""

    backend_id = "solr"
    retrieval_capabilities = RetrievalBackendCapabilities(
        lexical=True,
        dense=True,
        hybrid=True,
    )

    def __init__(
        self,
        *,
        transport_factory: Callable[[SolrSettings], SolrTransport] = UrllibSolrTransport,
        embedding_factory: Callable[[RetrievalConfig], EmbeddingProvider] = solr_embedding_provider,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize SolrBackend with its explicit dependencies."""
        self.transport_factory = transport_factory
        self.embedding_factory = embedding_factory
        self.timer = timer

    def search(
        self,
        intent: SearchIntent,
        scope: KnowledgeSearchScope,
        requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        """Execute retrieval for the supplied intent, scope, and requirements."""
        started = self.timer()
        if scope.max_results < 1:
            raise KnowledgeSearchError(
                "Solr candidate budget must be positive", category="schema_mismatch"
            )
        settings = SolrSettings.from_mapping(scope.settings)
        transport = self.transport_factory(settings)
        store = SolrIndexStore(transport, settings)
        repository = scope.repository or RepositoryIndexer().resolve_repository_identity(
            scope.workspace
        )
        snapshot = store.load_snapshot(repository)
        if scope.revision is not None and scope.revision != snapshot.revision:
            raise KnowledgeSearchError(
                "requested revision does not match the active Solr snapshot",
                category="snapshot_revision_mismatch",
                metadata={
                    "requested_revision": scope.revision,
                    "snapshot_revision": snapshot.revision,
                    "repository": snapshot.repository,
                },
            )
        if snapshot.document_count == 0:
            raise KnowledgeSearchError(
                "active Solr snapshot contains no knowledge documents",
                category="empty_index",
                metadata={"repository": snapshot.repository},
            )
        retrieval = scope.retrieval
        if retrieval is not None and retrieval.mode in {"dense", "hybrid"}:
            return _dense_or_hybrid_search(
                backend=self,
                started=started,
                intent=intent,
                scope=scope,
                requirements=requirements,
                settings=settings,
                transport=transport,
                snapshot=snapshot,
                retrieval=retrieval,
            )
        params = _search_params(intent, scope, snapshot, settings)
        payload = transport.get("select", params)
        docs, num_found = _search_documents(payload)
        if scope.repository_paths:
            docs = _repository_source_documents(docs, scope.repository_paths)
        max_raw_score = max((_float_value(item.get("score")) for item in docs), default=0.0)
        matches = tuple(_matches(intent, item, settings) for item in docs)
        provider_scores = _provider_relevance_scores(docs, matches, max_raw_score)
        results: list[SearchResult] = []
        output_truncated = num_found > min(len(docs), scope.max_results)
        for pre_cutoff_rank, (item, match, normalized) in enumerate(
            zip(
                docs[: scope.max_results],
                matches[: scope.max_results],
                provider_scores[: scope.max_results],
                strict=True,
            ),
            start=1,
        ):
            raw_score = _float_value(item.get("score"))
            knowledge_type = str(item.get("knowledge_type", "unknown"))
            capabilities = _capabilities(knowledge_type, match)
            source_ref = str(item.get("source_ref", ""))
            observations = _solr_structural_observations(
                item,
                source_ref=source_ref,
                revision=snapshot.revision,
            )
            relationships = _solr_relationships(item)
            query_matches = _solr_query_matches(
                match,
                source_ref=source_ref,
            )
            results.append(
                SearchResult(
                    id=str(item["id"]),
                    source=str(item.get("path", item.get("source_ref", ""))),
                    source_ref=source_ref,
                    knowledge_type=knowledge_type,
                    content=str(item.get("content", "")),
                    score=normalized,
                    query_alignment_score=match.alignment,
                    capabilities=capabilities,
                    capability_sources={
                        capability: "provider_classification" for capability in capabilities
                    },
                    matched_terms=match.matched_terms,
                    matched_signals=match.matched_signals,
                    reasons=tuple(f"solr_match:{channel}" for channel in match.channels),
                    line_start=_optional_int(item.get("line_start")),
                    line_end=_optional_int(item.get("line_end")),
                    revision=snapshot.revision,
                    checksum=_optional_str(item.get("checksum")),
                    source_type=str(item.get("source_type", "repository")),
                    observations=observations,
                    relationships=relationships,
                    query_matches=query_matches,
                    metadata={
                        "raw_solr_score": raw_score,
                        "document_revision": item.get("document_revision"),
                        "snapshot_revision": snapshot.revision,
                        "snapshot_corpus_checksum": snapshot.corpus_checksum,
                        "projection_version": snapshot.projection_version,
                        "chunk_ref": item.get("chunk_ref"),
                        "branch": item.get("branch"),
                        "path": item.get("path"),
                        "filename": item.get("filename"),
                        "technical_role": item.get("technical_role"),
                        "symbols": item.get("symbols", []),
                        "source_checksum": item.get("source_checksum"),
                        "matched_channels": list(match.channels),
                        "matched_channel_values": {
                            key: list(value) for key, value in match.channel_values.items()
                        },
                        "matched_channel_boosts": dict(match.channel_boosts),
                        "matched_query_signal_channels": list(match.signal_matches),
                        "matched_relation_edges": list(match.relation_edges),
                        "diagnostics_truncated": match.diagnostics_truncated,
                        "pre_cutoff_rank": pre_cutoff_rank,
                        "returned": True,
                        "output_limit": scope.max_results,
                        "output_truncated": output_truncated,
                        "provider_relevance_score": normalized,
                        "matched_channels_by_signal_type": {
                            key: list(value)
                            for key, value in match.channels_by_signal_type.items()
                        },
                        "implementation_evidence": list(match.implementation_evidence),
                        "query_alignment": {
                            "score": match.alignment,
                            "structural": match.structural_alignment,
                            "auxiliary": match.auxiliary_alignment,
                            "semantic": match.semantic_alignment,
                            "hint": match.hint_alignment,
                            "protected_semantic": match.protected_semantic_alignment,
                            "protected_identity": match.protected_identity_alignment,
                            "semantic_reuse": match.semantic_reuse_alignment,
                            "structural_candidate_confidence": (
                                match.structural_candidate_confidence
                            ),
                        },
                    },
                )
            )
        duration_ms = max(0, round((self.timer() - started) * 1_000))
        return KnowledgeSearchResponse(
            backend=self.backend_id,
            status="found" if results else "not_found",
            results=tuple(results),
            duration_ms=duration_ms,
            metadata={
                "backend": self.backend_id,
                "repository": snapshot.repository,
                "snapshot_revision": snapshot.revision,
                "snapshot_status": snapshot.status,
                "projection_version": snapshot.projection_version,
                "query_type_counts": _query_type_counts(intent),
                "query_signals": [item.to_mapping() for item in intent.signals],
                "typed_query_mapping": _typed_query_mapping_telemetry(intent, settings),
                "solr_query": _safe_query_telemetry(params),
                "candidates_found": num_found,
                "candidates_considered": len(docs),
                "candidates_returned": len(results),
                "output_limit": scope.max_results,
                "output_truncated": output_truncated,
                "raw_score_distribution": _score_distribution(
                    [_float_value(item.get("score")) for item in docs]
                ),
                "normalized_score_distribution": _score_distribution(
                    [item.score for item in results]
                ),
                "matched_channels": _distribution(
                    channel
                    for result in results
                    for channel in cast(list[str], result.metadata["matched_channels"])
                ),
                "knowledge_type_distribution": _distribution(
                    item.knowledge_type for item in results
                ),
                "technical_role_distribution": {
                    "observed": _distribution(
                        _technical_role_value(item.get("technical_role")) for item in docs
                    ),
                    "returned": _distribution(
                        _technical_role_value(item.metadata.get("technical_role"))
                        for item in results
                    ),
                },
                "search_requirements": [item.to_mapping() for item in requirements],
                "requirement_allocation": "disabled_v1",
                **_diagnostic_metadata(
                    docs=docs,
                    matches=matches,
                    provider_scores=provider_scores,
                    output_limit=scope.max_results,
                    max_raw_score=max_raw_score,
                    settings=settings,
                    num_found=num_found,
                ),
            },
        )



_RRF_K = DEFAULT_RRF_K


@dataclass(frozen=True)
class _SolrLexicalCandidate:
    rank: int
    item: dict[str, object]
    match: ProjectionV3MatchResult
    raw_score: float
    score: float


@dataclass(frozen=True)
class _SolrDenseCandidate:
    rank: int
    item: dict[str, object]
    raw_score: float
    score: float


def _dense_or_hybrid_search(
    *,
    backend: SolrBackend,
    started: float,
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    requirements: tuple[SearchRequirement, ...],
    settings: SolrSettings,
    transport: SolrTransport,
    snapshot: SolrIndexSnapshot,
    retrieval: RetrievalConfig,
) -> KnowledgeSearchResponse:
    embedder, serializer, vector, query_text = _prepare_dense_query(
        backend=backend,
        intent=intent,
        snapshot=snapshot,
        retrieval=retrieval,
    )
    dense_candidates, dense_num_found, dense_params = _solr_dense_candidates(
        transport=transport,
        scope=scope,
        snapshot=snapshot,
        settings=settings,
        vector=vector,
    )
    if retrieval.mode == "dense":
        results = tuple(
            _dense_search_result(
                candidate,
                snapshot=snapshot,
                scope=scope,
            )
            for candidate in dense_candidates[: scope.max_results]
        )
        output_truncated = len(dense_candidates) > scope.max_results
        return KnowledgeSearchResponse(
            backend=backend.backend_id,
            status="found" if results else "not_found",
            results=results,
            duration_ms=max(0, round((backend.timer() - started) * 1_000)),
            metadata={
                "backend": backend.backend_id,
                "repository": snapshot.repository,
                "snapshot_revision": snapshot.revision,
                "snapshot_status": snapshot.status,
                "projection_version": snapshot.projection_version,
                "projection_fingerprint": snapshot.projection_fingerprint,
                "embedding_provider": embedder.provider_id,
                "embedding_model": embedder.model_id,
                "vector_dimensions": len(vector),
                "embedding_serializer_version": VECTOR_EMBEDDING_SERIALIZATION_VERSION,
                "embedding_serializer_mode": serializer.mode.value,
                "embedding_serializer_fingerprint": serializer.serializer_fingerprint,
                "retrieval_mode": "dense",
                "vector_only": True,
                "lexical_fallback": False,
                "hybrid_fusion": False,
                "reranker": False,
                "solr_dense_query": _safe_query_telemetry(dense_params),
                "query_embedding_text_sha256": hashlib.sha256(
                    query_text.encode("utf-8")
                ).hexdigest(),
                "candidates_found": dense_num_found,
                "candidates_considered": len(dense_candidates),
                "candidates_returned": len(results),
                "output_limit": scope.max_results,
                "output_truncated": output_truncated,
                "normalized_score_distribution": _score_distribution(
                    [item.score for item in results]
                ),
                "candidate_order_before_cutoff": [
                    {
                        "id": candidate.item.get("id"),
                        "source": candidate.item.get("path"),
                        "pre_cutoff_rank": candidate.rank,
                        "dense_rank": candidate.rank,
                        "raw_score": candidate.raw_score,
                        "score": candidate.score,
                        "returned": candidate.rank <= scope.max_results,
                    }
                    for candidate in dense_candidates
                ],
                "search_requirements": [item.to_mapping() for item in requirements],
                "requirement_allocation": "disabled_v1",
            },
        )

    lexical_candidates, lexical_num_found, lexical_params = _solr_lexical_candidates(
        transport=transport,
        intent=intent,
        scope=scope,
        snapshot=snapshot,
        settings=settings,
    )
    return _hybrid_response(
        backend=backend,
        started=started,
        intent=intent,
        scope=scope,
        requirements=requirements,
        snapshot=snapshot,
        embedder=embedder,
        serializer=serializer,
        query_text=query_text,
        lexical_candidates=lexical_candidates,
        dense_candidates=dense_candidates,
        lexical_num_found=lexical_num_found,
        dense_num_found=dense_num_found,
        lexical_params=lexical_params,
        dense_params=dense_params,
    )


def _prepare_dense_query(
    *,
    backend: SolrBackend,
    intent: SearchIntent,
    snapshot: SolrIndexSnapshot,
    retrieval: RetrievalConfig,
) -> tuple[
    EmbeddingProvider,
    ProjectionV3EmbeddingSerializer,
    tuple[float, ...],
    str,
]:
    embedder = backend.embedding_factory(retrieval)
    requested = retrieval.dense.embedding
    if embedder.provider_id != requested.provider:
        raise KnowledgeSearchError(
            "configured embedding provider is not supported by Solr dense retrieval",
            category="unsupported_capability",
            metadata={
                "backend": backend.backend_id,
                "capability": f"embedding_provider:{requested.provider}",
            },
        )
    if embedder.model_id != requested.model:
        raise KnowledgeSearchError(
            "Solr embedding factory returned a different model than requested",
            category="schema_mismatch",
            metadata={
                "requested_embedding_model": requested.model,
                "actual_embedding_model": embedder.model_id,
            },
        )
    serializer = ProjectionV3EmbeddingSerializer(
        snapshot.projection_version,
        ProjectionEmbeddingMode.PROJECTION_V3,
    )
    _validate_dense_snapshot_identity(
        snapshot=snapshot,
        embedder=embedder,
        serializer=serializer,
    )
    query_text = serializer.serialize_query(intent)
    vectors = embedder.embed((query_text,))
    dimensions = validate_vector_dimensions(vectors)
    if dimensions != snapshot.vector_dimensions or dimensions != SOLR_VECTOR_DIMENSIONS:
        raise KnowledgeSearchError(
            "query embedding dimensions do not match the active Solr vector snapshot",
            category="schema_mismatch",
            metadata={
                "expected_dimensions": snapshot.vector_dimensions,
                "schema_dimensions": SOLR_VECTOR_DIMENSIONS,
                "actual_dimensions": dimensions,
                "rebuild_required": True,
            },
        )
    return embedder, serializer, vectors[0], query_text


def _validate_dense_snapshot_identity(
    *,
    snapshot: SolrIndexSnapshot,
    embedder: EmbeddingProvider,
    serializer: ProjectionV3EmbeddingSerializer,
) -> None:
    expected: dict[str, object] = {
        "embedding_provider": embedder.provider_id,
        "embedding_model": embedder.model_id,
        "vector_dimensions": SOLR_VECTOR_DIMENSIONS,
        "embedding_serializer_mode": serializer.mode.value,
        "embedding_serializer_version": VECTOR_EMBEDDING_SERIALIZATION_VERSION,
        "embedding_serializer_fingerprint": serializer.serializer_fingerprint,
    }
    actual: dict[str, object] = {
        "embedding_provider": snapshot.embedding_provider,
        "embedding_model": snapshot.embedding_model,
        "vector_dimensions": snapshot.vector_dimensions,
        "embedding_serializer_mode": snapshot.embedding_serializer_mode,
        "embedding_serializer_version": snapshot.embedding_serializer_version,
        "embedding_serializer_fingerprint": snapshot.embedding_serializer_fingerprint,
    }
    mismatches = {
        key: {"expected": value, "actual": actual[key]}
        for key, value in expected.items()
        if actual[key] != value
    }
    if mismatches:
        raise KnowledgeSearchError(
            "active Solr vector snapshot does not match the requested embedding identity",
            category="index_stale",
            metadata={
                "rebuild_required": True,
                "embedding_identity_mismatches": mismatches,
            },
        )


def _solr_dense_candidates(
    *,
    transport: SolrTransport,
    scope: KnowledgeSearchScope,
    snapshot: SolrIndexSnapshot,
    settings: SolrSettings,
    vector: tuple[float, ...],
) -> tuple[tuple[_SolrDenseCandidate, ...], int, dict[str, object]]:
    query_limit = max(
        scope.max_results,
        settings.diagnostic_candidate_window or scope.max_results,
    )
    filters = [
        "record_kind:knowledge",
        _term_filter("repository", snapshot.repository),
    ]
    if scope.repository_paths:
        filters.append(
            "path:("
            + " OR ".join(_quote_query(item) for item in scope.repository_paths)
            + ")"
        )
    if scope.knowledge_types:
        filters.append(
            "knowledge_type:("
            + " OR ".join(_quote_query(item) for item in scope.knowledge_types)
            + ")"
        )
    for field_name in ("language", "source_type"):
        values = _configured_filter_values(scope.settings.get(field_name), field_name)
        if values:
            filters.append(
                f"{field_name}:("
                + " OR ".join(_quote_query(item) for item in values)
                + ")"
            )
    params: dict[str, object] = {
        "query": (
            f"{{!knn f={SOLR_VECTOR_FIELD} topK={query_limit}}}"
            + json.dumps(list(vector), separators=(",", ":"))
        ),
        "filter": filters,
        "limit": query_limit,
        "fields": _solr_result_fields(),
        "sort": "score desc,id asc",
    }
    payload = transport.post("select", params, {"wt": "json"})
    docs, num_found = _search_documents(payload)
    if scope.repository_paths:
        docs = _repository_source_documents(docs, scope.repository_paths)
    candidates = tuple(
        _SolrDenseCandidate(
            rank=rank,
            item=item,
            raw_score=_float_value(item.get("score")),
            score=_clamp(_float_value(item.get("score"))),
        )
        for rank, item in enumerate(docs, start=1)
    )
    return candidates, num_found, params


def _solr_lexical_candidates(
    *,
    transport: SolrTransport,
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    snapshot: SolrIndexSnapshot,
    settings: SolrSettings,
) -> tuple[tuple[_SolrLexicalCandidate, ...], int, dict[str, object]]:
    params = _search_params(intent, scope, snapshot, settings)
    payload = transport.get("select", params)
    docs, num_found = _search_documents(payload)
    if scope.repository_paths:
        docs = _repository_source_documents(docs, scope.repository_paths)
    max_raw_score = max((_float_value(item.get("score")) for item in docs), default=0.0)
    matches = tuple(_matches(intent, item, settings) for item in docs)
    scores = _provider_relevance_scores(docs, matches, max_raw_score)
    candidates = tuple(
        _SolrLexicalCandidate(
            rank=rank,
            item=item,
            match=match,
            raw_score=_float_value(item.get("score")),
            score=score,
        )
        for rank, (item, match, score) in enumerate(
            zip(docs, matches, scores, strict=True),
            start=1,
        )
    )
    return candidates, num_found, params


def _dense_search_result(
    candidate: _SolrDenseCandidate,
    *,
    snapshot: SolrIndexSnapshot,
    scope: KnowledgeSearchScope,
) -> SearchResult:
    item = candidate.item
    source_ref = str(item.get("source_ref", ""))
    knowledge_type = str(item.get("knowledge_type", "unknown"))
    capabilities = _projected_dense_capabilities(item)
    return SearchResult(
        id=str(item["id"]),
        source=str(item.get("path", source_ref)),
        source_ref=source_ref,
        knowledge_type=knowledge_type,
        content=str(item.get("content", "")),
        score=candidate.score,
        query_alignment_score=candidate.score,
        capabilities=capabilities,
        capability_sources={
            capability: "provider_classification" for capability in capabilities
        },
        matched_terms=(),
        matched_signals=(),
        reasons=("solr_dense_vector_similarity",),
        line_start=_optional_int(item.get("line_start")),
        line_end=_optional_int(item.get("line_end")),
        revision=snapshot.revision,
        checksum=_optional_str(item.get("checksum")),
        source_type=str(item.get("source_type", "repository")),
        observations=_solr_structural_observations(
            item,
            source_ref=source_ref,
            revision=snapshot.revision,
        ),
        relationships=_solr_relationships(item),
        query_matches=(),
        metadata={
            "raw_solr_vector_score": candidate.raw_score,
            "provider_relevance_score": candidate.score,
            "pre_cutoff_rank": candidate.rank,
            "dense_rank": candidate.rank,
            "document_revision": item.get("document_revision"),
            "snapshot_revision": snapshot.revision,
            "snapshot_corpus_checksum": snapshot.corpus_checksum,
            "projection_version": snapshot.projection_version,
            "projection_fingerprint": snapshot.projection_fingerprint,
            "embedding_provider": snapshot.embedding_provider,
            "embedding_model": snapshot.embedding_model,
            "vector_dimensions": snapshot.vector_dimensions,
            "embedding_serializer_mode": snapshot.embedding_serializer_mode,
            "embedding_serializer_version": snapshot.embedding_serializer_version,
            "embedding_serializer_fingerprint": snapshot.embedding_serializer_fingerprint,
            "chunk_ref": item.get("chunk_ref"),
            "branch": item.get("branch"),
            "path": item.get("path"),
            "filename": item.get("filename"),
            "technical_role": item.get("technical_role"),
            "symbols": item.get("symbols", []),
            "source_checksum": item.get("source_checksum"),
            "matched_channels": [],
            "matched_query_signal_channels": [],
            "implementation_evidence": [],
            "returned": candidate.rank <= scope.max_results,
            "output_limit": scope.max_results,
            "vector_only": True,
            "lexical_fallback": False,
            "hybrid_fusion": False,
            "reranker": False,
        },
    )


def _hybrid_response(
    *,
    backend: SolrBackend,
    started: float,
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    requirements: tuple[SearchRequirement, ...],
    snapshot: SolrIndexSnapshot,
    embedder: EmbeddingProvider,
    serializer: ProjectionV3EmbeddingSerializer,
    query_text: str,
    lexical_candidates: tuple[_SolrLexicalCandidate, ...],
    dense_candidates: tuple[_SolrDenseCandidate, ...],
    lexical_num_found: int,
    dense_num_found: int,
    lexical_params: Mapping[str, object],
    dense_params: Mapping[str, object],
) -> KnowledgeSearchResponse:
    lexical_fusion_candidates = lexical_candidates[: scope.max_results]
    dense_fusion_candidates = dense_candidates[: scope.max_results]
    lexical_by_id = {
        str(item.item["id"]): item for item in lexical_fusion_candidates
    }
    dense_by_id = {
        str(item.item["id"]): item for item in dense_fusion_candidates
    }
    fusion_candidates: list[FusionCandidate] = []
    for candidate_id in sorted(set(lexical_by_id) | set(dense_by_id)):
        lexical = lexical_by_id.get(candidate_id)
        dense = dense_by_id.get(candidate_id)
        source_item = (
            lexical.item
            if lexical is not None
            else cast(_SolrDenseCandidate, dense).item
        )
        fusion_candidates.append(
            FusionCandidate(
                candidate_id=candidate_id,
                source=str(source_item.get("path", source_item.get("source_ref", ""))),
                lexical=(
                    None
                    if lexical is None
                    else FusionChannelSignal(
                        rank=lexical.rank,
                        normalized_score=lexical.score,
                        raw_score=lexical.raw_score,
                    )
                ),
                dense=(
                    None
                    if dense is None
                    else FusionChannelSignal(
                        rank=dense.rank,
                        normalized_score=dense.score,
                        raw_score=dense.raw_score,
                    )
                ),
            )
        )
    fusion_input = FusionInput(
        candidates=tuple(fusion_candidates),
        max_results=scope.max_results,
    )
    fusion_result = FusionKernel().fuse(
        FusionRequest(
            input=fusion_input,
            strategy="rrf",
            parameters={"k": _RRF_K},
        )
    )
    results: list[SearchResult] = []
    candidate_order: list[dict[str, object]] = []
    for fused_candidate in fusion_result.candidates:
        fused_rank = fused_candidate.fused_rank
        candidate_id = fused_candidate.candidate_id
        rrf_score = fused_candidate.fused_score
        lexical_contribution = fused_candidate.lexical_contribution
        dense_contribution = fused_candidate.dense_contribution
        lexical = lexical_by_id.get(candidate_id)
        dense = dense_by_id.get(candidate_id)
        item = lexical.item if lexical is not None else cast(_SolrDenseCandidate, dense).item
        source_ref = str(item.get("source_ref", ""))
        knowledge_type = str(item.get("knowledge_type", "unknown"))
        dense_capabilities = _projected_dense_capabilities(item)
        lexical_capabilities = (
            ()
            if lexical is None
            else _capabilities(knowledge_type, lexical.match)
        )
        capabilities = tuple(dict.fromkeys((*lexical_capabilities, *dense_capabilities)))
        normalized_rrf = fused_candidate.normalized_score
        matched_terms = () if lexical is None else lexical.match.matched_terms
        matched_signals = () if lexical is None else lexical.match.matched_signals
        query_matches = (
            ()
            if lexical is None
            else _solr_query_matches(lexical.match, source_ref=source_ref)
        )
        observations = _solr_structural_observations(
            item,
            source_ref=source_ref,
            revision=snapshot.revision,
        )
        reasons = ["solr_hybrid_rrf"]
        if lexical is not None:
            reasons.extend(f"solr_match:{channel}" for channel in lexical.match.channels)
        if dense is not None:
            reasons.append("solr_dense_vector_similarity")
        candidate_order.append(
            {
                "id": candidate_id,
                "source": item.get("path"),
                "fused_rank": fused_rank,
                "lexical_rank": None if lexical is None else lexical.rank,
                "dense_rank": None if dense is None else dense.rank,
                "lexical_normalized_score": fused_candidate.lexical_normalized_score,
                "dense_normalized_score": fused_candidate.dense_normalized_score,
                "rrf_score": rrf_score,
                "rrf_lexical_contribution": lexical_contribution,
                "rrf_dense_contribution": dense_contribution,
                "returned": fused_rank <= scope.max_results,
            }
        )
        if fused_rank > scope.max_results:
            continue
        results.append(
            SearchResult(
                id=candidate_id,
                source=str(item.get("path", source_ref)),
                source_ref=source_ref,
                knowledge_type=knowledge_type,
                content=str(item.get("content", "")),
                score=normalized_rrf,
                query_alignment_score=(
                    lexical.match.alignment
                    if lexical is not None
                    else cast(_SolrDenseCandidate, dense).score
                ),
                capabilities=capabilities,
                capability_sources={
                    capability: "provider_classification"
                    for capability in capabilities
                },
                matched_terms=matched_terms,
                matched_signals=matched_signals,
                reasons=tuple(reasons),
                line_start=_optional_int(item.get("line_start")),
                line_end=_optional_int(item.get("line_end")),
                revision=snapshot.revision,
                checksum=_optional_str(item.get("checksum")),
                source_type=str(item.get("source_type", "repository")),
                observations=observations,
                relationships=_solr_relationships(item),
                query_matches=query_matches,
                metadata={
                    "provider_relevance_score": normalized_rrf,
                    "fused_rank": fused_rank,
                    "lexical_rank": None if lexical is None else lexical.rank,
                    "dense_rank": None if dense is None else dense.rank,
                    "raw_solr_score": None if lexical is None else lexical.raw_score,
                    "raw_solr_vector_score": None if dense is None else dense.raw_score,
                    "rrf_score": rrf_score,
                    "rrf_k": _RRF_K,
                    "rrf_lexical_contribution": lexical_contribution,
                    "rrf_dense_contribution": dense_contribution,
                    "document_revision": item.get("document_revision"),
                    "snapshot_revision": snapshot.revision,
                    "snapshot_corpus_checksum": snapshot.corpus_checksum,
                    "projection_version": snapshot.projection_version,
                    "projection_fingerprint": snapshot.projection_fingerprint,
                    "embedding_provider": snapshot.embedding_provider,
                    "embedding_model": snapshot.embedding_model,
                    "vector_dimensions": snapshot.vector_dimensions,
                    "embedding_serializer_mode": snapshot.embedding_serializer_mode,
                    "embedding_serializer_version": snapshot.embedding_serializer_version,
                    "embedding_serializer_fingerprint": (
                        snapshot.embedding_serializer_fingerprint
                    ),
                    "chunk_ref": item.get("chunk_ref"),
                    "branch": item.get("branch"),
                    "path": item.get("path"),
                    "filename": item.get("filename"),
                    "technical_role": item.get("technical_role"),
                    "symbols": item.get("symbols", []),
                    "source_checksum": item.get("source_checksum"),
                    "matched_channels": (
                        [] if lexical is None else list(lexical.match.channels)
                    ),
                    "matched_query_signal_channels": (
                        [] if lexical is None else list(lexical.match.signal_matches)
                    ),
                    "implementation_evidence": (
                        []
                        if lexical is None
                        else list(lexical.match.implementation_evidence)
                    ),
                    "returned": True,
                    "output_limit": scope.max_results,
                    "vector_only": False,
                    "lexical_fallback": False,
                    "hybrid_fusion": True,
                    "reranker": False,
                },
            )
        )
    return KnowledgeSearchResponse(
        backend=backend.backend_id,
        status="found" if results else "not_found",
        results=tuple(results),
        duration_ms=max(0, round((backend.timer() - started) * 1_000)),
        metadata={
            "backend": backend.backend_id,
            "repository": snapshot.repository,
            "snapshot_revision": snapshot.revision,
            "snapshot_status": snapshot.status,
            "projection_version": snapshot.projection_version,
            "projection_fingerprint": snapshot.projection_fingerprint,
            "embedding_provider": embedder.provider_id,
            "embedding_model": embedder.model_id,
            "vector_dimensions": snapshot.vector_dimensions,
            "embedding_serializer_version": VECTOR_EMBEDDING_SERIALIZATION_VERSION,
            "embedding_serializer_mode": serializer.mode.value,
            "embedding_serializer_fingerprint": serializer.serializer_fingerprint,
            "retrieval_mode": "hybrid",
            "vector_only": False,
            "lexical_fallback": False,
            "hybrid_fusion": True,
            "fusion_strategy": "rrf",
            "rrf_k": _RRF_K,
            "fusion_parameters": dict(fusion_result.parameters),
            "fusion_normalization_identity": fusion_result.normalization_identity,
            "fusion_input_fingerprint": fusion_result.input_fingerprint,
            "fusion_output_fingerprint": fusion_result.output_fingerprint,
            "fusion_input": fusion_input.to_mapping(include_raw_scores=True),
            "reranker": False,
            "solr_lexical_query": _safe_query_telemetry(lexical_params),
            "solr_dense_query": _safe_query_telemetry(dense_params),
            "query_embedding_text_sha256": hashlib.sha256(
                query_text.encode("utf-8")
            ).hexdigest(),
            "lexical_candidates_found": lexical_num_found,
            "dense_candidates_found": dense_num_found,
            "lexical_candidates_observed": len(lexical_candidates),
            "dense_candidates_observed": len(dense_candidates),
            "lexical_candidates_considered": len(lexical_fusion_candidates),
            "dense_candidates_considered": len(dense_fusion_candidates),
            "candidates_considered": len(fusion_result.candidates),
            "candidates_returned": len(results),
            "output_limit": scope.max_results,
            "output_truncated": len(fusion_result.candidates) > scope.max_results,
            "candidate_order_before_cutoff": candidate_order,
            "normalized_score_distribution": _score_distribution(
                [item.score for item in results]
            ),
            "search_requirements": [item.to_mapping() for item in requirements],
            "requirement_allocation": "disabled_v1",
        },
    )


def _projected_dense_capabilities(
    document: Mapping[str, object],
) -> tuple[str, ...]:
    knowledge_type = str(document.get("knowledge_type", "unknown"))
    if knowledge_type == "tests":
        return ("tests",)
    if knowledge_type != "code":
        return ()
    symbols = _string_values(document.get("symbols"))
    symbol_types = _string_values(document.get("symbol_types"))
    content = "\n".join(_string_values(document.get("content")))
    executable = bool(symbols) and any(
        item in _EXECUTABLE_SYMBOL_TYPES for item in symbol_types
    )
    if executable or any(
        pattern.search(content)
        for _, pattern in _EXECUTABLE_DECLARATION_PATTERNS
    ):
        return ("implementation",)
    return ()


def _solr_result_fields() -> str:
    return (
        "id,source_type,source_ref,chunk_ref,repository,branch,document_revision,"
        "path,filename,language,knowledge_type,technical_role,symbols,symbol_types,"
        "content,line_start,line_end,checksum,source_checksum,identifiers_exact,"
        "content_identifiers_exact,filename_exact,path_text,path_segments,route_methods,"
        "route_paths,route_mechanisms,import_targets,dependency_targets,relation_types,"
        "relation_targets,relation_tokens,documentation_text,projection_version,score"
    )


def _without_solr_internal_fields(value: Mapping[str, object]) -> dict[str, object]:
    return {str(key): item for key, item in value.items() if key != "_version_"}


def _comparable_projection_record(value: Mapping[str, object]) -> dict[str, object]:
    comparable: dict[str, object] = {}
    for key, item in value.items():
        if key == "_version_" or item is None or item == [] or item == ():
            continue
        if key == SOLR_VECTOR_FIELD and isinstance(item, (list, tuple)):
            comparable[key] = [
                _float32(float(component))
                for component in item
            ]
            continue
        comparable[str(key)] = list(item) if isinstance(item, tuple) else item
    return comparable


def _float32(value: float) -> float:
    return float(struct.unpack("!f", struct.pack("!f", value))[0])


def _projection_records_checksum(records: Iterable[Mapping[str, object]]) -> str:
    normalized = sorted(
        (_without_solr_internal_fields(item) for item in records),
        key=lambda item: str(item.get("id", "")),
    )
    payload = json.dumps(normalized, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()




def _search_params(
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    snapshot: SolrIndexSnapshot,
    settings: SolrSettings,
) -> dict[str, object]:
    typed_entries = _typed_query_entries(intent)
    typed_clauses = [
        _typed_query_clause(signal_type, value, multiplier, settings)
        for signal_type, value, multiplier in typed_entries
    ]
    q = (
        "*:*"
        if scope.repository_paths
        else " OR ".join(item for item in typed_clauses if item) or "*:*"
    )
    filters = ["record_kind:knowledge", _term_filter("repository", snapshot.repository)]
    if scope.repository_paths:
        filters.append(
            "path_text:("
            + " OR ".join(_quote_query(item.casefold()) for item in scope.repository_paths)
            + ")"
        )
    if scope.knowledge_types:
        filters.append(
            "knowledge_type:("
            + " OR ".join(_quote_query(item) for item in scope.knowledge_types)
            + ")"
        )
    for field_name in ("language", "source_type"):
        values = _configured_filter_values(scope.settings.get(field_name), field_name)
        if values:
            filters.append(
                f"{field_name}:(" + " OR ".join(_quote_query(item) for item in values) + ")"
            )
    params: dict[str, object] = {
        "defType": "edismax",
        "q": q,
        "q.op": "OR",
        # Every query term is already fielded by signal type. Keeping qf lexical-only
        # avoids granting structural boosts to semantic or hint values.
        "qf": f'content^{settings.field_boosts["content"]:g}',
        "fq": filters,
        "rows": max(
            scope.max_results,
            settings.diagnostic_candidate_window or scope.max_results,
        ),
        "fl": (
            "id,source_type,source_ref,chunk_ref,repository,branch,document_revision,"
            "path,filename,language,knowledge_type,technical_role,symbols,symbol_types,"
            "content,line_start,line_end,checksum,source_checksum,identifiers_exact,"
            "content_identifiers_exact,filename_exact,path_text,path_segments,route_methods,"
            "route_paths,route_mechanisms,import_targets,dependency_targets,relation_types,"
            "relation_targets,relation_tokens,documentation_text,projection_version,score"
        ),
        "sort": "score desc,id asc",
        "wt": "json",
    }
    return params






def _typed_query_clause(
    signal_type: str,
    value: str,
    multiplier: float,
    settings: SolrSettings,
) -> str:
    field_factors = _SIGNAL_FIELD_FACTORS[signal_type]
    escaped = _escape_query(value.casefold())
    clauses = []
    for field_name in field_factors:
        boost = _query_field_boost(
            signal_type,
            field_name,
            multiplier,
            settings,
        )
        clauses.append(f'{field_name}:"{escaped}"^{boost:g}')
    for field_name in _V3_QUERY_FIELDS_BY_SIGNAL_TYPE[signal_type]:
        boost = _query_field_boost(
            signal_type,
            field_name,
            multiplier,
            settings,
        )
        clauses.append(f'{field_name}:"{escaped}"^{boost:g}')
    return "(" + " OR ".join(clauses) + ")"






def _search_documents(payload: Mapping[str, object]) -> tuple[list[dict[str, object]], int]:
    response = payload.get("response")
    if not isinstance(response, dict):
        raise KnowledgeSearchError(
            "Solr search response is missing response metadata",
            category="response_parsing_failure",
        )
    raw_docs = response.get("docs", [])
    if not isinstance(raw_docs, list):
        raise KnowledgeSearchError(
            "Solr search documents have an invalid shape",
            category="response_parsing_failure",
        )
    docs: list[dict[str, object]] = []
    for item in raw_docs:
        if not isinstance(item, dict):
            raise KnowledgeSearchError(
                "Solr search returned a non-object document",
                category="response_parsing_failure",
            )
        if item.get("record_kind") == "snapshot":
            raise KnowledgeSearchError(
                "Solr search leaked a snapshot control record",
                category="schema_mismatch",
            )
        docs.append(cast(dict[str, object], item))
    try:
        return docs, int(response.get("numFound", len(docs)))
    except (TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            "Solr search numFound is invalid",
            category="response_parsing_failure",
        ) from exc




























def _safe_query_telemetry(params: Mapping[str, object]) -> dict[str, object]:
    q = str(params.get("q", params.get("query", "")))
    filters = params.get("fq", params.get("filter", []))
    if isinstance(filters, str):
        filters = [filters]
    filter_names = [
        _filter_field_name(item) for item in cast(list[str], filters)
    ]
    return {
        "q": "<redacted>",
        "query_sha256": hashlib.sha256(q.encode("utf-8")).hexdigest(),
        "qf": params.get("qf"),
        "filter_fields": filter_names,
        "rows": params.get("rows", params.get("limit")),
        "sort": params.get("sort"),
        "transport": "json-post" if "query" in params else "query-params",
    }

def _filter_field_name(value: str) -> str:
    if value.startswith("{!term f=") and "}" in value:
        return value.removeprefix("{!term f=").split("}", 1)[0]
    return value.split(":", 1)[0]




def _configured_filter_values(value: object, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return _dedupe((value,))
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise KnowledgeSearchError(
                f"Solr {field_name} filter values must be strings",
                category="schema_mismatch",
            )
        return _dedupe(cast(list[str], value))
    raise KnowledgeSearchError(
        f"Solr {field_name} filter must be a string or list",
        category="schema_mismatch",
    )








def _term_filter(field_name: str, value: str) -> str:
    return f"{{!term f={field_name}}}{value}"


def _repository_query(repository: str) -> str:
    return f'repository:"{_escape_query(repository)}"'


def _quote_query(value: str) -> str:
    return f'"{_escape_query(value)}"'


def _escape_query(value: str) -> str:
    return re.sub(r'([+\-!(){}\[\]^"~*?:\\/])', r"\\\1", value)














def _error_category(error: Exception) -> str:
    if isinstance(error, KnowledgeSearchError):
        return error.category
    return type(error).__name__


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))
