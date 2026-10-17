"""OpenSearch persistence and search over the canonical Projection v3 records."""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import contextmanager
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import replace, dataclass, field
from uuid import uuid4
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from fetchcore.retrieval.contracts import Anchor
from fetchcore.retrieval.translation import validate_intent_translation, translation_diagnostics

from fetchcore.adapters.retrieval.opensearch.query import (
    OPENSEARCH_VECTOR_FIELD, _EXACT_FIELDS, _search_body, _search_documents,
)
from fetchcore.retrieval.embedding import (
    VECTOR_EMBEDDING_SERIALIZATION_VERSION,
    EmbeddingProvider,
    ProjectionEmbeddingMode,
    ProjectionV3EmbeddingSerializer,
    validate_vector_dimensions,
)
from fetchcore.retrieval.fusion import (
    DEFAULT_RRF_K,
    FusionCandidate,
    FusionChannelSignal,
    FusionInput,
    FusionKernel,
    FusionRequest,
)
from fetchcore.retrieval import RetrievalBackendCapabilities, RetrievalConfig
from fetchcore.telemetry import (
    IndexSyncTelemetry,
    RetrievalStageTelemetry,
    RetrievalTelemetry,
    elapsed_ms,
    returned_chars,
)
from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
    corpus_checksum,
    projection_contract_fingerprint,
)
from fetchcore.retrieval.projection_matching import (
    DEFAULT_FIELD_BOOSTS,
    DEFAULT_SIGNAL_TYPE_BOOSTS,
    ProjectionV3MatchResult,
    bounded_diagnostic_metadata,
    classify_capabilities,
    distribution,
    float_value,
    match_projection_record,
    normalize_provider_scores,
    optional_int,
    optional_str,
    query_match_evidence,
    query_type_counts,
    relationships,
    repository_source_documents,
    score_distribution,
    structural_candidate_evidence,
    structural_observations,
    technical_role_value,
    typed_query_mapping_telemetry,
    validate_projection_v3_controls,
)
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexer,
    RepositoryIndexResult,
)
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchRequirement,
    SearchResult,
)
from fetchcore.retrieval.contracts import SearchScope as KnowledgeSearchScope


_TEXT_FIELDS = frozenset({"content", "path_text", "documentation_text"})
OPENSEARCH_VECTOR_DIMENSIONS = 768
_RRF_K = DEFAULT_RRF_K


@dataclass(frozen=True)
class OpenSearchSettings:
    """Validated OpenSearch settings with backend-neutral Projection v3 controls."""

    url: str = "http://localhost:9200"
    index: str = "fetchcore-knowledge"
    timeout_seconds: float = 10.0
    projection_version: str = PROJECTION_VERSION
    field_boosts: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_FIELD_BOOSTS)
    )
    signal_type_boosts: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SIGNAL_TYPE_BOOSTS)
    )
    batch_size: int = 200
    diagnostic_candidate_window: int | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> OpenSearchSettings:
        """Build the value from a mapping while validating its public contract."""
        data = dict(value or {})
        url = str(data.get("url", cls.url)).rstrip("/")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise KnowledgeSearchError(
                "OpenSearch URL must be an absolute HTTP(S) URL",
                category="schema_mismatch",
            )
        index = str(data.get("index", data.get("collection", cls.index))).strip()
        if (
            not index
            or index != index.casefold()
            or index[0] in {"_", "-", "+"}
            or any(char in index for char in ' \\/*?"<>|,#:')
        ):
            raise KnowledgeSearchError(
                "OpenSearch index name is invalid",
                category="schema_mismatch",
            )

        try:
            timeout = float(
                cast(float | int | str, data.get("timeout_seconds", cls.timeout_seconds))
            )
            batch_size = int(
                cast(float | int | str, data.get("batch_size", cls.batch_size))
            )
        except (TypeError, ValueError) as exc:
            raise KnowledgeSearchError(
                "OpenSearch timeout_seconds and batch_size must be numeric",
                category="schema_mismatch",
            ) from exc
        if not 0.05 <= timeout <= 300:
            raise KnowledgeSearchError(
                "OpenSearch timeout_seconds must be between 0.05 and 300",
                category="schema_mismatch",
            )
        if not 1 <= batch_size <= 5_000:
            raise KnowledgeSearchError(
                "OpenSearch batch_size must be between 1 and 5000",
                category="schema_mismatch",
            )
        controls = validate_projection_v3_controls(data, backend_name="OpenSearch")
        return cls(
            url=url,
            index=index,
            timeout_seconds=timeout,
            projection_version=controls.projection_version,
            field_boosts=controls.field_boosts,
            signal_type_boosts=controls.signal_type_boosts,
            batch_size=batch_size,
            diagnostic_candidate_window=controls.diagnostic_candidate_window,
        )


class OpenSearchTransport(Protocol):
    """Transport contract used by OpenSearch search and index lifecycle operations."""
    def request(
        self,
        method: str,
        path: str,
        payload: object | bytes | None = None,
        *,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        """Execute a backend request and return the decoded response."""
        ...


class UrllibOpenSearchTransport:
    """Dependency-free REST transport for local or remote OpenSearch."""

    def __init__(self, settings: OpenSearchSettings) -> None:
        """Initialize UrllibOpenSearchTransport with its explicit dependencies."""
        self.settings = settings

    def request(
        self,
        method: str,
        path: str,
        payload: object | bytes | None = None,
        *,
        content_type: str = "application/json",
    ) -> dict[str, object]:
        """Execute a backend request and return the decoded response."""
        url = f"{self.settings.url}/{path.lstrip('/')}"
        if isinstance(payload, bytes):
            body = payload
        elif payload is None:
            body = None
        else:
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = Request(
            url,
            data=body,
            method=method,
            headers={"Accept": "application/json", "Content-Type": content_type},
        )
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            category = (
                "index_missing"
                if exc.code == 404
                else "schema_mismatch"
                if exc.code in {400, 409, 422}
                else "unavailable"
            )
            raise KnowledgeSearchError(
                f"OpenSearch request failed with HTTP {exc.code}",
                category=category,
                metadata={"http_status": exc.code, "operation": path},
            ) from exc
        except TimeoutError as exc:
            raise KnowledgeSearchError(
                "OpenSearch request timed out",
                category="timeout",
                metadata={"operation": path},
            ) from exc
        except URLError as exc:
            raise KnowledgeSearchError(
                "OpenSearch is unavailable",
                category="unavailable",
                metadata={"operation": path},
            ) from exc

        try:
            decoded = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgeSearchError(
                "OpenSearch returned invalid JSON",
                category="response_parsing_failure",
                metadata={"operation": path},
            ) from exc
        if not isinstance(decoded, dict):
            raise KnowledgeSearchError(
                "OpenSearch returned an unexpected response shape",
                category="response_parsing_failure",
                metadata={"operation": path},
            )
        return cast(dict[str, object], decoded)


@dataclass(frozen=True)
class OpenSearchIndexSnapshot:
    """Persisted identity and compatibility metadata for an OpenSearch repository snapshot."""
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
    indexing_fingerprint: str | None = None

    @property
    def id(self) -> str:
        """Return the deterministic persisted snapshot identifier."""
        digest = hashlib.sha256(self.repository.encode("utf-8")).hexdigest()
        return f"snapshot:{digest}"

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
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
            "indexing_fingerprint": self.indexing_fingerprint,
            "projection_version": self.projection_version,
            "snapshot_indexed_at": self.indexed_at,
            "snapshot_status": self.status,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> OpenSearchIndexSnapshot:
        """Build the value from a mapping while validating its public contract."""
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
                indexing_fingerprint=(
                    None if value.get("indexing_fingerprint") is None
                    else str(value["indexing_fingerprint"])
                ),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KnowledgeSearchError(
                "OpenSearch snapshot record is invalid",
                category="response_parsing_failure",
            ) from exc


@dataclass(frozen=True)
class OpenSearchIndexSyncResult:
    """Observable result of an OpenSearch index synchronization."""
    mode: str
    snapshot: OpenSearchIndexSnapshot
    added: int
    updated: int
    deleted: int
    unchanged: int
    duration_ms: int
    telemetry: IndexSyncTelemetry | None = None

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "mode": self.mode,
            "snapshot": self.snapshot.to_mapping(),
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "duration_ms": self.duration_ms,
            **(
                {"telemetry": self.telemetry.to_mapping()}
                if self.telemetry is not None
                else {}
            ),
        }


def _index_mapping(*, include_vectors: bool = False) -> dict[str, object]:
    keyword_fields = {
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
        "checksum",
        "source_checksum",
        "identifiers_exact",
        "content_identifiers_exact",
        "filename_exact",
        "path_segments",
        "route_methods",
        "route_paths",
        "route_mechanisms",
        "import_targets",
        "dependency_targets",
        "relation_types",
        "relation_targets",
        "relation_tokens",
        "structural_tokens",
        "projection_fingerprint",
        "projection_version",
        "snapshot_revision",
        "corpus_checksum",
        "snapshot_indexed_at",
        "snapshot_status",
        "embedding_provider",
        "embedding_model",
        "embedding_serializer_mode",
        "embedding_serializer_version",
        "embedding_serializer_fingerprint",
        "indexing_fingerprint",
    }
    properties: dict[str, object] = {
        name: {"type": "keyword"} for name in sorted(keyword_fields)
    }
    for name in sorted(_EXACT_FIELDS):
        properties[name] = {"type": "keyword", "normalizer": "fetchcore_identifier_ci"}
    for name in sorted(_TEXT_FIELDS):
        properties[name] = {"type": "text"}
    properties["line_start"] = {"type": "integer"}
    properties["line_end"] = {"type": "integer"}
    properties["document_count"] = {"type": "integer"}
    properties["vector_dimensions"] = {"type": "integer"}
    properties["canonical_payload"] = {"type": "text", "index": False}
    settings: dict[str, object] = {
        "analysis": {
            "normalizer": {
                "fetchcore_identifier_ci": {
                    "type": "custom",
                    "filter": ["lowercase"],
                }
            }
        }
    }
    if include_vectors:
        settings["index"] = {"knn": True}
        properties[OPENSEARCH_VECTOR_FIELD] = {
            "type": "knn_vector",
            "dimension": OPENSEARCH_VECTOR_DIMENSIONS,
            "method": {
                "name": "hnsw",
                "space_type": "cosinesimil",
                "engine": "lucene",
            },
        }
    return {
        "settings": settings,
        "mappings": {"dynamic": "strict", "properties": properties},
    }


def _repository_alias(settings: OpenSearchSettings, repository: str) -> str:
    digest = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:12]
    return f"{settings.index}-{digest}"


def _concrete_index(
    settings: OpenSearchSettings,
    result: RepositoryIndexResult,
    build_ref: str,
) -> str:
    revision = hashlib.sha256(result.revision.encode("utf-8")).hexdigest()[:12]
    checksum = corpus_checksum(result.documents)[:12]
    build = hashlib.sha256(build_ref.encode("utf-8")).hexdigest()[:8]
    return (
        f"{_repository_alias(settings, result.repository)}-"
        f"{revision}-{checksum}-{build}"
    )


class OpenSearchIndexStore:
    """Blue/green repository projection store using atomic OpenSearch aliases."""

    def __init__(
        self,
        transport: OpenSearchTransport,
        settings: OpenSearchSettings,
        *,
        clock: Callable[[], str],
        timer: Callable[[], float] = time.monotonic,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        """Initialize OpenSearchIndexStore with its explicit dependencies."""
        self.transport = transport
        self.settings = settings
        self.compiler = ProjectionV3Compiler(settings.projection_version)
        self.reader = ProjectionV3CanonicalDocumentReader(settings.projection_version)
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
        self.indexing_fingerprint: str | None = None
        self.repository: str | None = None

    def health(self) -> dict[str, object]:
        """Return backend health information required by the lifecycle."""
        return self.transport.request("GET", "_cluster/health")

    def validate_schema(self, *, require_vectors: bool = False) -> None:
        """Validate the active alias and every Projection v3 field used by this store."""
        # A missing alias is an initial, explicitly rebuildable state. Existing
        # indices must never be silently repaired by bootstrap or planning.
        prefix = f"{self.settings.index}-"
        self.health()
        repository = getattr(self, "repository", None)
        if not isinstance(repository, str) or not repository:
            raise KnowledgeSearchError("OpenSearch repository binding is missing", category="schema_mismatch")
        alias = _repository_alias(self.settings, repository)
        if not alias.startswith(prefix):
            raise KnowledgeSearchError("OpenSearch alias prefix is invalid", category="schema_mismatch")
        indexes = self._alias_indexes(alias)
        if not indexes:
            raise KnowledgeSearchError("OpenSearch repository alias is absent", category="index_missing")
        if len(indexes) != 1:
            raise KnowledgeSearchError("OpenSearch alias must select one index", category="schema_mismatch")
        payload = self.transport.request("GET", f"{quote(indexes[0], safe='')}/_mapping")
        root = payload.get(indexes[0])
        mappings = root.get("mappings") if isinstance(root, dict) else None
        meta = mappings.get("_meta") if isinstance(mappings, dict) else None
        if isinstance(meta, dict) and meta.get("repository") not in (None, repository):
            raise KnowledgeSearchError("OpenSearch index belongs to another repository",
                                       category="schema_mismatch")
        properties = mappings.get("properties") if isinstance(mappings, dict) else None
        expected = _index_mapping(include_vectors=require_vectors)["mappings"]
        expected_properties = expected["properties"] if isinstance(expected, dict) else {}
        if not isinstance(mappings, dict) or not isinstance(properties, dict) or not isinstance(expected_properties, dict):
            raise KnowledgeSearchError("OpenSearch mapping is invalid", category="schema_mismatch")
        if mappings.get("dynamic") != "strict":
            raise KnowledgeSearchError("OpenSearch mapping must be strict", category="schema_mismatch")
        for name, field_spec in expected_properties.items():
            actual = properties.get(name)
            if not isinstance(actual, dict) or not isinstance(field_spec, dict) or actual.get("type") != field_spec.get("type"):
                raise KnowledgeSearchError("OpenSearch Projection v3 mapping is incompatible", category="schema_mismatch", metadata={"field": name, "rebuild_required": True})
            if "normalizer" in field_spec and actual.get("normalizer") != field_spec["normalizer"]:
                raise KnowledgeSearchError("OpenSearch identifier normalizer is incompatible", category="schema_mismatch", metadata={"field": name, "rebuild_required": True})
            if name == OPENSEARCH_VECTOR_FIELD and actual.get("dimension") != OPENSEARCH_VECTOR_DIMENSIONS:
                raise KnowledgeSearchError("OpenSearch vector dimensions are incompatible", category="schema_mismatch", metadata={"rebuild_required": True})
            if name == OPENSEARCH_VECTOR_FIELD and actual.get("method") != field_spec.get("method"):
                raise KnowledgeSearchError("OpenSearch vector method is incompatible", category="schema_mismatch", metadata={"rebuild_required": True})
        settings_payload = self.transport.request("GET", f"{quote(indexes[0], safe='')}/_settings")
        settings_root = settings_payload.get(indexes[0])
        settings_outer = settings_root.get("settings") if isinstance(settings_root, dict) else None
        settings_index = settings_outer.get("index") if isinstance(settings_outer, dict) else None
        analysis = settings_index.get("analysis") if isinstance(settings_index, dict) else None
        normalizers = analysis.get("normalizer") if isinstance(analysis, dict) else None
        identifier = normalizers.get("fetchcore_identifier_ci") if isinstance(normalizers, dict) else None
        if not isinstance(identifier, dict) or identifier.get("filter") != ["lowercase"]:
            raise KnowledgeSearchError("OpenSearch identifier normalization is incompatible", category="schema_mismatch")
        if require_vectors and (not isinstance(settings_index, dict) or settings_index.get("knn") not in (True, "true")):
            raise KnowledgeSearchError("OpenSearch k-NN index setting is disabled", category="schema_mismatch")

    def embedding_snapshot_matches(self, snapshot: OpenSearchIndexSnapshot) -> bool:
        provider, serializer = self.embedding_provider, self.embedding_serializer
        if provider is None:
            return snapshot.embedding_provider is None
        return bool(serializer is not None and snapshot.embedding_provider == provider.provider_id
                    and snapshot.embedding_model == provider.model_id
                    and snapshot.vector_dimensions == OPENSEARCH_VECTOR_DIMENSIONS
                    and snapshot.embedding_serializer_mode == serializer.mode.value
                    and snapshot.embedding_serializer_version == VECTOR_EMBEDDING_SERIALIZATION_VERSION
                    and snapshot.embedding_serializer_fingerprint == serializer.serializer_fingerprint)

    def load_documents(self, repository: str, *, index: str | None = None) -> tuple[KnowledgeDocument, ...]:
        """Read the complete active canonical corpus with deterministic pagination."""
        alias = index or _repository_alias(self.settings, repository)
        documents: list[KnowledgeDocument] = []
        after: list[object] | None = None
        while True:
            body: dict[str, object] = {
                "size": self.settings.batch_size,
                "query": {"bool": {"filter": [
                    {"term": {"record_kind": "knowledge"}},
                    {"term": {"repository": repository}},
                ]}},
                "sort": [{"id": "asc"}],
                "_source": {"excludes": [OPENSEARCH_VECTOR_FIELD]},
            }
            if after is not None:
                body["search_after"] = after
            payload = self.transport.request("POST", f"{alias}/_search", body)
            hits = payload.get("hits")
            rows = hits.get("hits") if isinstance(hits, dict) else None
            if not isinstance(rows, list):
                raise KnowledgeSearchError("OpenSearch document page is invalid", category="response_parsing_failure")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("_source"), dict):
                    raise KnowledgeSearchError("OpenSearch document is invalid", category="response_parsing_failure")
                documents.append(self.reader.read(row["_source"]))
            if len(rows) < self.settings.batch_size:
                break
            last = rows[-1]
            after = last.get("sort") if isinstance(last, dict) else None
            if not isinstance(after, list):
                raise KnowledgeSearchError("OpenSearch pagination cursor is invalid", category="response_parsing_failure")
        return tuple(documents)

    def load_snapshot(self, repository: str) -> OpenSearchIndexSnapshot:
        """Load and validate the active repository snapshot."""
        alias = _repository_alias(self.settings, repository)
        body = {
            "size": 2,
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"record_kind": "snapshot"}},
                        {"term": {"repository": repository}},
                    ]
                }
            },
        }
        try:
            payload = self.transport.request("POST", f"{alias}/_search", body)
        except KnowledgeSearchError as exc:
            if exc.category == "index_missing":
                raise KnowledgeSearchError(
                    "OpenSearch has no active index snapshot",
                    category="snapshot_missing",
                    metadata={"repository": repository},
                ) from exc
            raise
        docs, _ = _search_documents(payload)
        if len(docs) != 1:
            raise KnowledgeSearchError(
                "OpenSearch repository scope does not select exactly one snapshot",
                category="snapshot_missing",
                metadata={"repository": repository, "snapshot_count": len(docs)},
            )
        snapshot = OpenSearchIndexSnapshot.from_mapping(docs[0])
        self._ensure_snapshot_compatible(snapshot)
        if snapshot.status != "ready":
            raise KnowledgeSearchError(
                "OpenSearch snapshot is not ready",
                category="index_stale",
                metadata={"snapshot_status": snapshot.status},
            )
        return snapshot

    def full_rebuild(self, result: RepositoryIndexResult) -> OpenSearchIndexSyncResult:
        """Replace the repository projection with a complete rebuild."""
        self._ensure_complete(result)
        started = self.timer()
        indexed_at = self.clock()
        snapshot = self._snapshot(result, indexed_at=indexed_at)
        concrete = _concrete_index(self.settings, result, uuid4().hex)
        alias = _repository_alias(self.settings, result.repository)
        with self._write_lock(result.repository):
            old_indexes = self._alias_indexes(alias)
            self._validate_alias_ownership(old_indexes, result.repository)
            self.transport.request("PUT", concrete, self._generation_mapping(result.repository))
            try:
                (projected, embedding_requests, embedding_vectors,
                 embedding_duration_ms) = self._project_documents_with_telemetry(result.documents)
                self._bulk_upsert(concrete, (*projected, snapshot.to_mapping()))
                self.transport.request("POST", f"{concrete}/_refresh", {})
                self._validate_staged(concrete, snapshot)
                self._promote(alias, concrete, old_indexes, snapshot)
            except Exception:
                # The alias still points at the old generation before promotion.
                # After an ambiguous promotion response, keep staging for recovery.
                if concrete not in self._alias_indexes(alias):
                    self._delete_index_if_present(concrete)
                raise

        duration_ms = elapsed_ms(started, self.timer())
        telemetry = IndexSyncTelemetry(
            duration_ms=duration_ms,
            backend_writes=len(result.documents),
            backend_deletes=0,
            embedding_requests=embedding_requests,
            embedding_vectors=embedding_vectors,
            embedding_duration_ms=embedding_duration_ms,
        )
        return OpenSearchIndexSyncResult(
            mode="full_rebuild",
            snapshot=snapshot,
            added=len(result.documents),
            updated=0,
            deleted=0,
            unchanged=0,
            duration_ms=duration_ms,
            telemetry=telemetry,
        )

    def incremental_sync(
        self, result: RepositoryIndexResult, *,
        expected_snapshot: OpenSearchIndexSnapshot | None = None,
    ) -> OpenSearchIndexSyncResult:
        """Stage a document delta over the active generation and atomically publish it.

        OpenSearch copies unchanged records internally. FetchCore embeds and sends
        only changed canonical documents. The copy cost is reported as writes.
        """
        self._ensure_complete(result)
        started = self.timer()
        alias = _repository_alias(self.settings, result.repository)
        with self._write_lock(result.repository):
            previous = self.load_snapshot(result.repository)
            if expected_snapshot is not None and previous != expected_snapshot:
                raise KnowledgeSearchError("OpenSearch snapshot changed during indexing",
                                           category="concurrent_write")
            if previous.indexing_fingerprint != self.indexing_fingerprint or not self.embedding_snapshot_matches(previous):
                raise KnowledgeSearchError("OpenSearch snapshot identity changed", category="rebuild_required")
            old_indexes = self._alias_indexes(alias)
            if len(old_indexes) != 1:
                raise KnowledgeSearchError("OpenSearch alias is ambiguous", category="schema_mismatch")
            snapshot = self._snapshot(result, indexed_at=self.clock())
            changes = result.changes
            upserts = (*changes.added, *(item.document for item in changes.updated))
            removed_ids = {item.id for item in changes.removed}
            removed_ids.update(item.previous_document_id for item in changes.updated
                               if item.previous_document_id != item.document.id)
            concrete = _concrete_index(self.settings, result, uuid4().hex)
            self.transport.request("PUT", concrete, self._generation_mapping(result.repository))
            staging_copy_active = False
            try:
                staging_copy_active = True
                copied = self._copy_to_stage({
                    "source": {"index": old_indexes[0]}, "dest": {"index": concrete},
                })
                staging_copy_active = False
                if copied.get("failures") or copied.get("timed_out") is True:
                    raise KnowledgeSearchError("OpenSearch staging copy failed", category="index_update_failure")
                copied_count = copied.get("created", 0)
                if not isinstance(copied_count, int) or copied_count != previous.document_count + 1:
                    raise KnowledgeSearchError("OpenSearch staging copy count is incomplete", category="index_update_failure")
                (projected, embedding_requests, embedding_vectors,
                 embedding_duration_ms) = self._project_documents_with_telemetry(upserts)
                self._bulk_upsert(concrete, projected)
                self._bulk_delete(concrete, removed_ids)
                self._bulk_upsert(concrete, (snapshot.to_mapping(),))
                self.transport.request("POST", f"{concrete}/_refresh", {})
                self._validate_staged(concrete, snapshot)
                self._promote(alias, concrete, old_indexes, snapshot)
            except Exception:
                # A task may outlive a transport timeout. Keep its private stage
                # until an operator confirms task completion before cleanup.
                if not staging_copy_active and concrete not in self._alias_indexes(alias):
                    self._delete_index_if_present(concrete)
                raise
        duration_ms = elapsed_ms(started, self.timer())
        return OpenSearchIndexSyncResult(
            mode="incremental", snapshot=snapshot,
            added=len(changes.added), updated=len(changes.updated),
            deleted=len(changes.removed), unchanged=len(changes.unchanged),
            duration_ms=duration_ms,
            telemetry=IndexSyncTelemetry(
                duration_ms=duration_ms,
                backend_writes=copied_count + len(upserts) + 1,
                backend_deletes=len(removed_ids),
                embedding_requests=embedding_requests,
                embedding_vectors=embedding_vectors,
                embedding_duration_ms=embedding_duration_ms,
            ),
        )

    @contextmanager
    def _write_lock(self, repository: str) -> Iterator[None]:
        """Serialize cooperating writers across processes using index creation."""
        name = f"{_repository_alias(self.settings, repository)}-write-lock"
        token = uuid4().hex
        try:
            self.transport.request("PUT", name, {"mappings": {"_meta": {
                "fetchcore_lock_token": token, "repository": repository,
            }}})
        except KnowledgeSearchError as exc:
            if exc.category == "schema_mismatch":
                raise KnowledgeSearchError("OpenSearch writer lock is held", category="concurrent_write") from exc
            raise
        try:
            yield
        finally:
            payload = self.transport.request("GET", f"{name}/_mapping")
            root = payload.get(name)
            mappings = root.get("mappings") if isinstance(root, dict) else None
            meta = mappings.get("_meta") if isinstance(mappings, dict) else None
            if not isinstance(meta, dict) or meta.get("fetchcore_lock_token") != token:
                raise KnowledgeSearchError("OpenSearch writer lock ownership changed", category="concurrent_write")
            self.transport.request("DELETE", name)

    def _promote(self, alias: str, concrete: str, old_indexes: tuple[str, ...],
                 snapshot: OpenSearchIndexSnapshot) -> None:
        if self._alias_indexes(alias) != old_indexes:
            raise KnowledgeSearchError("OpenSearch alias changed during staging", category="concurrent_write")
        actions = [
            *({"remove": {"index": old, "alias": alias}} for old in old_indexes),
            {"add": {"index": concrete, "alias": alias}},
        ]
        self.transport.request("POST", "_aliases", {"actions": actions})
        if self._alias_indexes(alias) != (concrete,) or self.load_snapshot(snapshot.repository) != snapshot:
            raise KnowledgeSearchError("OpenSearch alias promotion is incomplete", category="index_update_failure")
        # Keep old generations for manual rollback and recovery.

    def _generation_mapping(self, repository: str) -> dict[str, object]:
        mapping = _index_mapping(include_vectors=self.embedding_provider is not None)
        mappings = mapping["mappings"]
        assert isinstance(mappings, dict)
        mappings["_meta"] = {"fetchcore_projection": PROJECTION_VERSION,
                             "repository": repository}
        return mapping

    def _validate_alias_ownership(self, indexes: tuple[str, ...], repository: str) -> None:
        if len(indexes) > 1:
            raise KnowledgeSearchError("OpenSearch alias has multiple owners",
                                       category="schema_mismatch")
        if not indexes:
            return
        index = indexes[0]
        payload = self.transport.request("GET", f"{quote(index, safe='')}/_mapping")
        root = payload.get(index)
        mappings = root.get("mappings") if isinstance(root, dict) else None
        meta = mappings.get("_meta") if isinstance(mappings, dict) else None
        if isinstance(meta, dict) and meta.get("repository") == repository:
            return
        count = self.transport.request("POST", f"{quote(index, safe='')}/_count",
                                       {"query": {"match_all": {}}}).get("count")
        if count != 0:
            raise KnowledgeSearchError("OpenSearch index ownership is unverified",
                                       category="ownership_unverified")

    def _bulk_delete(self, index: str, identifiers: Iterable[str]) -> None:
        values = sorted(set(identifiers))
        if not values:
            return
        for offset in range(0, len(values), self.settings.batch_size):
            batch = values[offset:offset + self.settings.batch_size]
            payload = ("\n".join(json.dumps({"delete": {"_index": index, "_id": item}})
                                 for item in batch) + "\n").encode("utf-8")
            response = self.transport.request("POST", "_bulk", payload,
                                              content_type="application/x-ndjson")
            if response.get("errors") is True:
                raise KnowledgeSearchError("OpenSearch bulk delete failed", category="index_update_failure")

    def _copy_to_stage(self, body: dict[str, object]) -> dict[str, object]:
        """Wait for a server-side copy task without a long blocking HTTP response."""
        submitted = self.transport.request(
            "POST", "_reindex?wait_for_completion=false&refresh=true", body,
        )
        task_id = submitted.get("task")
        if not isinstance(task_id, str) or not task_id:
            raise KnowledgeSearchError("OpenSearch reindex task id is missing",
                                       category="response_parsing_failure")
        deadline = self.timer() + max(60.0, self.settings.timeout_seconds * 12)
        while self.timer() < deadline:
            state = self.transport.request("GET", f"_tasks/{quote(task_id, safe=':')}")
            if state.get("completed") is True:
                response = state.get("response")
                if not isinstance(response, dict) or state.get("error") is not None:
                    raise KnowledgeSearchError("OpenSearch reindex task failed",
                                               category="index_update_failure")
                return response
            time.sleep(0.2)
        raise KnowledgeSearchError("OpenSearch reindex task did not finish before deadline",
                                   category="timeout", metadata={"staging_task_active": True})

    def reset(self, repository: str) -> None:
        """Remove persisted projection state for the requested repository."""
        alias = _repository_alias(self.settings, repository)
        for index in self._alias_indexes(alias):
            self._delete_index_if_present(index)

    def _validate_staged(
        self,
        concrete: str,
        snapshot: OpenSearchIndexSnapshot,
    ) -> None:
        payload = self.transport.request(
            "POST",
            f"{concrete}/_search",
            {
                "size": 2,
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"record_kind": "snapshot"}},
                            {"term": {"repository": snapshot.repository}},
                        ]
                    }
                },
            },
        )
        docs, _ = _search_documents(payload)
        if len(docs) != 1:
            raise KnowledgeSearchError(
                "OpenSearch staged projection lacks exactly one snapshot",
                category="index_update_failure",
            )
        observed = OpenSearchIndexSnapshot.from_mapping(docs[0])
        self._ensure_snapshot_compatible(observed)
        if observed != snapshot:
            raise KnowledgeSearchError(
                "OpenSearch staged snapshot does not match compiled corpus",
                category="index_update_failure",
            )

        count_payload = self.transport.request(
            "POST",
            f"{concrete}/_count",
            {"query": {"term": {"record_kind": "knowledge"}}},
        )
        try:
            count = int(cast(float | int | str, count_payload["count"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise KnowledgeSearchError(
                "OpenSearch count response is invalid",
                category="response_parsing_failure",
            ) from exc
        if count != snapshot.document_count:
            raise KnowledgeSearchError(
                "OpenSearch staged projection document count is incomplete",
                category="index_update_failure",
                metadata={"expected": snapshot.document_count, "actual": count},
            )
        documents = self.load_documents(snapshot.repository, index=concrete)
        if len(documents) != snapshot.document_count or corpus_checksum(documents) != snapshot.corpus_checksum:
            raise KnowledgeSearchError(
                "OpenSearch staged canonical corpus is inconsistent",
                category="index_update_failure",
            )

    def _project_documents(
        self,
        documents: Iterable[KnowledgeDocument],
    ) -> tuple[dict[str, object], ...]:
        projected, _, _, _ = self._project_documents_with_telemetry(documents)
        return projected

    def _project_documents_with_telemetry(
        self,
        documents: Iterable[KnowledgeDocument],
    ) -> tuple[tuple[dict[str, object], ...], int, int, int]:
        source = tuple(documents)
        if not source:
            return (), 0, 0, 0
        projected: list[dict[str, object]] = []
        embedding_requests = 0
        embedding_vectors = 0
        embedding_duration_ms = 0
        for offset in range(0, len(source), self.settings.batch_size):
            batch = source[offset : offset + self.settings.batch_size]
            records = tuple(self.compiler.compile(document) for document in batch)
            payloads = [record.to_mapping() for record in records]
            if self.embedding_provider is not None:
                assert self.embedding_serializer is not None
                texts = tuple(
                    self.embedding_serializer.serialize_record(record)
                    for record in records
                )
                embedding_started = self.timer()
                vectors = self.embedding_provider.embed(texts)
                embedding_duration_ms += elapsed_ms(embedding_started, self.timer())
                embedding_requests += 1
                embedding_vectors += len(vectors)
                if len(vectors) != len(records):
                    raise KnowledgeSearchError(
                        "embedding provider returned an incomplete OpenSearch vector batch",
                        category="embedding_failed",
                    )
                dimensions = validate_vector_dimensions(vectors)
                if dimensions != OPENSEARCH_VECTOR_DIMENSIONS:
                    raise KnowledgeSearchError(
                        "OpenSearch embedding dimensions do not match the frozen vector mapping",
                        category="schema_mismatch",
                        metadata={
                            "expected_dimensions": OPENSEARCH_VECTOR_DIMENSIONS,
                            "actual_dimensions": dimensions,
                            "rebuild_required": True,
                        },
                    )
                for payload, vector in zip(payloads, vectors, strict=True):
                    payload[OPENSEARCH_VECTOR_FIELD] = list(vector)
            projected.extend(payloads)
        return (
            tuple(projected),
            embedding_requests,
            embedding_vectors,
            embedding_duration_ms,
        )

    def _snapshot(
        self,
        result: RepositoryIndexResult,
        *,
        indexed_at: str,
    ) -> OpenSearchIndexSnapshot:
        serializer = self.embedding_serializer
        provider = self.embedding_provider
        return OpenSearchIndexSnapshot(
            repository=result.repository,
            branch=result.branch,
            revision=result.revision,
            corpus_checksum=corpus_checksum(result.documents),
            document_count=len(result.documents),
            projection_version=self.settings.projection_version,
            indexed_at=indexed_at,
            embedding_provider=None if provider is None else provider.provider_id,
            embedding_model=None if provider is None else provider.model_id,
            vector_dimensions=None if provider is None else OPENSEARCH_VECTOR_DIMENSIONS,
            embedding_serializer_mode=None if serializer is None else serializer.mode.value,
            embedding_serializer_version=(
                None if serializer is None else VECTOR_EMBEDDING_SERIALIZATION_VERSION
            ),
            embedding_serializer_fingerprint=(
                None if serializer is None else serializer.serializer_fingerprint
            ),
            projection_fingerprint=self.projection_fingerprint,
            indexing_fingerprint=self.indexing_fingerprint,
        )

    def _bulk_upsert(
        self,
        index: str,
        records: Iterable[Mapping[str, object]],
    ) -> None:
        batch: list[Mapping[str, object]] = []
        for record in records:
            batch.append(record)
            if len(batch) == self.settings.batch_size:
                self._write_bulk(index, batch)
                batch = []
        if batch:
            self._write_bulk(index, batch)

    def _write_bulk(
        self,
        index: str,
        records: Iterable[Mapping[str, object]],
    ) -> None:
        lines: list[str] = []
        for record in records:
            identifier = str(record["id"])
            lines.append(
                json.dumps(
                    {"index": {"_index": index, "_id": identifier}},
                    separators=(",", ":"),
                )
            )
            lines.append(json.dumps(dict(record), separators=(",", ":"), default=str))
        payload = ("\n".join(lines) + "\n").encode("utf-8")
        response = self.transport.request(
            "POST",
            "_bulk",
            payload,
            content_type="application/x-ndjson",
        )
        if response.get("errors") is True:
            raise KnowledgeSearchError(
                "OpenSearch bulk projection write failed",
                category="index_update_failure",
            )

    def _alias_indexes(self, alias: str) -> tuple[str, ...]:
        try:
            payload = self.transport.request("GET", f"_alias/{quote(alias, safe='')}")
        except KnowledgeSearchError as exc:
            if exc.category == "index_missing":
                return ()
            raise
        return tuple(sorted(str(name) for name in payload))

    def _delete_index_if_present(self, index: str) -> None:
        try:
            self.transport.request("DELETE", quote(index, safe=""))
        except KnowledgeSearchError as exc:
            if exc.category != "index_missing":
                raise

    def _ensure_snapshot_compatible(self, snapshot: OpenSearchIndexSnapshot) -> None:
        if (
            snapshot.projection_version == self.settings.projection_version
            and snapshot.projection_fingerprint == self.projection_fingerprint
        ):
            return
        raise KnowledgeSearchError(
            "OpenSearch snapshot Projection v3 contract is incompatible",
            category="schema_mismatch",
            metadata={
                "expected_projection_version": self.settings.projection_version,
                "actual_projection_version": snapshot.projection_version,
                "expected_projection_fingerprint": self.projection_fingerprint,
                "actual_projection_fingerprint": snapshot.projection_fingerprint,
                "rebuild_required": True,
            },
        )

    @staticmethod
    def _ensure_complete(result: RepositoryIndexResult) -> None:
        if (
            not result.changes.removal_detection_complete
            or not result.telemetry.scan_traversal_complete
        ):
            raise KnowledgeSearchError(
                "repository observation is incomplete; OpenSearch projection was not changed",
                category="unsafe_snapshot",
                metadata={
                    "removal_detection_complete": result.changes.removal_detection_complete,
                    "scan_traversal_complete": result.telemetry.scan_traversal_complete,
                    "blockers": list(result.telemetry.removal_detection_blockers),
                },
            )


class OpenSearchKnowledgeIndex:
    """RepositoryIndexer coordinator for the blue/green OpenSearch store."""

    def __init__(self, store: OpenSearchIndexStore, indexer: RepositoryIndexer) -> None:
        """Initialize OpenSearchKnowledgeIndex with its explicit dependencies."""
        self.store = store
        self.indexer = indexer

    def rebuild(self, workspace: Path) -> OpenSearchIndexSyncResult:
        """Rebuild the repository projection from canonical source state."""
        return self.store.full_rebuild(self.indexer.index(workspace))

    def sync(self, workspace: Path) -> OpenSearchIndexSyncResult:
        """Synchronize the repository projection with canonical source state."""
        repository = self.indexer.resolve_repository_identity(workspace)
        snapshot = self.store.load_snapshot(repository)
        previous = self.store.load_documents(repository)
        if len(previous) != snapshot.document_count or corpus_checksum(previous) != snapshot.corpus_checksum:
            raise KnowledgeSearchError("OpenSearch active corpus differs from its snapshot",
                                       category="index_stale")
        result = self.indexer.index(workspace, previous_documents=previous)
        changes = result.changes
        if (not (changes.added or changes.updated or changes.removed)
            and snapshot.revision == result.revision
            and (result.branch is None or snapshot.branch == result.branch)):
            return OpenSearchIndexSyncResult(
                "incremental", snapshot, 0, 0, 0, snapshot.document_count, 0,
                IndexSyncTelemetry(0, 0, 0),
            )
        return self.store.incremental_sync(result, expected_snapshot=snapshot)


def _unconfigured_embedding_provider(retrieval: RetrievalConfig) -> EmbeddingProvider:
    """Fail explicitly when dense retrieval was not wired by composition."""
    raise KnowledgeSearchError(
        "OpenSearch dense retrieval requires a composed embedding provider",
        category="unsupported_capability",
        metadata={"backend": "opensearch", "capability": f"embedding_provider:{retrieval.dense.embedding.provider}"},
    )


class OpenSearchBackend:
    """Projection v3 lexical, dense and hybrid retrieval implemented with OpenSearch."""

    backend_id = "opensearch"
    retrieval_capabilities = RetrievalBackendCapabilities(
        canonical_query=True, targeted_lookup=True,
        structured_anchors=True, boolean_expressions=True,
        lexical=True,
        dense=True,
        hybrid=True,
    )

    def __init__(
        self,
        *,
        transport_factory: Callable[
            [OpenSearchSettings], OpenSearchTransport
        ] = UrllibOpenSearchTransport,
        embedding_factory: Callable[
            [RetrievalConfig], EmbeddingProvider
        ] = _unconfigured_embedding_provider,
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize OpenSearchBackend with its explicit dependencies."""
        self.transport_factory = transport_factory
        self.embedding_factory = embedding_factory
        self.timer = timer

    def lookup_exact(self, anchor: Anchor, scope: KnowledgeSearchScope) -> KnowledgeSearchResponse:
        # Reuse the exact keyword translator and snapshot/provenance checks.
        # Two records suffice to detect ambiguity; diagnostics must not enlarge it.
        exact_scope = replace(scope, max_results=2, retrieval=None,
                              settings={**scope.settings, "diagnostic_candidate_window": 2})
        return self.search(SearchIntent(anchors=(anchor,)), exact_scope, ())

    def search(
        self,
        intent: SearchIntent,
        scope: KnowledgeSearchScope,
        requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        """Execute retrieval for the supplied intent, scope, and requirements."""
        mode = scope.retrieval.mode if scope.retrieval else "lexical"
        channels = ("lexical", "dense") if mode == "hybrid" else (mode,)
        for channel in channels:
            validate_intent_translation(
                intent, channel=channel, capabilities=self.retrieval_capabilities,
            )
        started = self.timer()
        if scope.max_results < 1:
            raise KnowledgeSearchError(
                "OpenSearch candidate budget must be positive",
                category="schema_mismatch",
            )
        settings = OpenSearchSettings.from_mapping(scope.settings)
        transport = self.transport_factory(settings)
        store = OpenSearchIndexStore(
            transport,
            settings,
            clock=lambda: "",
            timer=self.timer,
        )
        repository = scope.repository or RepositoryIndexer().resolve_repository_identity(
            scope.workspace
        )
        snapshot = store.load_snapshot(repository)
        if scope.revision is not None and scope.revision != snapshot.revision:
            raise KnowledgeSearchError(
                "requested revision does not match the active OpenSearch snapshot",
                category="snapshot_revision_mismatch",
                metadata={
                    "requested_revision": scope.revision,
                    "snapshot_revision": snapshot.revision,
                    "repository": snapshot.repository,
                },
            )
        if snapshot.document_count == 0:
            raise KnowledgeSearchError(
                "active OpenSearch snapshot contains no knowledge documents",
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

        lexical_started = self.timer()
        body = _search_body(intent, scope, snapshot, settings)
        alias = _repository_alias(settings, repository)
        payload = transport.request("POST", f"{alias}/_search", body)
        docs, num_found = _search_documents(payload)
        if scope.repository_paths:
            docs = repository_source_documents(docs, scope.repository_paths)

        max_raw_score = max(
            (float_value(item.get("score")) for item in docs),
            default=0.0,
        )
        matches = tuple(match_projection_record(intent, item, settings) for item in docs)
        provider_scores = normalize_provider_scores(docs, matches, max_raw_score)
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
            raw_score = float_value(item.get("score"))
            knowledge_type = str(item.get("knowledge_type", "unknown"))
            capabilities = classify_capabilities(knowledge_type, match)
            source_ref = str(item.get("source_ref", ""))
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
                        capability: "provider_classification"
                        for capability in capabilities
                    },
                    matched_terms=match.matched_terms,
                    matched_signals=match.matched_signals,
                    reasons=tuple(
                        f"opensearch_match:{channel}" for channel in match.channels
                    ),
                    line_start=optional_int(item.get("line_start")),
                    line_end=optional_int(item.get("line_end")),
                    revision=snapshot.revision,
                    checksum=optional_str(item.get("checksum")),
                    source_type=str(item.get("source_type", "repository")),
                    observations=structural_observations(
                        item,
                        source_ref=source_ref,
                        revision=snapshot.revision,
                    ),
                    relationships=relationships(item),
                    query_matches=query_match_evidence(
                        match,
                        source_ref=source_ref,
                        provenance="opensearch_projection_v3_match",
                    ),
                    metadata={
                        "raw_opensearch_score": raw_score,
                        "document_revision": item.get("document_revision"),
                        "snapshot_revision": snapshot.revision,
                        "snapshot_corpus_checksum": snapshot.corpus_checksum,
                        "projection_version": snapshot.projection_version,
                        "projection_fingerprint": snapshot.projection_fingerprint,
                        "chunk_ref": item.get("chunk_ref"),
                        "branch": item.get("branch"),
                        "path": item.get("path"),
                        "filename": item.get("filename"),
                        "technical_role": item.get("technical_role"),
                        "symbols": item.get("symbols", []),
                        "source_checksum": item.get("source_checksum"),
                        "matched_channels": list(match.channels),
                        "matched_channel_values": {
                            key: list(value)
                            for key, value in match.channel_values.items()
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
                        "implementation_evidence": list(
                            match.implementation_evidence
                        ),
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

        ended = self.timer()
        lexical_ms = elapsed_ms(lexical_started, ended)
        duration_ms = elapsed_ms(started, ended)
        telemetry = RetrievalTelemetry(
            mode="lexical",
            duration_ms=duration_ms,
            candidates_examined=len(docs),
            candidates_returned=len(results),
            returned_chars=returned_chars(item.content for item in results),
            stages=(
                RetrievalStageTelemetry(
                    name="lexical",
                    duration_ms=lexical_ms,
                    candidates_out=len(docs),
                ),
            ),
            fingerprints={
                **(
                    {"projection": snapshot.projection_fingerprint}
                    if snapshot.projection_fingerprint is not None
                    else {}
                ),
            },
            budget={
                "max_results": scope.max_results,
                "output_truncated": output_truncated,
            },
        )
        diagnostic = bounded_diagnostic_metadata(
            docs=docs,
            matches=matches,
            provider_scores=provider_scores,
            output_limit=scope.max_results,
            max_raw_score=max_raw_score,
            settings=settings,
            num_found=num_found,
        )
        return KnowledgeSearchResponse(
            backend=self.backend_id,
            status="found" if results else "not_found",
            results=tuple(results),
            duration_ms=duration_ms,
            telemetry=telemetry,
            metadata={
                "backend": self.backend_id,
                "repository": snapshot.repository,
                "snapshot_revision": snapshot.revision,
                "snapshot_status": snapshot.status,
                "projection_version": snapshot.projection_version,
                "projection_fingerprint": snapshot.projection_fingerprint,
                "query_type_counts": query_type_counts(intent),
                "query_signals": [item.to_mapping() for item in intent.signals],
                "typed_query_mapping": typed_query_mapping_telemetry(
                    intent,
                    settings,
                ),
                "opensearch_query": body,
                **translation_diagnostics(intent, scope, lexical=body),
                "candidates_found": num_found,
                "candidates_considered": len(docs),
                "candidates_returned": len(results),
                "output_limit": scope.max_results,
                "output_truncated": output_truncated,
                "raw_score_distribution": score_distribution(
                    [float_value(item.get("score")) for item in docs]
                ),
                "normalized_score_distribution": score_distribution(
                    [item.score for item in results]
                ),
                "matched_channels": distribution(
                    channel
                    for result in results
                    for channel in cast(
                        list[str],
                        result.metadata["matched_channels"],
                    )
                ),
                "knowledge_type_distribution": distribution(
                    item.knowledge_type for item in results
                ),
                "technical_role_distribution": {
                    "observed": distribution(
                        technical_role_value(item.get("technical_role"))
                        for item in docs
                    ),
                    "returned": distribution(
                        technical_role_value(item.metadata.get("technical_role"))
                        for item in results
                    ),
                },
                "search_requirements": [
                    item.to_mapping() for item in requirements
                ],
                "requirement_allocation": "disabled_v1",
                **diagnostic,
            },
        )



@dataclass(frozen=True)
class _OpenSearchLexicalCandidate:
    rank: int
    item: dict[str, object]
    match: ProjectionV3MatchResult
    raw_score: float
    score: float


@dataclass(frozen=True)
class _OpenSearchDenseCandidate:
    rank: int
    item: dict[str, object]
    raw_score: float
    score: float


def _dense_or_hybrid_search(
    *,
    backend: OpenSearchBackend,
    started: float,
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    requirements: tuple[SearchRequirement, ...],
    settings: OpenSearchSettings,
    transport: OpenSearchTransport,
    snapshot: OpenSearchIndexSnapshot,
    retrieval: RetrievalConfig,
) -> KnowledgeSearchResponse:
    embedding_started = backend.timer()
    embedder, serializer, vector, query_text = _prepare_dense_query(
        backend=backend,
        intent=intent,
        snapshot=snapshot,
        retrieval=retrieval,
    )
    embedding_ms = elapsed_ms(embedding_started, backend.timer())
    dense_started = backend.timer()
    dense_candidates, dense_num_found, dense_telemetry = _opensearch_dense_candidates(
        transport=transport,
        scope=scope,
        snapshot=snapshot,
        settings=settings,
        vector=vector,
    )
    dense_ms = elapsed_ms(dense_started, backend.timer())
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
        duration_ms = elapsed_ms(started, backend.timer())
        telemetry = RetrievalTelemetry(
            mode="dense",
            duration_ms=duration_ms,
            candidates_examined=len(dense_candidates),
            candidates_returned=len(results),
            returned_chars=returned_chars(item.content for item in results),
            stages=(
                RetrievalStageTelemetry(name="embedding", duration_ms=embedding_ms),
                RetrievalStageTelemetry(
                    name="dense",
                    duration_ms=dense_ms,
                    candidates_out=len(dense_candidates),
                ),
            ),
            fingerprints={
                **(
                    {"projection": snapshot.projection_fingerprint}
                    if snapshot.projection_fingerprint is not None
                    else {}
                ),
                "embedding_serializer": serializer.serializer_fingerprint,
            },
            budget={
                "max_results": scope.max_results,
                "output_truncated": output_truncated,
            },
            metadata={
                "embedding_provider": embedder.provider_id,
                "embedding_model": embedder.model_id,
                "vector_dimensions": len(vector),
            },
        )
        return KnowledgeSearchResponse(
            backend=backend.backend_id,
            status="found" if results else "not_found",
            results=results,
            duration_ms=duration_ms,
            telemetry=telemetry,
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
                **translation_diagnostics(intent, scope, dense=query_text),
                "vector_only": True,
                "lexical_fallback": False,
                "hybrid_fusion": False,
                "reranker": False,
                "opensearch_dense_query": dense_telemetry,
                "query_embedding_text_sha256": hashlib.sha256(
                    query_text.encode("utf-8")
                ).hexdigest(),
                "candidates_found": dense_num_found,
                "candidates_considered": len(dense_candidates),
                "candidates_returned": len(results),
                "output_limit": scope.max_results,
                "output_truncated": len(dense_candidates) > scope.max_results,
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

    lexical_started = backend.timer()
    lexical_candidates, lexical_num_found, lexical_body = _opensearch_lexical_candidates(
        transport=transport,
        intent=intent,
        scope=scope,
        snapshot=snapshot,
        settings=settings,
    )
    lexical_ms = elapsed_ms(lexical_started, backend.timer())
    return _hybrid_response(
        backend=backend,
        intent=intent,
        started=started,
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
        lexical_body=lexical_body,
        dense_telemetry=dense_telemetry,
        embedding_ms=embedding_ms,
        dense_ms=dense_ms,
        lexical_ms=lexical_ms,
    )


def _prepare_dense_query(
    *,
    backend: OpenSearchBackend,
    intent: SearchIntent,
    snapshot: OpenSearchIndexSnapshot,
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
            "configured embedding provider is not supported by OpenSearch dense retrieval",
            category="unsupported_capability",
            metadata={
                "backend": backend.backend_id,
                "capability": f"embedding_provider:{requested.provider}",
            },
        )
    if embedder.model_id != requested.model:
        raise KnowledgeSearchError(
            "OpenSearch embedding factory returned a different model than requested",
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
    if (
        dimensions != snapshot.vector_dimensions
        or dimensions != OPENSEARCH_VECTOR_DIMENSIONS
    ):
        raise KnowledgeSearchError(
            "query embedding dimensions do not match the active OpenSearch vector snapshot",
            category="schema_mismatch",
            metadata={
                "expected_dimensions": snapshot.vector_dimensions,
                "mapping_dimensions": OPENSEARCH_VECTOR_DIMENSIONS,
                "actual_dimensions": dimensions,
                "rebuild_required": True,
            },
        )
    return embedder, serializer, vectors[0], query_text


def _validate_dense_snapshot_identity(
    *,
    snapshot: OpenSearchIndexSnapshot,
    embedder: EmbeddingProvider,
    serializer: ProjectionV3EmbeddingSerializer,
) -> None:
    expected: dict[str, object] = {
        "embedding_provider": embedder.provider_id,
        "embedding_model": embedder.model_id,
        "vector_dimensions": OPENSEARCH_VECTOR_DIMENSIONS,
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
            "active OpenSearch vector snapshot does not match the requested embedding identity",
            category="index_stale",
            metadata={
                "rebuild_required": True,
                "embedding_identity_mismatches": mismatches,
            },
        )


def _opensearch_dense_candidates(
    *,
    transport: OpenSearchTransport,
    scope: KnowledgeSearchScope,
    snapshot: OpenSearchIndexSnapshot,
    settings: OpenSearchSettings,
    vector: tuple[float, ...],
) -> tuple[tuple[_OpenSearchDenseCandidate, ...], int, dict[str, object]]:
    query_limit = max(
        scope.max_results,
        settings.diagnostic_candidate_window or scope.max_results,
    )
    filters = _scope_filters(scope, snapshot)
    body: dict[str, object] = {
        "size": query_limit,
        "query": {
            "knn": {
                OPENSEARCH_VECTOR_FIELD: {
                    "vector": list(vector),
                    "k": query_limit,
                    "filter": {"bool": {"filter": filters}},
                }
            }
        },
        "_source": {"excludes": [OPENSEARCH_VECTOR_FIELD]},
        "track_total_hits": True,
    }
    alias = _repository_alias(settings, snapshot.repository)
    payload = transport.request("POST", f"{alias}/_search", body)
    docs, num_found = _search_documents(payload)
    if scope.repository_paths:
        docs = repository_source_documents(docs, scope.repository_paths)
    docs.sort(
        key=lambda item: (
            -float_value(item.get("score")),
            str(item.get("id", "")),
        )
    )
    candidates = tuple(
        _OpenSearchDenseCandidate(
            rank=rank,
            item=item,
            raw_score=float_value(item.get("score")),
            score=_clamp(float_value(item.get("score"))),
        )
        for rank, item in enumerate(docs, start=1)
    )
    telemetry = {
        "field": OPENSEARCH_VECTOR_FIELD,
        "k": query_limit,
        "vector_dimensions": len(vector),
        "filter": {"bool": {"filter": filters}},
    }
    return candidates, num_found, telemetry


def _opensearch_lexical_candidates(
    *,
    transport: OpenSearchTransport,
    intent: SearchIntent,
    scope: KnowledgeSearchScope,
    snapshot: OpenSearchIndexSnapshot,
    settings: OpenSearchSettings,
) -> tuple[tuple[_OpenSearchLexicalCandidate, ...], int, dict[str, object]]:
    body = _search_body(intent, scope, snapshot, settings)
    alias = _repository_alias(settings, snapshot.repository)
    payload = transport.request("POST", f"{alias}/_search", body)
    docs, num_found = _search_documents(payload)
    if scope.repository_paths:
        docs = repository_source_documents(docs, scope.repository_paths)
    max_raw_score = max(
        (float_value(item.get("score")) for item in docs),
        default=0.0,
    )
    matches = tuple(match_projection_record(intent, item, settings) for item in docs)
    scores = normalize_provider_scores(docs, matches, max_raw_score)
    candidates = tuple(
        _OpenSearchLexicalCandidate(
            rank=rank,
            item=item,
            match=match,
            raw_score=float_value(item.get("score")),
            score=score,
        )
        for rank, (item, match, score) in enumerate(
            zip(docs, matches, scores, strict=True),
            start=1,
        )
    )
    return candidates, num_found, body


def _dense_search_result(
    candidate: _OpenSearchDenseCandidate,
    *,
    snapshot: OpenSearchIndexSnapshot,
    scope: KnowledgeSearchScope,
) -> SearchResult:
    item = candidate.item
    source_ref = str(item.get("source_ref", ""))
    capabilities = _projected_dense_capabilities(item)
    return SearchResult(
        id=str(item["id"]),
        source=str(item.get("path", source_ref)),
        source_ref=source_ref,
        knowledge_type=str(item.get("knowledge_type", "unknown")),
        content=str(item.get("content", "")),
        score=candidate.score,
        query_alignment_score=candidate.score,
        capabilities=capabilities,
        capability_sources={
            capability: "provider_classification" for capability in capabilities
        },
        matched_terms=(),
        matched_signals=(),
        reasons=("opensearch_dense_vector_similarity",),
        line_start=optional_int(item.get("line_start")),
        line_end=optional_int(item.get("line_end")),
        revision=snapshot.revision,
        checksum=optional_str(item.get("checksum")),
        source_type=str(item.get("source_type", "repository")),
        observations=structural_observations(
            item,
            source_ref=source_ref,
            revision=snapshot.revision,
        ),
        relationships=relationships(item),
        query_matches=(),
        metadata={
            "raw_opensearch_vector_score": candidate.raw_score,
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
    intent: SearchIntent,
    backend: OpenSearchBackend,
    started: float,
    scope: KnowledgeSearchScope,
    requirements: tuple[SearchRequirement, ...],
    snapshot: OpenSearchIndexSnapshot,
    embedder: EmbeddingProvider,
    serializer: ProjectionV3EmbeddingSerializer,
    query_text: str,
    lexical_candidates: tuple[_OpenSearchLexicalCandidate, ...],
    dense_candidates: tuple[_OpenSearchDenseCandidate, ...],
    lexical_num_found: int,
    dense_num_found: int,
    lexical_body: Mapping[str, object],
    dense_telemetry: Mapping[str, object],
    embedding_ms: int,
    dense_ms: int,
    lexical_ms: int,
) -> KnowledgeSearchResponse:
    fusion_started = backend.timer()
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
            else cast(_OpenSearchDenseCandidate, dense).item
        )
        fusion_candidates.append(
            FusionCandidate(
                candidate_id=candidate_id,
                source=str(source_item.get("path", source_item.get("source_ref", ""))),
                lexical=(None if lexical is None else FusionChannelSignal(
                    rank=lexical.rank,
                    normalized_score=lexical.score,
                    raw_score=lexical.raw_score,
                )),
                dense=(None if dense is None else FusionChannelSignal(
                    rank=dense.rank,
                    normalized_score=dense.score,
                    raw_score=dense.raw_score,
                )),
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
    fusion_ms = elapsed_ms(fusion_started, backend.timer())
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
        item = (
            lexical.item
            if lexical is not None
            else cast(_OpenSearchDenseCandidate, dense).item
        )
        source_ref = str(item.get("source_ref", ""))
        knowledge_type = str(item.get("knowledge_type", "unknown"))
        dense_capabilities = _projected_dense_capabilities(item)
        lexical_match = None if lexical is None else lexical.match
        lexical_capabilities = (
            ()
            if lexical_match is None
            else classify_capabilities(
                knowledge_type,
                lexical_match,
            )
        )
        capabilities = tuple(
            dict.fromkeys((*lexical_capabilities, *dense_capabilities))
        )
        normalized_rrf = fused_candidate.normalized_score
        matched_terms = (
            ()
            if lexical_match is None
            else lexical_match.matched_terms
        )
        matched_signals = (
            ()
            if lexical_match is None
            else lexical_match.matched_signals
        )
        query_matches = (
            ()
            if lexical_match is None
            else query_match_evidence(
                lexical_match,
                source_ref=source_ref,
                provenance="opensearch_projection_v3_match",
            )
        )
        candidate_order.append(
            {
                "id": candidate_id,
                "source": item.get("path"),
                "fused_rank": fused_rank,
                "lexical_rank": None if lexical is None else lexical.rank,
                "dense_rank": None if dense is None else dense.rank,
                "rrf_score": rrf_score,
                "rrf_lexical_contribution": lexical_contribution,
                "rrf_dense_contribution": dense_contribution,
                "returned": fused_rank <= scope.max_results,
            }
        )
        if fused_rank > scope.max_results:
            continue
        lexical_alignment = (
            None
            if lexical_match is None
            else lexical_match.alignment
        )
        results.append(
            SearchResult(
                id=candidate_id,
                source=str(item.get("path", source_ref)),
                source_ref=source_ref,
                knowledge_type=knowledge_type,
                content=str(item.get("content", "")),
                score=normalized_rrf,
                query_alignment_score=(
                    lexical_alignment
                    if lexical_alignment is not None
                    else cast(_OpenSearchDenseCandidate, dense).score
                ),
                capabilities=capabilities,
                capability_sources={
                    capability: "provider_classification"
                    for capability in capabilities
                },
                matched_terms=matched_terms,
                matched_signals=matched_signals,
                reasons=(
                        "opensearch_hybrid_rrf",
                        *(
                            []
                            if lexical_match is None
                            else [
                                f"opensearch_match:{channel}"
                                for channel in lexical_match.channels
                            ]
                        ),
                        *(
                            []
                            if dense is None
                            else ["opensearch_dense_vector_similarity"]
                        ),
                    ),
                line_start=optional_int(item.get("line_start")),
                line_end=optional_int(item.get("line_end")),
                revision=snapshot.revision,
                checksum=optional_str(item.get("checksum")),
                source_type=str(item.get("source_type", "repository")),
                observations=structural_observations(
                    item,
                    source_ref=source_ref,
                    revision=snapshot.revision,
                ),
                relationships=relationships(item),
                query_matches=query_matches,
                metadata={
                    "provider_relevance_score": normalized_rrf,
                    "fused_rank": fused_rank,
                    "lexical_rank": None if lexical is None else lexical.rank,
                    "dense_rank": None if dense is None else dense.rank,
                    "raw_opensearch_score": (
                        None if lexical is None else lexical.raw_score
                    ),
                    "raw_opensearch_vector_score": (
                        None if dense is None else dense.raw_score
                    ),
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
                        []
                        if lexical_match is None
                        else list(lexical_match.channels)
                    ),
                    "matched_query_signal_channels": (
                        []
                        if lexical_match is None
                        else list(lexical_match.signal_matches)
                    ),
                    "implementation_evidence": (
                        []
                        if lexical_match is None
                        else list(lexical_match.implementation_evidence)
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
    duration_ms = elapsed_ms(started, backend.timer())
    telemetry = RetrievalTelemetry(
        mode="hybrid",
        duration_ms=duration_ms,
        candidates_examined=len(fusion_result.candidates),
        candidates_returned=len(results),
        returned_chars=returned_chars(item.content for item in results),
        stages=(
            RetrievalStageTelemetry(name="embedding", duration_ms=embedding_ms),
            RetrievalStageTelemetry(
                name="lexical",
                duration_ms=lexical_ms,
                candidates_out=len(lexical_candidates),
            ),
            RetrievalStageTelemetry(
                name="dense",
                duration_ms=dense_ms,
                candidates_out=len(dense_candidates),
            ),
            RetrievalStageTelemetry(
                name="fusion",
                duration_ms=fusion_ms,
                candidates_in=len(fusion_candidates),
                candidates_out=len(fusion_result.candidates),
            ),
        ),
        fingerprints={
            **(
                {"projection": snapshot.projection_fingerprint}
                if snapshot.projection_fingerprint is not None
                else {}
            ),
            "embedding_serializer": serializer.serializer_fingerprint,
            "fusion_input": fusion_result.input_fingerprint,
            "fusion_output": fusion_result.output_fingerprint,
        },
        budget={
            "max_results": scope.max_results,
            "output_truncated": len(fusion_result.candidates) > scope.max_results,
        },
        metadata={
            "embedding_provider": embedder.provider_id,
            "embedding_model": embedder.model_id,
            "vector_dimensions": snapshot.vector_dimensions,
            "fusion_strategy": "rrf",
        },
    )
    return KnowledgeSearchResponse(
        backend=backend.backend_id,
        status="found" if results else "not_found",
        results=tuple(results),
        duration_ms=duration_ms,
        telemetry=telemetry,
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
            **translation_diagnostics(intent, scope, lexical=lexical_body, dense=query_text),
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
            "opensearch_lexical_query": lexical_body,
            "opensearch_dense_query": dict(dense_telemetry),
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
            "normalized_score_distribution": score_distribution(
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
    _, evidence = structural_candidate_evidence(document)
    if knowledge_type == "tests":
        return ("tests",)
    if knowledge_type == "code" and evidence:
        return ("implementation",)
    return ()


def _scope_filters(
    scope: KnowledgeSearchScope,
    snapshot: OpenSearchIndexSnapshot,
) -> list[dict[str, object]]:
    filters: list[dict[str, object]] = [
        {"term": {"record_kind": "knowledge"}},
        {"term": {"repository": snapshot.repository}},
    ]
    if scope.repository_paths:
        filters.append(
            {
                "bool": {
                    "minimum_should_match": 1,
                    "should": [
                        {
                            "match_phrase": {
                                "path_text": {
                                    "query": item.casefold(),
                                }
                            }
                        }
                        for item in scope.repository_paths
                    ],
                }
            }
        )
    if scope.knowledge_types:
        filters.append({"terms": {"knowledge_type": list(scope.knowledge_types)}})
    for field_name in ("language", "source_type"):
        raw = scope.settings.get(field_name)
        if raw is None:
            continue
        if isinstance(raw, str):
            values = [raw]
        elif isinstance(raw, (list, tuple)):
            values = [str(item) for item in raw if str(item).strip()]
        else:
            raise KnowledgeSearchError(
                f"OpenSearch filter {field_name!r} must be a string or list",
                category="schema_mismatch",
            )
        if values:
            filters.append({"terms": {field_name: values}})
    return filters


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
