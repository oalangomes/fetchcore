"""Dense and sparse-hybrid retrieval over canonical Projection v3 using Qdrant."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, TypeVar, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

from fetchcore.retrieval.translation import validate_intent_translation, translation_diagnostics

from fetchcore.retrieval.signals import QuerySignal
from fetchcore.retrieval.evidence import EvidenceObservation, QueryMatchEvidence
from fetchcore.retrieval.config import EmbeddingConfig
from fetchcore.retrieval.embedding import (
    VECTOR_EMBEDDING_SERIALIZATION_VERSION,
    EmbeddingProvider,
    ProjectionEmbeddingMode,
    ProjectionV3EmbeddingSerializer,
    validate_vector_dimensions as _validate_vector_dimensions,
)
from fetchcore.retrieval.hybrid import FusionObserver, HybridFusionCapture, allocate_fusion_input
from fetchcore.retrieval.fusion import (
    DEFAULT_RRF_K,
    FusionCandidate,
    FusionChannelSignal,
    FusionInput,
    FusionKernel,
    FusionRequest,
)
from fetchcore.retrieval.projection_matching import (
    DEFAULT_FIELD_BOOSTS,
    DEFAULT_SIGNAL_TYPE_BOOSTS,
    ValidatedProjectionV3Controls,
    match_projection_record,
    query_match_evidence,
)
from fetchcore.adapters.retrieval.qdrant.query import _qdrant_filter, _qdrant_query_points
from fetchcore.adapters.retrieval.qdrant.sparse import (
    SPARSE_ANALYZER_FINGERPRINT,
    SPARSE_ANALYZER_VERSION,
    SPARSE_MODIFIER,
    SPARSE_VECTOR_NAME,
    intent_sparse_vector,
    matched_sparse_terms,
    projection_sparse_vector,
)
from fetchcore.projection import (
    PROJECTION_VERSION,
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
    ProjectionError,
    corpus_checksum,
    projection_contract_fingerprint,
)
from fetchcore.projection.core import ProjectedKnowledgeRecord
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexResult,
    RepositoryIndexer,
)
from fetchcore.repository.index_fingerprint import indexing_contract_fingerprint
from fetchcore.retrieval import RetrievalBackendCapabilities
from fetchcore.telemetry import (
    IndexSyncTelemetry,
    RetrievalStageTelemetry,
    RetrievalTelemetry,
    elapsed_ms,
    returned_chars,
)
from fetchcore.retrieval.contracts import (
    KnowledgeSearchError,
    KnowledgeSearchResponse,
    SearchIntent,
    SearchRelationship,
    SearchRequirement,
    SearchResult,
    SearchScope,
)


_COLLECTION_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_EXECUTABLE_SYMBOL_TYPES = frozenset(
    {"class", "constructor", "function", "method", "procedure"}
)
_EXECUTABLE_PATTERNS = (
    re.compile(r"\b(?:async\s+)?function\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\("),
    re.compile(r"\bclass\s+[A-Za-z_$][A-Za-z0-9_$]*\b"),
    re.compile(r"(?m)^\s*(?:async\s+)?def\s+[A-Za-z_][A-Za-z0-9_]*\s*\("),
    re.compile(r"(?m)^\s*(?:func|fn)\s+[A-Za-z_][A-Za-z0-9_]*\s*\("),
)


@dataclass(frozen=True)
class QdrantVectorSettings:
    """Qdrant settings; legacy embedding fields are mapped by composition."""

    url: str = "http://localhost:6333"
    collection: str = "fetchcore-vector"
    timeout_seconds: float = 30.0
    projection_version: str = PROJECTION_VERSION
    embedding_serializer_mode: ProjectionEmbeddingMode = (
        ProjectionEmbeddingMode.PROJECTION_V3
    )
    embedding_url: str = "http://localhost:11434"
    embedding_model: str = "embeddinggemma"
    vector_dimensions: int | None = None
    batch_size: int = 32
    diagnostic_candidate_window: int | None = None
    api_key: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> QdrantVectorSettings:
        """Build the value from a mapping while validating its public contract."""
        data = dict(value or {})
        url = _absolute_http_url(str(data.get("url", cls.url)), "Qdrant")
        embedding_url = _absolute_http_url(
            str(data.get("embedding_url", cls.embedding_url)),
            "embedding",
        )
        collection = str(data.get("collection", cls.collection)).strip()
        if not _COLLECTION_PATTERN.fullmatch(collection):
            raise KnowledgeSearchError(
                "Qdrant collection prefix must use lowercase safe characters",
                category="schema_mismatch",
            )
        projection_version = str(
            data.get("projection_version", PROJECTION_VERSION)
        ).strip()
        if not projection_version:
            raise KnowledgeSearchError(
                "Qdrant projection_version must not be empty",
                category="schema_mismatch",
            )
        embedding_serializer_mode = _projection_embedding_mode(
            data.get(
                "embedding_serializer_mode",
                ProjectionEmbeddingMode.PROJECTION_V3,
            )
        )
        embedding_model = str(data.get("embedding_model", cls.embedding_model)).strip()
        if not embedding_model:
            raise KnowledgeSearchError(
                "Qdrant embedding_model must not be empty",
                category="schema_mismatch",
            )
        timeout_seconds = _positive_float(
            data.get("timeout_seconds", cls.timeout_seconds),
            "timeout_seconds",
        )
        batch_size = _positive_int(data.get("batch_size", cls.batch_size), "batch_size")
        if batch_size > 256:
            raise KnowledgeSearchError(
                "Qdrant vector batch_size must not exceed 256",
                category="schema_mismatch",
            )
        raw_dimensions = data.get("vector_dimensions")
        vector_dimensions = (
            None
            if raw_dimensions in (None, "")
            else _positive_int(raw_dimensions, "vector_dimensions")
        )
        raw_window = data.get("diagnostic_candidate_window")
        diagnostic_candidate_window = (
            None
            if raw_window in (None, "", "disabled")
            else _positive_int(raw_window, "diagnostic_candidate_window")
        )
        raw_api_key = data.get("api_key")
        api_key = None if raw_api_key is None else str(raw_api_key).strip() or None
        return cls(
            url=url,
            collection=collection,
            timeout_seconds=timeout_seconds,
            projection_version=projection_version,
            embedding_serializer_mode=embedding_serializer_mode,
            embedding_url=embedding_url,
            embedding_model=embedding_model,
            vector_dimensions=vector_dimensions,
            batch_size=batch_size,
            diagnostic_candidate_window=diagnostic_candidate_window,
            api_key=api_key,
        )


class QdrantTransport(Protocol):
    """Transport contract used by Qdrant vector search and index lifecycle operations."""
    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        """Execute a backend request and return the decoded response."""
        ...


class UrllibQdrantTransport:
    """Dependency-free Qdrant REST transport."""

    def __init__(self, settings: QdrantVectorSettings) -> None:
        """Initialize UrllibQdrantTransport with its explicit dependencies."""
        self.settings = settings

    def request(
        self,
        method: str,
        path: str,
        payload: object | None = None,
    ) -> dict[str, object]:
        """Execute a backend request and return the decoded response."""
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.settings.api_key:
            headers["api-key"] = self.settings.api_key
        body = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        request = Request(
            f"{self.settings.url}/{path.lstrip('/')}",
            data=body,
            method=method,
            headers=headers,
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
                f"Qdrant request failed with HTTP {exc.code}",
                category=category,
                metadata={"http_status": exc.code, "operation": path},
            ) from exc
        except TimeoutError as exc:
            raise KnowledgeSearchError(
                "Qdrant request timed out",
                category="timeout",
                metadata={"operation": path},
            ) from exc
        except URLError as exc:
            raise KnowledgeSearchError(
                "Qdrant is unavailable",
                category="unavailable",
                metadata={"operation": path},
            ) from exc
        return _json_object(raw, source="Qdrant", operation=path)


@dataclass(frozen=True)
class QdrantVectorSnapshot:
    """Persisted identity and embedding metadata for a Qdrant repository snapshot."""
    repository: str
    branch: str | None
    revision: str
    corpus_checksum: str
    document_count: int
    projection_version: str
    projection_fingerprint: str
    embedding_provider: str
    embedding_model: str
    vector_dimensions: int
    serializer_mode: ProjectionEmbeddingMode
    serializer_version: str
    serializer_fingerprint: str
    indexed_at: str
    sparse_vector_name: str | None = None
    sparse_analyzer_version: str | None = None
    sparse_analyzer_fingerprint: str | None = None
    sparse_modifier: str | None = None
    status: str = "ready"
    indexing_fingerprint: str | None = None
    active_collection: str | None = field(default=None, compare=False, repr=False)

    @property
    def point_id(self) -> str:
        """Return the deterministic persisted snapshot point identifier."""
        return _snapshot_point_id(self.repository)

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "id": self.point_id,
            "record_kind": "snapshot",
            "repository": self.repository,
            "branch": self.branch,
            "snapshot_revision": self.revision,
            "corpus_checksum": self.corpus_checksum,
            "document_count": self.document_count,
            "projection_version": self.projection_version,
            "projection_fingerprint": self.projection_fingerprint,
            "embedding_provider": self.embedding_provider,
            "embedding_model": self.embedding_model,
            "vector_dimensions": self.vector_dimensions,
            "embedding_serializer_mode": self.serializer_mode.value,
            "embedding_serializer_version": self.serializer_version,
            "embedding_serializer_fingerprint": self.serializer_fingerprint,
            "snapshot_indexed_at": self.indexed_at,
            "sparse_vector_name": self.sparse_vector_name,
            "sparse_analyzer_version": self.sparse_analyzer_version,
            "sparse_analyzer_fingerprint": self.sparse_analyzer_fingerprint,
            "sparse_modifier": self.sparse_modifier,
            "snapshot_status": self.status,
            "indexing_fingerprint": self.indexing_fingerprint,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> QdrantVectorSnapshot:
        """Build the value from a mapping while validating its public contract."""
        try:
            return cls(
                repository=str(value["repository"]),
                branch=None if value.get("branch") is None else str(value["branch"]),
                revision=str(value["snapshot_revision"]),
                corpus_checksum=str(value["corpus_checksum"]),
                document_count=int(str(value["document_count"])),
                projection_version=str(value["projection_version"]),
                projection_fingerprint=str(value["projection_fingerprint"]),
                embedding_provider=str(value["embedding_provider"]),
                embedding_model=str(value["embedding_model"]),
                vector_dimensions=int(str(value["vector_dimensions"])),
                serializer_mode=_projection_embedding_mode(
                    value["embedding_serializer_mode"]
                ),
                serializer_version=str(value["embedding_serializer_version"]),
                serializer_fingerprint=str(value["embedding_serializer_fingerprint"]),
                indexed_at=str(value["snapshot_indexed_at"]),
                sparse_vector_name=_optional_str(value.get("sparse_vector_name")),
                sparse_analyzer_version=_optional_str(value.get("sparse_analyzer_version")),
                sparse_analyzer_fingerprint=_optional_str(
                    value.get("sparse_analyzer_fingerprint")
                ),
                sparse_modifier=_optional_str(value.get("sparse_modifier")),
                status=str(value["snapshot_status"]),
                indexing_fingerprint=_optional_str(value.get("indexing_fingerprint")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise KnowledgeSearchError(
                "Qdrant vector snapshot record is invalid",
                category="response_parsing_failure",
            ) from exc


@dataclass(frozen=True)
class QdrantVectorIndexSyncResult:
    """Observable result of a Qdrant vector index synchronization."""
    mode: str
    snapshot: QdrantVectorSnapshot
    added: int
    duration_ms: int
    telemetry: IndexSyncTelemetry | None = None
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    embedding_vectors_reused: int = 0
    collection_creations: int = 0
    collection_deletions: int = 0
    alias_promotions: int = 0
    stage_durations_ms: tuple[tuple[str, int], ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        telemetry = (
            None if self.telemetry is None else self.telemetry.to_mapping()
        )
        if telemetry is not None:
            telemetry["embedding_vectors_reused"] = self.embedding_vectors_reused
            telemetry["collection_creations"] = self.collection_creations
            telemetry["collection_deletions"] = self.collection_deletions
            telemetry["alias_promotions"] = self.alias_promotions
            telemetry["stage_durations_ms"] = dict(self.stage_durations_ms)
        return {
            "mode": self.mode,
            "snapshot": self.snapshot.to_mapping(),
            "added": self.added,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "duration_ms": self.duration_ms,
            **({"telemetry": telemetry} if telemetry is not None else {}),
        }


@dataclass(frozen=True)
class QdrantVectorCollectionState:
    """Repository-scoped Qdrant collection inventory."""

    repository: str
    alias: str
    active_collection: str | None
    owned_collections: tuple[str, ...]
    orphaned_collections: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "repository": self.repository,
            "alias": self.alias,
            "active_collection": self.active_collection,
            "owned_collections": list(self.owned_collections),
            "orphaned_collections": list(self.orphaned_collections),
        }


@dataclass(frozen=True)
class QdrantVectorCleanupResult:
    """Result of deleting repository-scoped orphaned concrete collections."""

    repository: str
    alias: str
    active_collection: str | None
    deleted_collections: tuple[str, ...]
    remaining_orphaned_collections: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "repository": self.repository,
            "alias": self.alias,
            "active_collection": self.active_collection,
            "deleted_collections": list(self.deleted_collections),
            "remaining_orphaned_collections": list(
                self.remaining_orphaned_collections
            ),
        }


class QdrantVectorIndexStore:
    """Safe full-rebuild vector persistence using a repository-scoped alias."""

    def __init__(
        self,
        transport: QdrantTransport,
        settings: QdrantVectorSettings,
        embedder: EmbeddingProvider,
        *,
        clock: Callable[[], str],
        timer: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize QdrantVectorIndexStore with its explicit dependencies."""
        self.transport = transport
        self.settings = settings
        self.embedder = embedder
        self.compiler = ProjectionV3Compiler(settings.projection_version)
        self.reader = ProjectionV3CanonicalDocumentReader(settings.projection_version)
        self.serializer = ProjectionV3EmbeddingSerializer(
            settings.projection_version,
            settings.embedding_serializer_mode,
        )
        self.embedding_provider = embedder
        self.embedding_serializer = self.serializer
        self.projection_fingerprint = projection_contract_fingerprint(
            settings.projection_version
        )
        self.indexing_fingerprint: str | None = None
        self.repository: str | None = None
        self.clock = clock
        self.timer = timer

    def health(self) -> dict[str, object]:
        """Return backend health information required by the lifecycle."""
        self._validate_embedding_identity()
        qdrant = self.transport.request("GET", "collections")
        probe = self.embedder.embed(("fetchcore embedding probe",))
        dimensions = _validate_vector_dimensions(probe)
        return {
            "qdrant": qdrant,
            "embedding_provider": self.embedder.provider_id,
            "embedding_model": self.embedder.model_id,
            "vector_dimensions": dimensions,
        }

    @contextmanager
    def _writer_lock(self, repository: str) -> Iterator[None]:
        """Acquire an exclusive Qdrant-backed lock shared across processes."""
        collection = _writer_lock_collection(self.settings, repository)
        token = uuid.uuid4().hex
        try:
            self.transport.request(
                "PUT",
                f"collections/{quote(collection, safe='')}",
                {"vectors": {"size": 1, "distance": "Cosine"}},
            )
        except KnowledgeSearchError as exc:
            if exc.category == "schema_mismatch":
                raise KnowledgeSearchError(
                    "Qdrant writer lock is already held; inspect it before retry",
                    category="concurrent_write",
                    metadata={
                        "repository": repository,
                        "lock_collection": collection,
                        "recovery_required": True,
                    },
                ) from exc
            raise
        try:
            self._upsert_points(
                collection,
                [
                    {
                        "id": _writer_lock_point_id(repository),
                        "vector": {"": [1.0]},
                        "payload": {
                            "record_kind": "writer_lock",
                            "repository": repository,
                            "writer_token": token,
                            "started_at": self.clock(),
                        },
                    }
                ],
            )
        except Exception:
            try:
                self._delete_collection_if_present(collection)
            except Exception as cleanup_error:
                raise KnowledgeSearchError(
                    "Qdrant writer lock acquisition failed; inspect lock collection",
                    category="concurrent_write",
                    metadata={
                        "repository": repository,
                        "lock_collection": collection,
                        "recovery_required": True,
                    },
                ) from cleanup_error
            raise
        try:
            yield
        except BaseException:
            self._release_writer_lock(collection, repository, token)
            raise
        else:
            self._release_writer_lock(collection, repository, token)

    def _release_writer_lock(
        self,
        collection: str,
        repository: str,
        token: str,
    ) -> None:
        try:
            response = self.transport.request(
                "POST",
                f"collections/{quote(collection, safe='')}/points",
                {
                    "ids": [_writer_lock_point_id(repository)],
                    "with_payload": True,
                    "with_vector": False,
                },
            )
            payload = _single_point_payload(response, "Qdrant writer lock")
        except Exception as exc:
            raise KnowledgeSearchError(
                "Qdrant writer lock ownership could not be verified",
                category="concurrent_write",
                metadata={"repository": repository, "lock_collection": collection},
            ) from exc
        if (
            payload.get("record_kind") != "writer_lock"
            or payload.get("repository") != repository
            or payload.get("writer_token") != token
        ):
            raise KnowledgeSearchError(
                "Qdrant writer lock ownership changed",
                category="concurrent_write",
                metadata={"repository": repository, "lock_collection": collection},
            )
        self.transport.request(
            "DELETE",
            f"collections/{quote(collection, safe='')}",
        )

    def validate_schema(self, *, require_vectors: bool = False) -> None:
        """Validate the bound repository's dense and sparse schema without writes."""
        if not self.repository:
            raise KnowledgeSearchError(
                "Qdrant repository binding is missing",
                category="schema_mismatch",
            )
        alias = _repository_alias(self.settings, self.repository)
        collection = self._active_collection(alias)
        if collection is None:
            raise KnowledgeSearchError(
                "Qdrant repository alias is absent",
                category="index_missing",
                metadata={"repository": self.repository, "alias": alias},
            )
        self._validate_collection_schema(
            collection,
            expected_dimensions=self.settings.vector_dimensions,
        )

    def _validate_collection_schema(
        self,
        collection: str,
        *,
        expected_dimensions: int | None = None,
    ) -> int:
        response = self.transport.request(
            "GET",
            f"collections/{quote(collection, safe='')}",
        )
        result = response.get("result")
        config = result.get("config") if isinstance(result, Mapping) else None
        params = config.get("params") if isinstance(config, Mapping) else None
        vectors = params.get("vectors") if isinstance(params, Mapping) else None
        sparse_vectors = (
            params.get("sparse_vectors") if isinstance(params, Mapping) else None
        )
        dense = _dense_vector_config(vectors)
        if dense is None or dense.get("distance") != "Cosine":
            raise KnowledgeSearchError(
                "Qdrant collection has an incompatible dense-vector schema",
                category="schema_mismatch",
                metadata={"collection": collection, "rebuild_required": True},
            )
        dimensions = _positive_response_int(dense.get("size"), "dense vector size")
        if (
            expected_dimensions is not None
            and dimensions != expected_dimensions
        ):
            raise KnowledgeSearchError(
                "Qdrant collection vector dimensions do not match configuration",
                category="schema_mismatch",
                metadata={
                    "expected_dimensions": expected_dimensions,
                    "actual_dimensions": dimensions,
                    "rebuild_required": True,
                },
            )
        sparse = (
            sparse_vectors.get(SPARSE_VECTOR_NAME)
            if isinstance(sparse_vectors, Mapping)
            else None
        )
        if (
            not isinstance(sparse, Mapping)
            or sparse.get("modifier") != SPARSE_MODIFIER
        ):
            raise KnowledgeSearchError(
                "Qdrant collection has an incompatible sparse-vector schema",
                category="schema_mismatch",
                metadata={"collection": collection, "rebuild_required": True},
            )
        return dimensions

    def embedding_snapshot_matches(self, snapshot: QdrantVectorSnapshot) -> bool:
        """Return whether persisted vectors match the configured provider contract."""
        return (
            snapshot.embedding_provider == self.embedder.provider_id
            and snapshot.embedding_model == self.embedder.model_id
            and snapshot.embedding_model == self.settings.embedding_model
            and (
                self.settings.vector_dimensions is None
                or snapshot.vector_dimensions == self.settings.vector_dimensions
            )
            and snapshot.serializer_mode == self.settings.embedding_serializer_mode
            and snapshot.serializer_version == VECTOR_EMBEDDING_SERIALIZATION_VERSION
            and snapshot.serializer_fingerprint == self.serializer.serializer_fingerprint
            and snapshot.projection_version == self.settings.projection_version
            and snapshot.projection_fingerprint == self.projection_fingerprint
        )

    def load_documents(
        self,
        repository: str,
        *,
        collection: str | None = None,
    ) -> tuple[KnowledgeDocument, ...]:
        """Read the complete repository corpus from one stable collection."""
        active = collection or self._active_collection(
            _repository_alias(self.settings, repository)
        )
        if active is None:
            raise KnowledgeSearchError(
                "Qdrant repository collection is absent",
                category="snapshot_missing",
                metadata={"repository": repository},
            )
        documents: list[KnowledgeDocument] = []
        seen_ids: set[str] = set()
        for point in self._scroll_points(
            active,
            repository,
            with_vector=False,
        ):
            payload = cast(dict[str, object], point["payload"])
            try:
                document = self.reader.read(payload)
            except ProjectionError as exc:
                raise KnowledgeSearchError(
                    "Qdrant canonical document payload is incompatible",
                    category="schema_mismatch",
                    metadata={"repository": repository},
                ) from exc
            point_id = str(point["id"])
            if (
                document.repository != repository
                or point_id != _knowledge_point_id(document.id)
                or document.id in seen_ids
            ):
                raise KnowledgeSearchError(
                    "Qdrant repository document identity is inconsistent",
                    category="response_parsing_failure",
                    metadata={"repository": repository},
                )
            seen_ids.add(document.id)
            documents.append(document)
        return tuple(sorted(documents, key=lambda item: item.id))

    def _scroll_points(
        self,
        collection: str,
        repository: str,
        *,
        with_vector: bool,
    ) -> Iterable[dict[str, object]]:
        """Yield validated Qdrant point pages with an opaque pagination cursor."""
        offset: object | None = None
        seen_offsets: set[str] = set()
        while True:
            body: dict[str, object] = {
                "filter": {
                    "must": [
                        {"key": "record_kind", "match": {"value": "knowledge"}},
                        {"key": "repository", "match": {"value": repository}},
                    ]
                },
                "limit": self.settings.batch_size,
                "with_payload": True,
                "with_vector": with_vector,
            }
            if offset is not None:
                body["offset"] = offset
            response = self.transport.request(
                "POST",
                f"collections/{quote(collection, safe='')}/points/scroll",
                body,
            )
            result = response.get("result")
            if not isinstance(result, Mapping):
                raise KnowledgeSearchError(
                    "Qdrant scroll result is invalid",
                    category="response_parsing_failure",
                )
            points = result.get("points")
            if not isinstance(points, list) or len(points) > self.settings.batch_size:
                raise KnowledgeSearchError(
                    "Qdrant scroll page is invalid",
                    category="response_parsing_failure",
                )
            for raw_point in points:
                if not isinstance(raw_point, Mapping):
                    raise KnowledgeSearchError(
                        "Qdrant scroll returned an invalid point",
                        category="response_parsing_failure",
                    )
                raw_id = raw_point.get("id")
                raw_payload = raw_point.get("payload")
                raw_vector = raw_point.get("vector")
                if (
                    isinstance(raw_id, bool)
                    or not isinstance(raw_id, (str, int))
                    or not isinstance(raw_payload, Mapping)
                    or raw_payload.get("record_kind") != "knowledge"
                    or raw_payload.get("repository") != repository
                    or (with_vector and not isinstance(raw_vector, (Mapping, list)))
                ):
                    raise KnowledgeSearchError(
                        "Qdrant scroll point identity or shape is invalid",
                        category="response_parsing_failure",
                        metadata={"repository": repository},
                    )
                point: dict[str, object] = {
                    "id": str(raw_id),
                    "payload": dict(raw_payload),
                }
                if with_vector:
                    if isinstance(raw_vector, Mapping):
                        point["vector"] = dict(raw_vector)
                    elif isinstance(raw_vector, list):
                        point["vector"] = list(raw_vector)
                    else:
                        raise KnowledgeSearchError(
                            "Qdrant scroll point has no vector",
                            category="response_parsing_failure",
                        )
                yield point
            next_offset = result.get("next_page_offset")
            if next_offset is None:
                break
            if isinstance(next_offset, bool) or not isinstance(
                next_offset, (str, int)
            ):
                raise KnowledgeSearchError(
                    "Qdrant scroll cursor is invalid",
                    category="response_parsing_failure",
                )
            cursor_key = json.dumps(next_offset, separators=(",", ":"))
            if cursor_key in seen_offsets or not points:
                raise KnowledgeSearchError(
                    "Qdrant scroll cursor did not advance",
                    category="response_parsing_failure",
                )
            seen_offsets.add(cursor_key)
            offset = next_offset

    def _load_points_by_ids(
        self,
        collection: str,
        point_ids: Iterable[str],
        *,
        repository: str,
    ) -> dict[str, dict[str, object]]:
        identifiers = tuple(sorted(set(point_ids)))
        if not identifiers:
            return {}
        response = self.transport.request(
            "POST",
            f"collections/{quote(collection, safe='')}/points",
            {
                "ids": list(identifiers),
                "with_payload": True,
                "with_vector": True,
            },
        )
        result = response.get("result")
        if not isinstance(result, list):
            raise KnowledgeSearchError(
                "Qdrant point lookup response is invalid",
                category="response_parsing_failure",
            )
        points: dict[str, dict[str, object]] = {}
        for raw_point in result:
            if not isinstance(raw_point, Mapping):
                raise KnowledgeSearchError(
                    "Qdrant point lookup returned an invalid point",
                    category="response_parsing_failure",
                )
            raw_id = raw_point.get("id")
            payload = raw_point.get("payload")
            vector = raw_point.get("vector")
            if (
                isinstance(raw_id, bool)
                or not isinstance(raw_id, (str, int))
                or not isinstance(payload, Mapping)
                or payload.get("record_kind") != "knowledge"
                or payload.get("repository") != repository
                or not isinstance(vector, (Mapping, list))
            ):
                raise KnowledgeSearchError(
                    "Qdrant point lookup identity or shape is invalid",
                    category="response_parsing_failure",
                )
            point_id = str(raw_id)
            if point_id not in identifiers or point_id in points:
                raise KnowledgeSearchError(
                    "Qdrant point lookup returned an unexpected point",
                    category="response_parsing_failure",
                )
            points[point_id] = {
                "id": point_id,
                "payload": dict(payload),
                "vector": dict(vector) if isinstance(vector, Mapping) else list(vector),
            }
        return points

    def inspect_collections(self, repository: str) -> QdrantVectorCollectionState:
        """Inspect active and orphaned concrete collections for one repository."""

        alias = _repository_alias(self.settings, repository)
        active = self._active_collection(alias)
        owned = self._owned_collections(repository)
        orphaned = tuple(name for name in owned if name != active)
        return QdrantVectorCollectionState(
            repository=repository,
            alias=alias,
            active_collection=active,
            owned_collections=owned,
            orphaned_collections=orphaned,
        )

    def cleanup_orphans(self, repository: str) -> QdrantVectorCleanupResult:
        """Delete stale repository-owned generations without deleting the active one."""
        with self._writer_lock(repository):
            return self._cleanup_orphans(repository)

    def _cleanup_orphans(self, repository: str) -> QdrantVectorCleanupResult:
        initial = self.inspect_collections(repository)
        deleted: list[str] = []
        for collection in initial.orphaned_collections:
            # Re-resolve immediately before every destructive operation. A rebuild
            # may have switched the alias since the initial inventory was read.
            if self._active_collection(initial.alias) == collection:
                continue
            self._delete_collection_if_present(collection)
            deleted.append(collection)

        final = self.inspect_collections(repository)
        return QdrantVectorCleanupResult(
            repository=repository,
            alias=initial.alias,
            active_collection=final.active_collection,
            deleted_collections=tuple(deleted),
            remaining_orphaned_collections=final.orphaned_collections,
        )

    def load_snapshot(self, repository: str) -> QdrantVectorSnapshot:
        """Load and validate the active repository snapshot."""
        alias = _repository_alias(self.settings, repository)
        collection = self._active_collection(alias)
        if collection is None:
            raise KnowledgeSearchError(
                "Qdrant has no active vector snapshot",
                category="snapshot_missing",
                metadata={"repository": repository, "alias": alias},
            )
        try:
            snapshot = self._load_snapshot_from_collection(collection, repository)
        except KnowledgeSearchError as exc:
            if exc.category == "index_missing":
                raise KnowledgeSearchError(
                    "Qdrant has no active vector snapshot",
                    category="snapshot_missing",
                    metadata={"repository": repository},
                ) from exc
            raise
        if snapshot.status != "ready":
            raise KnowledgeSearchError(
                "Qdrant vector snapshot is not ready",
                category="index_stale",
            )
        return snapshot

    def _load_snapshot_from_collection(
        self,
        collection: str,
        repository: str,
        *,
        validate_contract: bool = True,
    ) -> QdrantVectorSnapshot:
        response = self.transport.request(
            "POST",
            f"collections/{quote(collection, safe='')}/points",
            {
                "ids": [_snapshot_point_id(repository)],
                "with_payload": True,
                "with_vector": False,
            },
        )
        points = response.get("result")
        if points == []:
            raise KnowledgeSearchError(
                "Qdrant collection has no repository snapshot",
                category="snapshot_missing",
                metadata={"repository": repository, "collection": collection},
            )
        snapshot = QdrantVectorSnapshot.from_mapping(
            _single_point_payload(response, "active Qdrant snapshot")
        )
        if snapshot.repository != repository:
            raise KnowledgeSearchError(
                "Qdrant snapshot repository identity does not match the request",
                category="snapshot_revision_mismatch",
                metadata={"repository": repository},
            )
        if validate_contract:
            self._ensure_snapshot_compatible(snapshot)
        return replace(snapshot, active_collection=collection)

    def full_rebuild(self, result: RepositoryIndexResult) -> QdrantVectorIndexSyncResult:
        """Embed and validate the first batch before acquiring a writer lock."""
        self._ensure_complete(result)
        self._validate_result(result)
        self._validate_embedding_identity()
        if not result.documents:
            raise KnowledgeSearchError(
                "cannot build an empty Qdrant vector corpus",
                category="empty_index",
            )
        self._assert_writer_unlocked(result.repository)
        alias = _repository_alias(self.settings, result.repository)
        self._validate_rebuild_target(
            result.repository,
            alias,
            self._active_collection(alias),
        )
        self._assert_writer_unlocked(result.repository)
        started = self.timer()
        first_documents = result.documents[: self.settings.batch_size]
        first_records = tuple(self.compiler.compile(item) for item in first_documents)
        embedding_started = self.timer()
        first_vectors = self.embedder.embed(
            tuple(self.serializer.serialize_record(item) for item in first_records)
        )
        first_embedding_ms = elapsed_ms(embedding_started, self.timer())
        if len(first_vectors) != len(first_records):
            raise KnowledgeSearchError(
                "embedding provider returned an incomplete batch",
                category="embedding_failed",
            )
        first_dimensions = _validate_vector_dimensions(first_vectors)
        self._validate_configured_dimensions(first_dimensions)
        lock_started = self.timer()
        with self._writer_lock(result.repository):
            lock_wait_ms = elapsed_ms(lock_started, self.timer())
            return self._full_rebuild(
                result,
                started=started,
                first_records=first_records,
                first_vectors=first_vectors,
                first_embedding_ms=first_embedding_ms,
                first_dimensions=first_dimensions,
                lock_wait_ms=lock_wait_ms,
            )

    def _full_rebuild(
        self,
        result: RepositoryIndexResult,
        *,
        started: float,
        first_records: tuple[ProjectedKnowledgeRecord, ...],
        first_vectors: tuple[tuple[float, ...], ...],
        first_embedding_ms: int,
        first_dimensions: int,
        lock_wait_ms: int,
    ) -> QdrantVectorIndexSyncResult:
        """Replace the repository projection with a complete rebuild."""
        self._ensure_complete(result)
        self._validate_result(result)
        self._validate_embedding_identity()
        indexed_at = self.clock()
        corpus = corpus_checksum(result.documents)
        alias = _repository_alias(self.settings, result.repository)
        concrete = _concrete_collection(
            self.settings,
            result.repository,
            result.revision,
            corpus,
            f"{indexed_at}:{uuid.uuid4().hex}",
        )
        prior = self._active_collection(alias)
        self._validate_rebuild_target(result.repository, alias, prior)

        dimensions: int | None = None
        inserted = 0
        embedding_requests = 1
        embedding_vectors = len(first_vectors)
        embedding_duration_ms = first_embedding_ms
        alias_switched = False
        staging_attempted = False
        collection_creation_ms = 0
        point_write_ms = 0
        snapshot_validation_ms = 0
        alias_promotion_ms = 0
        try:
            for batch_number, document_batch in enumerate(
                _batches(result.documents, self.settings.batch_size)
            ):
                if batch_number == 0:
                    records, vectors = first_records, first_vectors
                    observed_dimensions = first_dimensions
                else:
                    records = tuple(self.compiler.compile(item) for item in document_batch)
                    embedding_started = self.timer()
                    vectors = self.embedder.embed(
                        tuple(self.serializer.serialize_record(item) for item in records)
                    )
                    embedding_duration_ms += elapsed_ms(embedding_started, self.timer())
                    embedding_requests += 1
                    embedding_vectors += len(vectors)
                    if len(vectors) != len(records):
                        raise KnowledgeSearchError(
                            "embedding provider returned an incomplete batch",
                            category="embedding_failed",
                        )
                    observed_dimensions = _validate_vector_dimensions(vectors)
                if dimensions is None:
                    dimensions = observed_dimensions
                    self._validate_configured_dimensions(dimensions)
                    staging_attempted = True
                    create_started = self.timer()
                    self._create_collection(concrete, dimensions)
                    collection_creation_ms += elapsed_ms(create_started, self.timer())
                elif observed_dimensions != dimensions:
                    raise KnowledgeSearchError(
                        "embedding vector dimensions changed during rebuild",
                        category="schema_mismatch",
                    )
                points = [
                    {
                        "id": _knowledge_point_id(str(record.fields["id"])),
                        "vector": _qdrant_point_vectors(vector, record.to_mapping()),
                        "payload": record.to_mapping(),
                    }
                    for record, vector in zip(records, vectors, strict=True)
                ]
                write_started = self.timer()
                self.transport.request(
                    "PUT",
                    f"collections/{quote(concrete, safe='')}/points?wait=true",
                    {"points": points},
                )
                point_write_ms += elapsed_ms(write_started, self.timer())
                inserted += len(points)

            if dimensions is None:
                raise KnowledgeSearchError(
                    "Qdrant rebuild did not determine vector dimensions",
                    category="embedding_failed",
                )
            snapshot = self._snapshot(result, corpus, dimensions, indexed_at)
            write_started = self.timer()
            self._write_snapshot(concrete, snapshot)
            point_write_ms += elapsed_ms(write_started, self.timer())
            validation_started = self.timer()
            self._validate_staged(concrete, snapshot)
            snapshot_validation_ms += elapsed_ms(validation_started, self.timer())
            if self._active_collection(alias) != prior:
                raise KnowledgeSearchError(
                    "Qdrant alias changed while the rebuild was staged",
                    category="concurrent_update",
                    metadata={"repository": result.repository},
                )
            promotion_started = self.timer()
            self._switch_alias(alias, prior, concrete)
            alias_switched = True
            published = self.load_snapshot(result.repository)
            if (
                published != snapshot
                or published.active_collection != concrete
            ):
                raise KnowledgeSearchError(
                    "Qdrant alias did not expose the staged snapshot",
                    category="index_update_failure",
                )
            alias_promotion_ms += elapsed_ms(promotion_started, self.timer())
        except Exception as exc:
            try:
                active = self._active_collection(alias)
                if active == concrete:
                    self._switch_alias(alias, concrete, prior)
                elif active != prior:
                    raise KnowledgeSearchError(
                        "Qdrant alias changed during rebuild recovery",
                        category="concurrent_update",
                        metadata={"repository": result.repository},
                    )
            except Exception as rollback_error:
                raise KnowledgeSearchError(
                    "Qdrant rebuild failed and alias recovery could not be verified",
                    category="index_update_failure",
                    metadata={
                        "repository": result.repository,
                        "staged_collection": concrete,
                        "rollback_error": type(rollback_error).__name__,
                    },
                ) from rollback_error
            if alias_switched and self._active_collection(alias) != prior:
                raise KnowledgeSearchError(
                    "Qdrant rebuild rollback did not restore the prior alias",
                    category="index_update_failure",
                    metadata={"repository": result.repository},
                ) from exc
            if isinstance(exc, KnowledgeSearchError):
                metadata = dict(exc.metadata)
                metadata.update(
                    {
                        "repository": result.repository,
                        "prior_collection": prior,
                        "staged_collection": concrete if staging_attempted else None,
                        "recovery_required": (
                            staging_attempted or bool(metadata.get("recovery_required"))
                        ),
                    }
                )
                raise KnowledgeSearchError(
                    "Qdrant rebuild failed; inspect the active alias and staged generation",
                    category=exc.category,
                    metadata=metadata,
                ) from exc
            raise

        duration_ms = elapsed_ms(started, self.timer())
        telemetry = IndexSyncTelemetry(
            duration_ms=duration_ms,
            backend_writes=inserted + 1,
            backend_deletes=0,
            embedding_requests=embedding_requests,
            embedding_vectors=embedding_vectors,
            embedding_duration_ms=embedding_duration_ms,
        )
        return QdrantVectorIndexSyncResult(
            mode="full_rebuild",
            snapshot=replace(snapshot, active_collection=concrete),
            added=inserted,
            duration_ms=duration_ms,
            telemetry=telemetry,
            unchanged=0,
            collection_creations=2,
            collection_deletions=1,
            alias_promotions=1,
            stage_durations_ms=(
                ("embedding", embedding_duration_ms),
                ("writer_lock_wait", lock_wait_ms),
                ("collection_creation", collection_creation_ms),
                ("point_writes", point_write_ms),
                ("snapshot_validation", snapshot_validation_ms),
                ("alias_promotion", alias_promotion_ms),
                ("total", duration_ms),
            ),
        )

    def incremental_sync(
        self,
        result: RepositoryIndexResult,
        *,
        expected_snapshot: QdrantVectorSnapshot | None = None,
    ) -> QdrantVectorIndexSyncResult:
        """Stage a repository delta and publish it under the server writer lock."""
        return self._incremental_sync(result, expected_snapshot=expected_snapshot)

    def _incremental_sync(
        self,
        result: RepositoryIndexResult,
        *,
        expected_snapshot: QdrantVectorSnapshot | None,
    ) -> QdrantVectorIndexSyncResult:
        self._ensure_complete(result)
        self._validate_result(result)
        self._validate_embedding_identity()
        started = self.timer()
        snapshot = self.load_snapshot(result.repository)
        if expected_snapshot is not None and not _same_snapshot_generation(
            snapshot,
            expected_snapshot,
        ):
            raise KnowledgeSearchError(
                "Qdrant snapshot changed since the application observation",
                category="concurrent_write",
                metadata={"repository": result.repository},
            )
        if snapshot.indexing_fingerprint != self.indexing_fingerprint:
            raise KnowledgeSearchError(
                "Qdrant indexing contract changed; explicit rebuild required",
                category="rebuild_required",
                metadata={"reason": "indexing_contract_changed"},
            )
        if not self.embedding_snapshot_matches(snapshot):
            raise KnowledgeSearchError(
                "Qdrant embedding identity changed; explicit rebuild required",
                category="rebuild_required",
                metadata={"reason": "embedding_identity_changed"},
            )
        if result.repository != snapshot.repository:
            raise KnowledgeSearchError(
                "Qdrant repository binding differs from the active snapshot",
                category="snapshot_revision_mismatch",
            )
        alias = _repository_alias(self.settings, result.repository)
        active = snapshot.active_collection
        if active is None or self._active_collection(alias) != active:
            raise KnowledgeSearchError(
                "Qdrant active collection changed while loading its snapshot",
                category="concurrent_write",
                metadata={"repository": result.repository},
            )
        self._validate_collection_schema(
            active,
            expected_dimensions=snapshot.vector_dimensions,
        )
        previous_documents = self.load_documents(result.repository, collection=active)
        previous_checksum = corpus_checksum(previous_documents)
        if (
            len(previous_documents) != snapshot.document_count
            or previous_checksum != snapshot.corpus_checksum
        ):
            raise KnowledgeSearchError(
                "Qdrant active documents do not match the persisted snapshot",
                category="index_stale",
                metadata={"repository": result.repository, "rebuild_required": True},
            )
        current_checksum = corpus_checksum(result.documents)
        previous_by_id = {item.id: item for item in previous_documents}
        current_by_id = {item.id: item for item in result.documents}
        if (
            len(previous_by_id) != len(previous_documents)
            or len(current_by_id) != len(result.documents)
        ):
            raise KnowledgeSearchError(
                "Qdrant incremental result contains duplicate document identities",
                category="unsafe_snapshot",
            )
        if (
            snapshot.revision == result.revision
            and snapshot.branch == result.branch
            and snapshot.corpus_checksum == current_checksum
        ):
            if not _same_snapshot_generation(
                snapshot,
                self.load_snapshot(result.repository),
            ):
                raise KnowledgeSearchError(
                    "Qdrant snapshot changed during no-op",
                    category="concurrent_write",
                    metadata={"repository": result.repository},
                )
            duration_ms = elapsed_ms(started, self.timer())
            return QdrantVectorIndexSyncResult(
                mode="noop",
                snapshot=snapshot,
                added=0,
                updated=0,
                deleted=0,
                unchanged=len(result.documents),
                duration_ms=duration_ms,
                telemetry=IndexSyncTelemetry(
                    duration_ms=duration_ms,
                    backend_writes=0,
                    backend_deletes=0,
                ),
                embedding_vectors_reused=0,
            )

        self._assert_writer_unlocked(result.repository)
        previous_records = {
            document_id: self.compiler.compile(document)
            for document_id, document in previous_by_id.items()
        }
        current_records = {
            document_id: self.compiler.compile(document)
            for document_id, document in current_by_id.items()
        }
        previous_texts = {
            document_id: self.serializer.serialize_record(record)
            for document_id, record in previous_records.items()
        }
        current_texts = {
            document_id: self.serializer.serialize_record(record)
            for document_id, record in current_records.items()
        }
        added_ids = set(current_by_id) - set(previous_by_id)
        deleted_ids = set(previous_by_id) - set(current_by_id)
        updated_ids = {
            document_id
            for document_id in set(previous_by_id) & set(current_by_id)
            if (
                not _same_canonical_document(
                    previous_by_id[document_id],
                    current_by_id[document_id],
                )
                or previous_by_id[document_id].revision
                != current_by_id[document_id].revision
                or previous_by_id[document_id].branch
                != current_by_id[document_id].branch
            )
        }
        unchanged_ids = (
            set(previous_by_id) & set(current_by_id)
        ) - updated_ids
        changed_ids = added_ids | updated_ids
        if not changed_ids and snapshot.branch == result.branch:
            raise KnowledgeSearchError(
                "Qdrant content checksum changed without a document-level delta",
                category="unsafe_snapshot",
            )

        reusable_document_ids = {
            document_id
            for document_id in updated_ids
            if current_texts[document_id] == previous_texts[document_id]
        }
        previous_point_ids = {
            _knowledge_point_id(document_id)
            for document_id in reusable_document_ids
        }
        previous_points: dict[str, dict[str, object]] = {}
        for batch in _batches(
            tuple(sorted(previous_point_ids)),
            self.settings.batch_size,
        ):
            previous_points.update(
                self._load_points_by_ids(
                    active,
                    batch,
                    repository=result.repository,
                )
            )
        if len(previous_points) != len(previous_point_ids):
            raise KnowledgeSearchError(
                "Qdrant changed or removed points are missing from the active snapshot",
                category="index_stale",
                metadata={"repository": result.repository},
            )
        reusable_vectors: dict[str, tuple[float, ...]] = {}
        for point_id, point in previous_points.items():
            payload = cast(dict[str, object], point["payload"])
            document = self.reader.read(payload)
            expected = previous_records.get(document.id)
            if (
                expected is None
                or expected.to_mapping() != payload
                or point_id != _knowledge_point_id(document.id)
            ):
                raise KnowledgeSearchError(
                    "Qdrant point changed after its snapshot was loaded",
                    category="index_stale",
                    metadata={"repository": result.repository},
                )
            if (
                document.id in updated_ids
                and current_texts[document.id] == previous_texts[document.id]
            ):
                reusable_vectors[current_texts[document.id]] = _dense_point_vector(
                    point["vector"],
                    expected_dimensions=snapshot.vector_dimensions,
                )

        embedding_texts: dict[str, str] = {}
        for document_id in changed_ids:
            text = current_texts[document_id]
            if text not in reusable_vectors:
                embedding_texts.setdefault(text, text)
        generated_vectors: dict[str, tuple[float, ...]] = {}
        embedding_requests = 0
        embedding_vectors = 0
        embedding_duration_ms = 0
        for text_batch in _batches(
            tuple(embedding_texts),
            self.settings.batch_size,
        ):
            embedding_started = self.timer()
            vectors = self.embedder.embed(text_batch)
            embedding_duration_ms += elapsed_ms(embedding_started, self.timer())
            embedding_requests += 1
            if len(vectors) != len(text_batch):
                raise KnowledgeSearchError(
                    "embedding provider returned an incomplete incremental batch",
                    category="embedding_failed",
                )
            observed_dimensions = _validate_vector_dimensions(vectors)
            if observed_dimensions != snapshot.vector_dimensions:
                raise KnowledgeSearchError(
                    "Qdrant incremental embedding dimensions changed",
                    category="schema_mismatch",
                    metadata={
                        "expected_dimensions": snapshot.vector_dimensions,
                        "actual_dimensions": observed_dimensions,
                        "rebuild_required": True,
                    },
                )
            for text, vector in zip(text_batch, vectors, strict=True):
                generated_vectors[text] = vector
            embedding_vectors += len(vectors)

        indexed_at = self.clock()
        next_snapshot = self._snapshot(
            result,
            current_checksum,
            snapshot.vector_dimensions,
            indexed_at,
        )
        updates: list[dict[str, object]] = []
        for document_id in sorted(changed_ids):
            record = current_records[document_id]
            text = current_texts[document_id]
            point_vector = reusable_vectors.get(text)
            if point_vector is None:
                point_vector = generated_vectors.get(text)
            if point_vector is None:
                raise KnowledgeSearchError(
                    "Qdrant incremental vector was not prepared",
                    category="embedding_failed",
                )
            payload = record.to_mapping()
            updates.append(
                {
                    "id": _knowledge_point_id(document_id),
                    "vector": _qdrant_point_vectors(point_vector, payload),
                    "payload": payload,
                }
            )

        concrete = _concrete_collection(
            self.settings,
            result.repository,
            result.revision,
            current_checksum,
            f"{indexed_at}:{uuid.uuid4().hex}",
        )
        copied_points = 0
        deleted_points = 0
        upserted_points = 0
        staging_attempted = False
        lock_wait_ms = 0
        collection_creation_ms = 0
        copy_ms = 0
        deletion_ms = 0
        update_ms = 0
        snapshot_write_ms = 0
        snapshot_validation_ms = 0
        alias_promotion_ms = 0
        lock_started = self.timer()
        try:
            with self._writer_lock(result.repository):
                lock_wait_ms = elapsed_ms(lock_started, self.timer())
                if not _same_snapshot_generation(
                    snapshot,
                    self.load_snapshot(result.repository),
                ):
                    raise KnowledgeSearchError(
                        "Qdrant snapshot changed before incremental staging",
                        category="concurrent_write",
                        metadata={"repository": result.repository},
                    )
                if self._active_collection(alias) != active:
                    raise KnowledgeSearchError(
                        "Qdrant alias changed before incremental staging",
                        category="concurrent_write",
                        metadata={"repository": result.repository},
                    )
                staging_attempted = True
                create_started = self.timer()
                self._create_collection(concrete, snapshot.vector_dimensions)
                collection_creation_ms = elapsed_ms(create_started, self.timer())
                copy_batch: list[dict[str, object]] = []
                copied_ids: set[str] = set()
                copy_started = self.timer()
                for point in self._scroll_points(
                    active,
                    result.repository,
                    with_vector=True,
                ):
                    payload = cast(dict[str, object], point["payload"])
                    document = self.reader.read(payload)
                    prior_record = previous_records.get(document.id)
                    if (
                        prior_record is None
                        or prior_record.to_mapping() != payload
                        or point["id"] != _knowledge_point_id(document.id)
                        or document.id in copied_ids
                    ):
                        raise KnowledgeSearchError(
                            "Qdrant source point differs from its canonical snapshot",
                            category="index_stale",
                            metadata={"repository": result.repository},
                        )
                    copied_ids.add(document.id)
                    if document.id in updated_ids:
                        continue
                    copy_batch.append(point)
                    if len(copy_batch) == self.settings.batch_size:
                        self._upsert_points(concrete, copy_batch)
                        copied_points += len(copy_batch)
                        copy_batch = []
                if copy_batch:
                    self._upsert_points(concrete, copy_batch)
                    copied_points += len(copy_batch)
                if len(copied_ids) != len(previous_documents):
                    raise KnowledgeSearchError(
                        "Qdrant staging copy did not observe the full active corpus",
                        category="index_update_failure",
                        metadata={
                            "expected_document_count": len(previous_documents),
                            "copied_source_count": len(copied_ids),
                        },
                    )
                copy_ms = elapsed_ms(copy_started, self.timer())

                removed_point_ids = tuple(
                    _knowledge_point_id(document_id)
                    for document_id in sorted(deleted_ids)
                )
                delete_started = self.timer()
                for id_batch in _batches(
                    removed_point_ids,
                    self.settings.batch_size,
                ):
                    self._delete_points(concrete, id_batch)
                    deleted_points += len(id_batch)
                deletion_ms = elapsed_ms(delete_started, self.timer())

                upsert_batch: list[dict[str, object]] = []
                update_started = self.timer()
                for point in updates:
                    upsert_batch.append(point)
                    if len(upsert_batch) == self.settings.batch_size:
                        self._upsert_points(concrete, upsert_batch)
                        upserted_points += len(upsert_batch)
                        upsert_batch = []
                if upsert_batch:
                    self._upsert_points(concrete, upsert_batch)
                    upserted_points += len(upsert_batch)
                update_ms = elapsed_ms(update_started, self.timer())

                snapshot_write_started = self.timer()
                self._write_snapshot(concrete, next_snapshot)
                snapshot_write_ms = elapsed_ms(snapshot_write_started, self.timer())
                validation_started = self.timer()
                self._validate_staged(concrete, next_snapshot)
                snapshot_validation_ms = elapsed_ms(validation_started, self.timer())
                if (
                    self._active_collection(alias) != active
                    or not _same_snapshot_generation(
                        snapshot,
                        self.load_snapshot(result.repository),
                    )
                ):
                    raise KnowledgeSearchError(
                        "Qdrant alias changed before incremental promotion",
                        category="concurrent_write",
                        metadata={"repository": result.repository},
                    )
                try:
                    promotion_started = self.timer()
                    self._switch_alias(alias, active, concrete)
                    published = self.load_snapshot(result.repository)
                    if (
                        published != next_snapshot
                        or published.active_collection != concrete
                    ):
                        raise KnowledgeSearchError(
                            "Qdrant alias did not expose the verified incremental generation",
                            category="index_update_failure",
                        )
                    alias_promotion_ms = elapsed_ms(promotion_started, self.timer())
                except Exception as promotion_error:
                    observed = self._active_collection(alias)
                    if observed == concrete:
                        try:
                            self._switch_alias(alias, concrete, active)
                        except Exception as rollback_error:
                            raise KnowledgeSearchError(
                                "Qdrant promotion failed and alias rollback requires recovery",
                                category="index_update_failure",
                                metadata={
                                    "repository": result.repository,
                                    "prior_collection": active,
                                    "staged_collection": concrete,
                                    "rollback_error": type(rollback_error).__name__,
                                },
                            ) from rollback_error
                    elif observed != active:
                        raise KnowledgeSearchError(
                            "Qdrant alias changed during incremental recovery",
                            category="concurrent_write",
                            metadata={"repository": result.repository},
                        ) from promotion_error
                    raise
        except KnowledgeSearchError as exc:
            metadata = dict(exc.metadata)
            metadata.update(
                {
                    "repository": result.repository,
                    "prior_collection": active,
                    "staged_collection": concrete if staging_attempted else None,
                    "recovery_required": (
                        staging_attempted or bool(metadata.get("recovery_required"))
                    ),
                }
            )
            try:
                metadata["active_collection_after_failure"] = self._active_collection(alias)
            except KnowledgeSearchError as inspection_error:
                metadata["active_collection_after_failure"] = "unverified"
                metadata["alias_inspection_error"] = inspection_error.category
            raise KnowledgeSearchError(
                "Qdrant incremental sync failed; inspect active and staged generations",
                category=exc.category,
                metadata=metadata,
            ) from exc

        published = replace(next_snapshot, active_collection=concrete)
        duration_ms = elapsed_ms(started, self.timer())
        telemetry = IndexSyncTelemetry(
            duration_ms=duration_ms,
            backend_writes=copied_points + upserted_points + 1,
            backend_deletes=deleted_points,
            embedding_requests=embedding_requests,
            embedding_vectors=embedding_vectors,
            embedding_duration_ms=embedding_duration_ms,
        )
        return QdrantVectorIndexSyncResult(
            mode="incremental",
            snapshot=published,
            added=len(added_ids),
            updated=len(updated_ids),
            deleted=len(deleted_ids),
            unchanged=len(unchanged_ids),
            duration_ms=duration_ms,
            telemetry=telemetry,
            embedding_vectors_reused=len(result.documents) - embedding_vectors,
            collection_creations=2,
            collection_deletions=1,
            alias_promotions=1,
            stage_durations_ms=(
                ("planning", max(0, elapsed_ms(started, lock_started) - embedding_duration_ms)),
                ("embedding", embedding_duration_ms),
                ("writer_lock_wait", lock_wait_ms),
                ("collection_creation", collection_creation_ms),
                ("generation_copy", copy_ms),
                ("point_deletions", deletion_ms),
                ("point_upserts", update_ms),
                ("snapshot_write", snapshot_write_ms),
                ("snapshot_validation", snapshot_validation_ms),
                ("alias_promotion", alias_promotion_ms),
                ("total", duration_ms),
            ),
        )

    def _snapshot(
        self,
        result: RepositoryIndexResult,
        corpus: str,
        dimensions: int,
        indexed_at: str,
    ) -> QdrantVectorSnapshot:
        return QdrantVectorSnapshot(
            repository=result.repository,
            branch=result.branch,
            revision=result.revision,
            corpus_checksum=corpus,
            document_count=len(result.documents),
            projection_version=self.settings.projection_version,
            projection_fingerprint=self.projection_fingerprint,
            embedding_provider=self.embedder.provider_id,
            embedding_model=self.embedder.model_id,
            vector_dimensions=dimensions,
            serializer_mode=self.settings.embedding_serializer_mode,
            serializer_version=VECTOR_EMBEDDING_SERIALIZATION_VERSION,
            serializer_fingerprint=self.serializer.serializer_fingerprint,
            indexed_at=indexed_at,
            sparse_vector_name=SPARSE_VECTOR_NAME,
            sparse_analyzer_version=SPARSE_ANALYZER_VERSION,
            sparse_analyzer_fingerprint=SPARSE_ANALYZER_FINGERPRINT,
            sparse_modifier=SPARSE_MODIFIER,
            indexing_fingerprint=self.indexing_fingerprint,
        )

    def _write_snapshot(
        self,
        collection: str,
        snapshot: QdrantVectorSnapshot,
    ) -> None:
        control_vector = [1.0, *([0.0] * (snapshot.vector_dimensions - 1))]
        self._upsert_points(
            collection,
            [
                {
                    "id": snapshot.point_id,
                    "vector": {"": control_vector},
                    "payload": snapshot.to_mapping(),
                }
            ],
        )

    def _switch_alias(
        self,
        alias: str,
        expected: str | None,
        target: str | None,
    ) -> None:
        if self._active_collection(alias) != expected:
            raise KnowledgeSearchError(
                "Qdrant alias changed before atomic promotion",
                category="concurrent_update",
                metadata={"alias": alias},
            )
        actions: list[dict[str, object]] = []
        if expected is not None:
            actions.append({"delete_alias": {"alias_name": alias}})
        if target is not None:
            actions.append(
                {"create_alias": {"collection_name": target, "alias_name": alias}}
            )
        if actions:
            self.transport.request(
                "POST",
                "collections/aliases",
                {"actions": actions},
            )
        if self._active_collection(alias) != target:
            raise KnowledgeSearchError(
                "Qdrant alias promotion did not expose the requested collection",
                category="index_update_failure",
                metadata={"alias": alias},
            )

    def _upsert_points(
        self,
        collection: str,
        points: list[dict[str, object]],
    ) -> None:
        if points:
            self.transport.request(
                "PUT",
                f"collections/{quote(collection, safe='')}/points?wait=true",
                {"points": points},
            )

    def _delete_points(
        self,
        collection: str,
        point_ids: tuple[str, ...],
    ) -> None:
        if point_ids:
            self.transport.request(
                "POST",
                f"collections/{quote(collection, safe='')}/points/delete?wait=true",
                {"points": list(point_ids)},
            )

    @staticmethod
    def _validate_result(result: RepositoryIndexResult) -> None:
        if any(
            item.repository != result.repository or item.revision != result.revision
            for item in result.documents
        ):
            raise KnowledgeSearchError(
                "Qdrant indexing result contains foreign repository or revision data",
                category="unsafe_snapshot",
            )
        identifiers = [item.id for item in result.documents]
        if len(set(identifiers)) != len(identifiers):
            raise KnowledgeSearchError(
                "Qdrant indexing result contains duplicate document identities",
                category="unsafe_snapshot",
            )

    def _validate_configured_dimensions(self, dimensions: int) -> None:
        if (
            self.settings.vector_dimensions is not None
            and dimensions != self.settings.vector_dimensions
        ):
            raise KnowledgeSearchError(
                "Qdrant embedding dimensions do not match configuration",
                category="schema_mismatch",
                metadata={
                    "expected_dimensions": self.settings.vector_dimensions,
                    "actual_dimensions": dimensions,
                    "rebuild_required": True,
                },
            )

    def _validate_embedding_identity(self) -> None:
        if self.embedder.model_id != self.settings.embedding_model:
            raise KnowledgeSearchError(
                "Qdrant embedding model differs from its configured identity",
                category="schema_mismatch",
                metadata={
                    "configured_model": self.settings.embedding_model,
                    "provider_model": self.embedder.model_id,
                    "rebuild_required": True,
                },
            )

    def reset(self, repository: str) -> None:
        """Remove the active alias and every concrete collection owned by a repository."""
        with self._writer_lock(repository):
            self._reset(repository)

    def _reset(self, repository: str) -> None:
        state = self.inspect_collections(repository)
        if state.active_collection is not None:
            self.transport.request(
                "POST",
                "collections/aliases",
                {"actions": [{"delete_alias": {"alias_name": state.alias}}]},
            )
        for collection in self._owned_collections(repository):
            self._delete_collection_if_present(collection)

    def _create_collection(self, collection: str, dimensions: int) -> None:
        self.transport.request(
            "PUT",
            f"collections/{quote(collection, safe='')}",
            {
                "vectors": {"size": dimensions, "distance": "Cosine"},
                "sparse_vectors": {
                    SPARSE_VECTOR_NAME: {"modifier": SPARSE_MODIFIER}
                },
            },
        )

    def _validate_staged(
        self,
        collection: str,
        snapshot: QdrantVectorSnapshot,
    ) -> None:
        self._validate_collection_schema(
            collection,
            expected_dimensions=snapshot.vector_dimensions,
        )
        count = _qdrant_count(
            self.transport.request(
                "POST",
                f"collections/{quote(collection, safe='')}/points/count",
                {
                    "filter": {
                        "must": [
                            {"key": "record_kind", "match": {"value": "knowledge"}},
                            {
                                "key": "repository",
                                "match": {"value": snapshot.repository},
                            },
                        ]
                    },
                    "exact": True,
                },
            )
        )
        if count != snapshot.document_count:
            raise KnowledgeSearchError(
                "Qdrant staged vector document count is incomplete",
                category="index_update_failure",
                metadata={"expected": snapshot.document_count, "actual": count},
            )
        documents = self.load_documents(
            snapshot.repository,
            collection=collection,
        )
        if (
            len(documents) != snapshot.document_count
            or corpus_checksum(documents) != snapshot.corpus_checksum
        ):
            raise KnowledgeSearchError(
                "Qdrant staged documents do not match the requested snapshot",
                category="index_update_failure",
                metadata={"repository": snapshot.repository},
            )
        observed = self._load_snapshot_from_collection(
            collection,
            snapshot.repository,
        )
        if observed != snapshot:
            raise KnowledgeSearchError(
                "Qdrant staged vector snapshot does not match compiled corpus",
                category="index_update_failure",
            )

    def _validate_rebuild_target(
        self,
        repository: str,
        alias: str,
        active_collection: str | None,
    ) -> None:
        """Prove repository ownership before replacing an active generation."""
        if active_collection is None:
            orphaned = self._owned_collections(repository)
            if orphaned:
                raise KnowledgeSearchError(
                    "Qdrant repository alias is absent but owned-prefix collections exist",
                    category="schema_mismatch",
                    metadata={
                        "repository": repository,
                        "alias": alias,
                        "unaliased_collections": list(orphaned),
                    },
                )
            return
        if not active_collection.startswith(
            _repository_collection_prefix(self.settings, repository)
        ):
            raise KnowledgeSearchError(
                "Qdrant repository alias selects a collection outside its namespace",
                category="schema_mismatch",
                metadata={"repository": repository, "alias": alias},
            )
        try:
            self._load_snapshot_from_collection(
                active_collection,
                repository,
                validate_contract=False,
            )
        except KnowledgeSearchError as exc:
            if exc.category != "snapshot_missing":
                raise
            total = _qdrant_count(
                self.transport.request(
                    "POST",
                    f"collections/{quote(active_collection, safe='')}/points/count",
                    {"exact": True},
                )
            )
            if total == 0:
                return
            raise KnowledgeSearchError(
                "Qdrant aliased collection has points but no repository ownership snapshot",
                category="snapshot_missing",
                metadata={
                    "repository": repository,
                    "alias": alias,
                    "collection": active_collection,
                    "point_count": total,
                },
            ) from exc

    def _owned_collections(self, repository: str) -> tuple[str, ...]:
        prefix = _repository_collection_prefix(self.settings, repository)
        return tuple(
            name
            for name in self._collection_names()
            if name.startswith(prefix)
        )

    def _collection_names(self) -> tuple[str, ...]:
        response = self.transport.request("GET", "collections")
        result = response.get("result")
        collections = result.get("collections") if isinstance(result, Mapping) else None
        if not isinstance(collections, list):
            raise KnowledgeSearchError(
                "Qdrant collections response is invalid",
                category="response_parsing_failure",
            )
        names = {
            str(item.get("name"))
            for item in collections
            if isinstance(item, Mapping) and item.get("name")
        }
        return tuple(sorted(names))

    def _assert_writer_unlocked(self, repository: str) -> None:
        lock_name = _writer_lock_collection(self.settings, repository)
        if lock_name in self._collection_names():
            raise KnowledgeSearchError(
                "Qdrant writer lock is already held; inspect it before retry",
                category="concurrent_write",
                metadata={
                    "repository": repository,
                    "lock_collection": lock_name,
                    "recovery_required": True,
                },
            )

    def _active_collection(self, alias: str) -> str | None:
        payload = self.transport.request("GET", "aliases")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise KnowledgeSearchError(
                "Qdrant aliases response is invalid",
                category="response_parsing_failure",
            )
        aliases = result.get("aliases", [])
        if not isinstance(aliases, list):
            raise KnowledgeSearchError(
                "Qdrant aliases response is invalid",
                category="response_parsing_failure",
            )
        matches = [
            str(item.get("collection_name"))
            for item in aliases
            if isinstance(item, Mapping)
            and str(item.get("alias_name", "")) == alias
            and item.get("collection_name")
        ]
        if len(matches) > 1:
            raise KnowledgeSearchError(
                "Qdrant alias resolves to multiple collections",
                category="schema_mismatch",
            )
        return matches[0] if matches else None

    def _delete_collection_if_present(self, collection: str) -> None:
        try:
            self.transport.request(
                "DELETE",
                f"collections/{quote(collection, safe='')}",
            )
        except KnowledgeSearchError as exc:
            if exc.category != "index_missing":
                raise

    def _ensure_snapshot_compatible(self, snapshot: QdrantVectorSnapshot) -> None:
        expected = {
            "projection_version": self.settings.projection_version,
            "projection_fingerprint": self.projection_fingerprint,
            "embedding_provider": self.embedder.provider_id,
            "embedding_model": self.settings.embedding_model,
            "serializer_mode": self.settings.embedding_serializer_mode.value,
            "serializer_version": VECTOR_EMBEDDING_SERIALIZATION_VERSION,
            "serializer_fingerprint": self.serializer.serializer_fingerprint,
            "sparse_vector_name": SPARSE_VECTOR_NAME,
            "sparse_analyzer_version": SPARSE_ANALYZER_VERSION,
            "sparse_analyzer_fingerprint": SPARSE_ANALYZER_FINGERPRINT,
            "sparse_modifier": SPARSE_MODIFIER,
        }
        actual = {
            "projection_version": snapshot.projection_version,
            "projection_fingerprint": snapshot.projection_fingerprint,
            "embedding_provider": snapshot.embedding_provider,
            "embedding_model": snapshot.embedding_model,
            "serializer_mode": snapshot.serializer_mode.value,
            "serializer_version": snapshot.serializer_version,
            "serializer_fingerprint": snapshot.serializer_fingerprint,
            "sparse_vector_name": snapshot.sparse_vector_name,
            "sparse_analyzer_version": snapshot.sparse_analyzer_version,
            "sparse_analyzer_fingerprint": snapshot.sparse_analyzer_fingerprint,
            "sparse_modifier": snapshot.sparse_modifier,
        }
        dimensions_match = (
            self.settings.vector_dimensions is None
            or snapshot.vector_dimensions == self.settings.vector_dimensions
        )
        if actual == expected and snapshot.vector_dimensions > 0 and dimensions_match:
            return
        raise KnowledgeSearchError(
            "Qdrant vector snapshot contract is incompatible",
            category="schema_mismatch",
            metadata={
                "expected": expected,
                "actual": actual,
                "vector_dimensions": snapshot.vector_dimensions,
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
                "repository observation is incomplete; Qdrant projection was not changed",
                category="unsafe_snapshot",
            )


class QdrantVectorKnowledgeIndex:
    """Canonical RepositoryIndexer coordinator for the vector benchmark."""

    def __init__(self, store: QdrantVectorIndexStore, indexer: RepositoryIndexer) -> None:
        """Initialize QdrantVectorKnowledgeIndex with its explicit dependencies."""
        self.store = store
        self.indexer = indexer
        self.store.indexing_fingerprint = indexing_contract_fingerprint(indexer)

    def rebuild(self, workspace: Path) -> QdrantVectorIndexSyncResult:
        """Rebuild the repository projection from canonical source state."""
        return self.store.full_rebuild(self.indexer.index(workspace))

    def sync(self, workspace: Path) -> QdrantVectorIndexSyncResult:
        """Synchronize the repository projection with canonical source state."""
        repository = self.indexer.resolve_repository_identity(workspace)
        snapshot = self.store.load_snapshot(repository)
        if snapshot.active_collection is None:
            raise KnowledgeSearchError(
                "Qdrant snapshot was not pinned to a concrete collection",
                category="snapshot_missing",
                metadata={"repository": repository},
            )
        previous = self.store.load_documents(
            repository,
            collection=snapshot.active_collection,
        )
        result = self.indexer.index(workspace, previous_documents=previous)
        return self.store.incremental_sync(result, expected_snapshot=snapshot)


def _unconfigured_embedding_provider(
    settings: QdrantVectorSettings,
    embedding: EmbeddingConfig,
) -> EmbeddingProvider:
    """Fail explicitly when Qdrant was not wired by composition."""
    raise KnowledgeSearchError(
        "Qdrant vector retrieval requires a composed embedding provider",
        category="unsupported_capability",
        metadata={"backend": "qdrant-vector", "capability": f"embedding_provider:{embedding.provider}"},
    )


class QdrantVectorBackend:
    """Qdrant dense retrieval with an opt-in native sparse hybrid channel."""

    backend_id = "qdrant-vector"
    retrieval_capabilities = RetrievalBackendCapabilities(dense=True, hybrid=True, canonical_query=True)

    def __init__(
        self,
        *,
        transport_factory: Callable[
            [QdrantVectorSettings], QdrantTransport
        ] = UrllibQdrantTransport,
        embedding_factory: Callable[
            [QdrantVectorSettings, EmbeddingConfig], EmbeddingProvider
        ] = _unconfigured_embedding_provider,
        timer: Callable[[], float] = time.monotonic,
        fusion_observer: FusionObserver | None = None,
    ) -> None:
        """Initialize QdrantVectorBackend with its explicit dependencies."""
        self.transport_factory = transport_factory
        self.embedding_factory = embedding_factory
        self.timer = timer
        self.fusion_observer = fusion_observer

    def search(
        self,
        intent: SearchIntent,
        scope: SearchScope,
        requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        """Execute retrieval for the supplied intent, scope, and requirements."""
        mode = scope.retrieval.mode if scope.retrieval else "dense"
        channels = ("sparse", "dense") if mode == "hybrid" else (mode,)
        for channel in channels:
            validate_intent_translation(
                intent, channel=channel, capabilities=self.retrieval_capabilities,
            )
        started = self.timer()
        if scope.max_results < 1:
            raise KnowledgeSearchError(
                "Qdrant vector candidate budget must be positive",
                category="schema_mismatch",
            )
        if scope.retrieval is not None and not scope.retrieval.dense.enabled:
            raise KnowledgeSearchError(
                "Qdrant specialized backend does not support lexical-only retrieval",
                category="unsupported_capability",
                metadata={
                    "backend": self.backend_id,
                    "capability": "lexical",
                    "mode": "lexical_only",
                },
            )
        effective_settings = dict(scope.settings)
        if scope.retrieval is not None:
            effective_settings["embedding_model"] = scope.retrieval.dense.embedding.model
            if scope.retrieval.dense.embedding.dimensions is not None:
                effective_settings["vector_dimensions"] = (
                    scope.retrieval.dense.embedding.dimensions
                )
        settings = QdrantVectorSettings.from_mapping(effective_settings)
        if scope.retrieval is not None and scope.retrieval.hybrid.enabled:
            scope.retrieval.hybrid.validate_acquisition_budget(
                max(scope.max_results, settings.diagnostic_candidate_window or scope.max_results)
            )
        transport = self.transport_factory(settings)
        embedding = (
            scope.retrieval.dense.embedding
            if scope.retrieval is not None
            else EmbeddingConfig(model=settings.embedding_model)
        )
        embedder = self.embedding_factory(settings, embedding)
        if scope.retrieval is not None:
            requested_provider = scope.retrieval.dense.embedding.provider
            if embedder.provider_id != requested_provider:
                raise KnowledgeSearchError(
                    "configured embedding provider is not supported by Qdrant vector backend",
                    category="unsupported_capability",
                    metadata={
                        "backend": self.backend_id,
                        "capability": f"embedding_provider:{requested_provider}",
                    },
                )
        store = QdrantVectorIndexStore(
            transport,
            settings,
            embedder,
            clock=lambda: "",
            timer=self.timer,
        )
        repository = scope.repository or RepositoryIndexer().resolve_repository_identity(
            scope.workspace
        )
        snapshot = store.load_snapshot(repository)
        collection = snapshot.active_collection
        if collection is None:
            raise KnowledgeSearchError(
                "Qdrant snapshot was not pinned to a concrete collection",
                category="snapshot_missing",
                metadata={"repository": repository},
            )
        if scope.revision is not None and scope.revision != snapshot.revision:
            raise KnowledgeSearchError(
                "requested revision does not match the active Qdrant vector snapshot",
                category="snapshot_revision_mismatch",
                metadata={
                    "requested_revision": scope.revision,
                    "snapshot_revision": snapshot.revision,
                },
            )
        embedding_started = self.timer()
        query_text = store.serializer.serialize_query(intent)
        vectors = embedder.embed((query_text,))
        dimensions = _validate_vector_dimensions(vectors)
        embedding_ms = elapsed_ms(embedding_started, self.timer())
        if dimensions != snapshot.vector_dimensions:
            raise KnowledgeSearchError(
                "query embedding dimensions do not match the active Qdrant snapshot",
                category="schema_mismatch",
                metadata={
                    "expected_dimensions": snapshot.vector_dimensions,
                    "actual_dimensions": dimensions,
                    "rebuild_required": True,
                },
            )

        qdrant_filter = _qdrant_filter(scope, snapshot)
        pool_count = _qdrant_count(
            transport.request(
                "POST",
                f"collections/{quote(collection, safe='')}/points/count",
                {"filter": qdrant_filter, "exact": True},
            )
        )
        query_limit = max(
            scope.max_results,
            settings.diagnostic_candidate_window or scope.max_results,
        )
        if scope.retrieval is not None and scope.retrieval.hybrid.enabled:
            return _qdrant_hybrid_response(
                backend_id=self.backend_id,
                transport=transport,
                settings=settings,
                collection=collection,
                snapshot=snapshot,
                intent=intent,
                scope=scope,
                requirements=requirements,
                dense_vector=vectors[0],
                qdrant_filter=qdrant_filter,
                pool_count=pool_count,
                query_limit=query_limit,
                query_text=query_text,
                started=started,
                timer=self.timer,
                embedding_ms=embedding_ms,
                fusion_observer=self.fusion_observer,
            )

        dense_started = self.timer()
        raw_query = transport.request(
            "POST",
            f"collections/{quote(collection, safe='')}/points/query",
            {
                "query": list(vectors[0]),
                "filter": qdrant_filter,
                "limit": query_limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
        points = _qdrant_query_points(raw_query)
        dense_ms = elapsed_ms(dense_started, self.timer())

        results: list[SearchResult] = []
        for rank, point in enumerate(points[: scope.max_results], start=1):
            raw_payload = cast(dict[str, object], point["payload"])
            raw_score = _finite_float(point["score"], "Qdrant score")
            normalized = _normalize_cosine_score(raw_score)
            source_ref = str(raw_payload.get("source_ref", ""))
            knowledge_type = str(raw_payload.get("knowledge_type", "unknown"))
            capabilities = _projected_capabilities(raw_payload)
            results.append(
                SearchResult(
                    id=str(raw_payload["id"]),
                    source=str(raw_payload.get("path", source_ref)),
                    source_ref=source_ref,
                    knowledge_type=knowledge_type,
                    content=str(raw_payload.get("content", "")),
                    score=normalized,
                    query_alignment_score=normalized,
                    capabilities=capabilities,
                    capability_sources={
                        capability: "provider_classification"
                        for capability in capabilities
                    },
                    matched_terms=(),
                    matched_signals=(),
                    reasons=("qdrant_dense_vector_similarity",),
                    line_start=_optional_int(raw_payload.get("line_start")),
                    line_end=_optional_int(raw_payload.get("line_end")),
                    revision=snapshot.revision,
                    checksum=_optional_str(raw_payload.get("checksum")),
                    source_type=str(raw_payload.get("source_type", "repository")),
                    observations=_projection_observations(
                        raw_payload,
                        source_ref=source_ref,
                        revision=snapshot.revision,
                    ),
                    relationships=_projection_relationships(raw_payload),
                    query_matches=(),
                    metadata={
                        "raw_qdrant_score": raw_score,
                        "provider_relevance_score": normalized,
                        "pre_cutoff_rank": rank,
                        "embedding_provider": snapshot.embedding_provider,
                        "embedding_model": snapshot.embedding_model,
                        "vector_dimensions": snapshot.vector_dimensions,
                        "embedding_serializer_version": snapshot.serializer_version,
                        "embedding_serializer_mode": snapshot.serializer_mode.value,
                        "embedding_serializer_fingerprint": snapshot.serializer_fingerprint,
                        "projection_version": snapshot.projection_version,
                        "projection_fingerprint": snapshot.projection_fingerprint,
                        "vector_only": True,
                        "lexical_fallback": False,
                        "sparse_retrieval": False,
                        "hybrid_fusion": False,
                        "reranker": False,
                    },
                )
            )

        metadata: dict[str, object] = {
            "backend": self.backend_id,
            "repository": snapshot.repository,
            "snapshot_revision": snapshot.revision,
            "snapshot_status": snapshot.status,
            "projection_version": snapshot.projection_version,
            "projection_fingerprint": snapshot.projection_fingerprint,
            "embedding_provider": snapshot.embedding_provider,
            "embedding_model": snapshot.embedding_model,
            "vector_dimensions": snapshot.vector_dimensions,
            "embedding_serializer_version": snapshot.serializer_version,
            "embedding_serializer_mode": snapshot.serializer_mode.value,
            "embedding_serializer_fingerprint": snapshot.serializer_fingerprint,
            "retrieval_mode": "dense_vector_only",
            **translation_diagnostics(intent, scope, dense=query_text),
            "lexical_fallback": False,
            "sparse_retrieval": False,
            "hybrid_fusion": False,
            "reranker": False,
            "candidates_found": pool_count,
            "candidates_considered": len(points),
            "candidates_returned": len(results),
            "output_limit": scope.max_results,
            "output_truncated": pool_count > scope.max_results,
            "search_requirements": [item.to_mapping() for item in requirements],
            "query_embedding_text_sha256": hashlib.sha256(
                query_text.encode("utf-8")
            ).hexdigest(),
            "score_normalization": "clamp_cosine_to_0_1",
            "normalized_score_distribution": _score_distribution(
                [item.score for item in results]
            ),
        }
        if settings.diagnostic_candidate_window is not None:
            metadata["diagnostic_candidate_window"] = (
                settings.diagnostic_candidate_window
            )
            candidate_order = [
                {
                    "id": str(cast(dict[str, object], point["payload"]).get("id", "")),
                    "source": str(
                        cast(dict[str, object], point["payload"]).get("path", "")
                    ),
                    "pre_cutoff_rank": rank,
                    "score": _normalize_cosine_score(
                        _finite_float(point["score"], "Qdrant score")
                    ),
                    "raw_score": point["score"],
                    "normalized_diagnostic_score": _normalize_cosine_score(
                        _finite_float(point["score"], "Qdrant score")
                    ),
                    "returned": rank <= scope.max_results,
                }
                for rank, point in enumerate(points, start=1)
            ]
            metadata["candidate_order_before_cutoff"] = candidate_order
            metadata["diagnostic_candidate_order"] = candidate_order
        else:
            metadata["diagnostic_candidate_window"] = "disabled"

        duration_ms = elapsed_ms(started, self.timer())
        telemetry = RetrievalTelemetry(
            mode="dense",
            duration_ms=duration_ms,
            candidates_examined=len(points),
            candidates_returned=len(results),
            returned_chars=returned_chars(item.content for item in results),
            stages=(
                RetrievalStageTelemetry(name="embedding", duration_ms=embedding_ms),
                RetrievalStageTelemetry(
                    name="dense",
                    duration_ms=dense_ms,
                    candidates_out=len(points),
                ),
            ),
            fingerprints={
                **(
                    {"projection": snapshot.projection_fingerprint}
                    if snapshot.projection_fingerprint is not None
                    else {}
                ),
                "embedding_serializer": snapshot.serializer_fingerprint,
            },
            budget={
                "max_results": scope.max_results,
                "output_truncated": pool_count > scope.max_results,
            },
            metadata={
                "embedding_provider": snapshot.embedding_provider,
                "embedding_model": snapshot.embedding_model,
                "vector_dimensions": snapshot.vector_dimensions,
            },
        )
        return KnowledgeSearchResponse(
            backend=self.backend_id,
            status="found" if results else "not_found",
            results=tuple(results),
            metadata=metadata,
            duration_ms=duration_ms,
            telemetry=telemetry,
        )



@dataclass(frozen=True)
class _QdrantHybridChannelCandidate:
    rank: int
    raw_score: float
    normalized_score: float
    payload: dict[str, object]


def _qdrant_point_vectors(
    dense_vector: tuple[float, ...],
    document: Mapping[str, object],
) -> dict[str, object]:
    sparse = projection_sparse_vector(document)
    vectors: dict[str, object] = {"": list(dense_vector)}
    if sparse.indices:
        vectors[SPARSE_VECTOR_NAME] = sparse.to_qdrant()
    return vectors


def _qdrant_hybrid_response(
    *,
    backend_id: str,
    transport: QdrantTransport,
    settings: QdrantVectorSettings,
    collection: str,
    snapshot: QdrantVectorSnapshot,
    intent: SearchIntent,
    scope: SearchScope,
    requirements: tuple[SearchRequirement, ...],
    dense_vector: tuple[float, ...],
    qdrant_filter: dict[str, object],
    pool_count: int,
    query_limit: int,
    query_text: str,
    started: float,
    timer: Callable[[], float],
    embedding_ms: int,
    fusion_observer: FusionObserver | None = None,
) -> KnowledgeSearchResponse:
    expected_sparse = {
        "sparse_vector_name": SPARSE_VECTOR_NAME,
        "sparse_analyzer_version": SPARSE_ANALYZER_VERSION,
        "sparse_analyzer_fingerprint": SPARSE_ANALYZER_FINGERPRINT,
        "sparse_modifier": SPARSE_MODIFIER,
    }
    actual_sparse = {
        "sparse_vector_name": snapshot.sparse_vector_name,
        "sparse_analyzer_version": snapshot.sparse_analyzer_version,
        "sparse_analyzer_fingerprint": snapshot.sparse_analyzer_fingerprint,
        "sparse_modifier": snapshot.sparse_modifier,
    }
    if actual_sparse != expected_sparse:
        raise KnowledgeSearchError(
            "Qdrant snapshot does not contain the required sparse lexical contract",
            category="schema_mismatch",
            metadata={
                "expected": expected_sparse,
                "actual": actual_sparse,
                "rebuild_required": True,
            },
        )

    sparse_query = intent_sparse_vector(intent)
    if not sparse_query.indices:
        raise KnowledgeSearchError(
            "Qdrant hybrid retrieval requires at least one deterministic sparse query term",
            category="schema_mismatch",
        )
    dense_started = timer()
    dense_points = _qdrant_query_points(
        transport.request(
            "POST",
            f"collections/{quote(collection, safe='')}/points/query",
            {
                "query": list(dense_vector),
                "filter": qdrant_filter,
                "limit": query_limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
    )
    dense_ms = elapsed_ms(dense_started, timer())
    sparse_started = timer()
    sparse_points = _qdrant_query_points(
        transport.request(
            "POST",
            f"collections/{quote(collection, safe='')}/points/query",
            {
                "query": sparse_query.to_qdrant(),
                "using": SPARSE_VECTOR_NAME,
                "filter": qdrant_filter,
                "limit": query_limit,
                "with_payload": True,
                "with_vector": False,
            },
        )
    )
    sparse_ms = elapsed_ms(sparse_started, timer())
    fusion_started = timer()

    dense_by_id: dict[str, _QdrantHybridChannelCandidate] = {}
    for rank, point in enumerate(dense_points, start=1):
        payload = cast(dict[str, object], point["payload"])
        raw_score = _finite_float(point["score"], "Qdrant dense score")
        candidate_id = str(payload["id"])
        dense_by_id[candidate_id] = _QdrantHybridChannelCandidate(
            rank=rank,
            raw_score=raw_score,
            normalized_score=_normalize_cosine_score(raw_score),
            payload=payload,
        )

    eligible_sparse: list[tuple[int, dict[str, object], float, tuple[str, ...]]] = []
    for rank, point in enumerate(sparse_points, start=1):
        payload = cast(dict[str, object], point["payload"])
        overlap = matched_sparse_terms(intent, payload)
        if not overlap:
            continue
        raw_score = _finite_float(point["score"], "Qdrant sparse score")
        eligible_sparse.append((rank, payload, raw_score, overlap))
    sparse_max = max((max(0.0, item[2]) for item in eligible_sparse), default=0.0)
    sparse_by_id: dict[str, _QdrantHybridChannelCandidate] = {}
    sparse_terms_by_id: dict[str, tuple[str, ...]] = {}
    for rank, payload, raw_score, overlap in eligible_sparse:
        candidate_id = str(payload["id"])
        normalized = (
            0.0
            if sparse_max <= 0.0
            else max(0.0, min(1.0, raw_score / sparse_max))
        )
        sparse_by_id[candidate_id] = _QdrantHybridChannelCandidate(
            rank=rank,
            raw_score=raw_score,
            normalized_score=normalized,
            payload=payload,
        )
        sparse_terms_by_id[candidate_id] = overlap

    fusion_candidates: list[FusionCandidate] = []
    for candidate_id in sorted(set(dense_by_id) | set(sparse_by_id)):
        dense = dense_by_id.get(candidate_id)
        sparse = sparse_by_id.get(candidate_id)
        source_payload = (
            sparse.payload
            if sparse is not None
            else cast(_QdrantHybridChannelCandidate, dense).payload
        )
        fusion_candidates.append(
            FusionCandidate(
                candidate_id=candidate_id,
                source=str(source_payload.get("path", source_payload.get("source_ref", ""))),
                lexical=(
                    None
                    if sparse is None
                    else FusionChannelSignal(
                        rank=sparse.rank,
                        normalized_score=sparse.normalized_score,
                        raw_score=sparse.raw_score,
                    )
                ),
                dense=(
                    None
                    if dense is None
                    else FusionChannelSignal(
                        rank=dense.rank,
                        normalized_score=dense.normalized_score,
                        raw_score=dense.raw_score,
                    )
                ),
            )
        )
    hybrid = scope.retrieval.hybrid if scope.retrieval is not None else None
    lexical_window = (hybrid.lexical_fusion_window if hybrid else None) or query_limit
    dense_window = (hybrid.dense_fusion_window if hybrid else None) or query_limit
    rrf_k = hybrid.rrf_k if hybrid else DEFAULT_RRF_K
    lexical_signals = tuple(sorted(
        (item for item in fusion_candidates if item.lexical is not None),
        key=lambda item: item.lexical.rank if item.lexical is not None else 0,
    ))
    dense_signals = tuple(sorted(
        (item for item in fusion_candidates if item.dense is not None),
        key=lambda item: item.dense.rank if item.dense is not None else 0,
    ))
    if fusion_observer is not None:
        full_input = FusionInput(tuple(fusion_candidates), scope.max_results)
        evidence = []
        for candidate in fusion_candidates:
            acquired = sparse_by_id.get(candidate.candidate_id) or dense_by_id[candidate.candidate_id]
            record = acquired.payload
            evidence.append(SearchResult(
                id=candidate.candidate_id,
                source=str(record.get("path", record.get("source_ref", ""))),
                source_ref=str(record.get("source_ref", "")),
                knowledge_type=str(record.get("knowledge_type", "unknown")),
                content=str(record.get("content", "")),
                score=(candidate.lexical.normalized_score if candidate.lexical is not None
                       else cast(FusionChannelSignal, candidate.dense).normalized_score),
                line_start=_optional_int(record.get("line_start")),
                line_end=_optional_int(record.get("line_end")),
                revision=snapshot.revision,
                checksum=_optional_str(record.get("checksum")),
                source_type=str(record.get("source_type", "repository")),
                observations=_projection_observations(record, source_ref=str(record.get("source_ref", "")), revision=snapshot.revision),
                relationships=_projection_relationships(record),
                metadata={key: record[key] for key in ("chunk_ref", "source_checksum", "document_revision", "branch") if key in record},
            ))
        fusion_observer(HybridFusionCapture(
            full_input, tuple(evidence), len(sparse_points), len(dense_points),
        ))
    fusion_input = allocate_fusion_input(
        lexical=lexical_signals, dense=dense_signals,
        lexical_window=lexical_window, dense_window=dense_window,
        max_results=scope.max_results,
    )
    admitted_lexical = {item.candidate_id for item in fusion_input.candidates if item.lexical is not None}
    admitted_dense = {item.candidate_id for item in fusion_input.candidates if item.dense is not None}
    sparse_by_id = {key: value for key, value in sparse_by_id.items() if key in admitted_lexical}
    dense_by_id = {key: value for key, value in dense_by_id.items() if key in admitted_dense}
    fusion_result = FusionKernel().fuse(
        FusionRequest(
            input=fusion_input,
            strategy="rrf",
            parameters={"k": rrf_k},
        )
    )
    fusion_ms = elapsed_ms(fusion_started, timer())
    controls = ValidatedProjectionV3Controls(
        projection_version=snapshot.projection_version,
        field_boosts=dict(DEFAULT_FIELD_BOOSTS),
        signal_type_boosts=dict(DEFAULT_SIGNAL_TYPE_BOOSTS),
        diagnostic_candidate_window=settings.diagnostic_candidate_window,
    )

    results: list[SearchResult] = []
    candidate_order: list[dict[str, object]] = []
    for fused in fusion_result.candidates:
        dense = dense_by_id.get(fused.candidate_id)
        sparse = sparse_by_id.get(fused.candidate_id)
        source_candidate = (
            sparse
            if sparse is not None
            else cast(_QdrantHybridChannelCandidate, dense)
        )
        payload = source_candidate.payload
        source_ref = str(payload.get("source_ref", ""))
        sparse_terms = sparse_terms_by_id.get(fused.candidate_id, ()) if sparse is not None else ()
        candidate_order.append(
            {
                "id": fused.candidate_id,
                "source": str(payload.get("path", source_ref)),
                "fused_rank": fused.fused_rank,
                "sparse_rank": fused.lexical_rank,
                "lexical_rank": fused.lexical_rank,
                "dense_rank": fused.dense_rank,
                "rrf_score": fused.fused_score,
                "rrf_sparse_contribution": fused.lexical_contribution,
                "rrf_dense_contribution": fused.dense_contribution,
                "returned": fused.returned,
            }
        )
        if not fused.returned:
            continue

        if sparse is None:
            matched_terms: tuple[str, ...] = ()
            matched_signals = cast(tuple[QuerySignal, ...], ())
            query_matches = cast(tuple[QueryMatchEvidence, ...], ())
            alignment = (
                dense.normalized_score
                if dense is not None
                else fused.normalized_score
            )
        else:
            match = match_projection_record(intent, payload, controls)
            matched_terms = match.matched_terms
            matched_signals = match.matched_signals
            query_matches = query_match_evidence(
                match,
                source_ref=source_ref,
                provenance="qdrant_sparse_projection_v3_match",
            )
            alignment = match.alignment

        capabilities = _projected_capabilities(payload)
        reasons = (
                *([] if sparse is None else ["qdrant_sparse_lexical_similarity"]),
                *([] if dense is None else ["qdrant_dense_vector_similarity"]),
                "qdrant_hybrid_rrf",
            )
        results.append(
            SearchResult(
                id=fused.candidate_id,
                source=str(payload.get("path", source_ref)),
                source_ref=source_ref,
                knowledge_type=str(payload.get("knowledge_type", "unknown")),
                content=str(payload.get("content", "")),
                score=fused.normalized_score,
                query_alignment_score=alignment,
                capabilities=capabilities,
                capability_sources={
                    capability: "provider_classification" for capability in capabilities
                },
                matched_terms=matched_terms,
                matched_signals=matched_signals,
                reasons=reasons,
                line_start=_optional_int(payload.get("line_start")),
                line_end=_optional_int(payload.get("line_end")),
                revision=snapshot.revision,
                checksum=_optional_str(payload.get("checksum")),
                source_type=str(payload.get("source_type", "repository")),
                observations=_projection_observations(
                    payload,
                    source_ref=source_ref,
                    revision=snapshot.revision,
                ),
                relationships=_projection_relationships(payload),
                query_matches=query_matches,
                metadata={
                    "provider_relevance_score": fused.normalized_score,
                    "pre_cutoff_rank": fused.fused_rank,
                    "fused_rank": fused.fused_rank,
                    "sparse_rank": fused.lexical_rank,
                    "lexical_rank": fused.lexical_rank,
                    "dense_rank": fused.dense_rank,
                    "raw_qdrant_sparse_score": fused.lexical_raw_score,
                    "raw_qdrant_dense_score": fused.dense_raw_score,
                    "rrf_score": fused.fused_score,
                    "rrf_sparse_contribution": fused.lexical_contribution,
                    "rrf_dense_contribution": fused.dense_contribution,
                    "sparse_matched_terms": list(sparse_terms),
                    "embedding_provider": snapshot.embedding_provider,
                    "embedding_model": snapshot.embedding_model,
                    "vector_dimensions": snapshot.vector_dimensions,
                    "embedding_serializer_version": snapshot.serializer_version,
                    "embedding_serializer_mode": snapshot.serializer_mode.value,
                    "embedding_serializer_fingerprint": snapshot.serializer_fingerprint,
                    "projection_version": snapshot.projection_version,
                    "projection_fingerprint": snapshot.projection_fingerprint,
                    "vector_only": False,
                    "lexical_fallback": False,
                    "sparse_retrieval": sparse is not None,
                    "hybrid_fusion": True,
                    "reranker": False,
                },
            )
        )

    metadata: dict[str, object] = {
        "backend": backend_id,
        "repository": snapshot.repository,
        "snapshot_revision": snapshot.revision,
        "snapshot_status": snapshot.status,
        "projection_version": snapshot.projection_version,
        "projection_fingerprint": snapshot.projection_fingerprint,
        "embedding_provider": snapshot.embedding_provider,
        "embedding_model": snapshot.embedding_model,
        "vector_dimensions": snapshot.vector_dimensions,
        "embedding_serializer_version": snapshot.serializer_version,
        "embedding_serializer_mode": snapshot.serializer_mode.value,
        "embedding_serializer_fingerprint": snapshot.serializer_fingerprint,
        "retrieval_mode": "hybrid",
        **translation_diagnostics(intent, scope, dense=query_text, sparse=list(sparse_query.terms)),
        "lexical_fallback": False,
        "sparse_retrieval": True,
        "hybrid_fusion": True,
        "fusion_strategy": "rrf",
        "rrf_k": rrf_k,
        "fusion_parameters": dict(fusion_result.parameters),
        "fusion_normalization_identity": fusion_result.normalization_identity,
        "fusion_input_fingerprint": fusion_result.input_fingerprint,
        "fusion_output_fingerprint": fusion_result.output_fingerprint,
        "fusion_input": fusion_input.to_mapping(include_raw_scores=True),
        "sparse_vector_name": SPARSE_VECTOR_NAME,
        "sparse_modifier": SPARSE_MODIFIER,
        "sparse_analyzer_version": SPARSE_ANALYZER_VERSION,
        "sparse_analyzer_fingerprint": SPARSE_ANALYZER_FINGERPRINT,
        "sparse_query_terms": list(sparse_query.terms),
        "sparse_query_term_indices": [
            {"term": term, "index": index} for term, index in sparse_query.term_indices
        ],
        "dense_candidates_considered": len(dense_points),
        "sparse_candidates_considered": len(sparse_points),
        "sparse_candidates_grounded": len(sparse_by_id),
        "candidates_found": pool_count,
        "candidates_considered": len(fusion_result.candidates),
        "candidates_returned": len(results),
        "output_limit": scope.max_results,
        "output_truncated": any(not item.returned for item in fusion_result.candidates),
        "search_requirements": [item.to_mapping() for item in requirements],
        "query_embedding_text_sha256": hashlib.sha256(query_text.encode("utf-8")).hexdigest(),
        "score_normalization": "shared_rrf_rank_fusion",
        "normalized_score_distribution": _score_distribution([item.score for item in results]),
        "candidate_order_before_cutoff": candidate_order,
        "diagnostic_candidate_order": candidate_order,
        "diagnostic_candidate_window": (
            settings.diagnostic_candidate_window
            if settings.diagnostic_candidate_window is not None
            else "disabled"
        ),
        "reranker": False,
    }
    duration_ms = elapsed_ms(started, timer())
    telemetry = RetrievalTelemetry(
        mode="hybrid",
        duration_ms=duration_ms,
        candidates_examined=len(fusion_result.candidates),
        candidates_returned=len(results),
        returned_chars=returned_chars(item.content for item in results),
        stages=(
            RetrievalStageTelemetry(name="embedding", duration_ms=embedding_ms),
            RetrievalStageTelemetry(
                name="dense",
                duration_ms=dense_ms,
                candidates_out=len(dense_points),
            ),
            RetrievalStageTelemetry(
                name="lexical",
                duration_ms=sparse_ms,
                candidates_out=len(sparse_by_id),
                metadata={
                    "representation": "sparse",
                    "raw_candidates": len(sparse_points),
                },
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
            "embedding_serializer": snapshot.serializer_fingerprint,
            "sparse_analyzer": SPARSE_ANALYZER_FINGERPRINT,
            "fusion_input": fusion_result.input_fingerprint,
            "fusion_output": fusion_result.output_fingerprint,
        },
        budget={
            "max_results": scope.max_results,
            "output_truncated": any(
                not item.returned for item in fusion_result.candidates
            ),
        },
        metadata={
            "embedding_provider": snapshot.embedding_provider,
            "embedding_model": snapshot.embedding_model,
            "vector_dimensions": snapshot.vector_dimensions,
            "fusion_strategy": "rrf",
        },
    )
    return KnowledgeSearchResponse(
        backend=backend_id,
        status="found" if results else "not_found",
        results=tuple(results),
        metadata=metadata,
        duration_ms=duration_ms,
        telemetry=telemetry,
    )

def _absolute_http_url(value: str, label: str) -> str:
    url = value.rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise KnowledgeSearchError(
            f"{label} URL must be an absolute HTTP(S) URL",
            category="schema_mismatch",
        )
    return url


def _projection_embedding_mode(value: object) -> ProjectionEmbeddingMode:
    try:
        return ProjectionEmbeddingMode(str(value))
    except ValueError as exc:
        raise KnowledgeSearchError(
            "Qdrant embedding_serializer_mode is invalid",
            category="schema_mismatch",
            metadata={
                "actual": str(value),
                "allowed": [mode.value for mode in ProjectionEmbeddingMode],
            },
        ) from exc


def _positive_float(value: object, name: str) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            f"{name} must be numeric",
            category="schema_mismatch",
        ) from exc
    if not math.isfinite(result) or result <= 0:
        raise KnowledgeSearchError(
            f"{name} must be positive",
            category="schema_mismatch",
        )
    return result


def _positive_int(value: object, name: str) -> int:
    try:
        result = int(str(value))
    except (TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            f"{name} must be an integer",
            category="schema_mismatch",
        ) from exc
    if result <= 0:
        raise KnowledgeSearchError(
            f"{name} must be positive",
            category="schema_mismatch",
        )
    return result


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


def _normalize_cosine_score(score: float) -> float:
    return max(0.0, min(1.0, score))


def _repository_alias(settings: QdrantVectorSettings, repository: str) -> str:
    digest = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:12]
    return f"{settings.collection}-{digest}"


def _writer_lock_collection(
    settings: QdrantVectorSettings,
    repository: str,
) -> str:
    identity = f"{settings.url}\0{_repository_alias(settings, repository)}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"fetchcore-qdrant-writer-lock-{digest}"


def _writer_lock_point_id(repository: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"fetchcore:qdrant:writer-lock:{repository}"))


def _same_snapshot_generation(
    left: QdrantVectorSnapshot,
    right: QdrantVectorSnapshot,
) -> bool:
    return left == right and left.active_collection == right.active_collection


def _same_canonical_document(
    previous: KnowledgeDocument,
    current: KnowledgeDocument,
) -> bool:
    def content(document: KnowledgeDocument) -> str:
        value = document.to_mapping()
        value.pop("branch", None)
        value.pop("revision", None)
        value.pop("indexed_at", None)
        provenance = value.get("provenance")
        if isinstance(provenance, dict):
            provenance.pop("revision", None)
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    return content(previous) == content(current)


def _repository_collection_prefix(
    settings: QdrantVectorSettings,
    repository: str,
) -> str:
    return f"{_repository_alias(settings, repository)}-"


def _concrete_collection(
    settings: QdrantVectorSettings,
    repository: str,
    revision: str,
    corpus: str,
    indexed_at: str,
) -> str:
    revision_hash = hashlib.sha256(revision.encode("utf-8")).hexdigest()[:10]
    build_hash = hashlib.sha256(indexed_at.encode("utf-8")).hexdigest()[:8]
    return (
        f"{_repository_alias(settings, repository)}-"
        f"{revision_hash}-{corpus[:10]}-{build_hash}"
    )


def _knowledge_point_id(document_id: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"agentsorch:qdrant:knowledge:{document_id}",
        )
    )


def _snapshot_point_id(repository: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"agentsorch:qdrant:snapshot:{repository}",
        )
    )


_T = TypeVar("_T")


def _batches(values: tuple[_T, ...], size: int) -> Iterable[tuple[_T, ...]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _qdrant_count(payload: Mapping[str, object]) -> int:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise KnowledgeSearchError(
            "Qdrant count response is invalid",
            category="response_parsing_failure",
        )
    try:
        return int(str(result["count"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            "Qdrant count response is invalid",
            category="response_parsing_failure",
        ) from exc


def _dense_vector_config(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    dense = value.get("") if "" in value else value
    return dense if isinstance(dense, Mapping) else None


def _positive_response_int(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise KnowledgeSearchError(
            f"Qdrant {label} is invalid",
            category="response_parsing_failure",
        )
    return value


def _dense_point_vector(
    value: object,
    *,
    expected_dimensions: int,
) -> tuple[float, ...]:
    dense: object = value.get("") if isinstance(value, Mapping) else value
    if not isinstance(dense, (list, tuple)):
        raise KnowledgeSearchError(
            "Qdrant point is missing its dense vector",
            category="response_parsing_failure",
        )
    vector = tuple(
        _finite_float(item, "Qdrant dense vector value") for item in dense
    )
    if len(vector) != expected_dimensions:
        raise KnowledgeSearchError(
            "Qdrant point dense vector dimensions are incompatible",
            category="schema_mismatch",
            metadata={
                "expected_dimensions": expected_dimensions,
                "actual_dimensions": len(vector),
                "rebuild_required": True,
            },
        )
    return vector


def _single_point_payload(
    payload: Mapping[str, object],
    label: str,
) -> dict[str, object]:
    result = payload.get("result")
    if not isinstance(result, list) or len(result) != 1:
        raise KnowledgeSearchError(
            f"{label} is missing or ambiguous",
            category="response_parsing_failure",
        )
    point = result[0]
    raw_payload = point.get("payload") if isinstance(point, Mapping) else None
    if not isinstance(raw_payload, Mapping):
        raise KnowledgeSearchError(
            f"{label} payload is invalid",
            category="response_parsing_failure",
        )
    return {str(key): item for key, item in raw_payload.items()}


def _projected_capabilities(document: Mapping[str, object]) -> tuple[str, ...]:
    knowledge_type = str(document.get("knowledge_type", "unknown"))
    if knowledge_type == "tests":
        return ("tests",)
    if knowledge_type != "code":
        return ()
    symbols = _string_values(document.get("symbols"))
    symbol_types = _string_values(document.get("symbol_types"))
    content = "\n".join(_string_values(document.get("content")))
    executable = (
        bool(symbols)
        and any(item in _EXECUTABLE_SYMBOL_TYPES for item in symbol_types)
    )
    if executable or any(pattern.search(content) for pattern in _EXECUTABLE_PATTERNS):
        return ("implementation",)
    return ()


def _projection_observations(
    document: Mapping[str, object],
    *,
    source_ref: str,
    revision: str,
) -> tuple[EvidenceObservation, ...]:
    slice_ref = _optional_str(document.get("chunk_ref"))
    checksum = _optional_str(document.get("source_checksum"))
    line_start = _optional_int(document.get("line_start"))
    line_end = _optional_int(document.get("line_end"))
    values: dict[tuple[str, str, str], EvidenceObservation] = {}

    def add(category: str, subtype: str, raw_values: tuple[str, ...]) -> None:
        for value in raw_values:
            if not value.strip():
                continue
            values.setdefault(
                (category, subtype, value.casefold()),
                EvidenceObservation(
                    category=category,  # type: ignore[arg-type]
                    subtype=subtype,  # type: ignore[arg-type]
                    value=value,
                    source_ref=source_ref,
                    slice_ref=slice_ref,
                    revision=revision,
                    checksum=checksum,
                    line_start=line_start,
                    line_end=line_end,
                    provenance="canonical_repository_projection_v3",
                ),
            )

    add("identifier", "symbol", _string_values(document.get("symbols")))
    add(
        "identifier",
        "content_identifier",
        _string_values(document.get("content_identifiers_exact")),
    )
    add("path", "filename", _string_values(document.get("filename_exact")))
    add("path", "file", _string_values(document.get("path_text")))
    add("path", "segment", _string_values(document.get("path_segments")))
    add(
        "technical_role",
        "technical_role",
        _string_values(document.get("technical_role")),
    )
    add("route", "method", _string_values(document.get("route_methods")))
    add("route", "path", _string_values(document.get("route_paths")))
    add("route", "mechanism", _string_values(document.get("route_mechanisms")))
    add("reference", "import", _string_values(document.get("import_targets")))
    add(
        "dependency",
        "dependency_target",
        _string_values(document.get("dependency_targets")),
    )
    add(
        "reference",
        "relation_target",
        _string_values(document.get("relation_targets")),
    )
    return tuple(values.values())


def _projection_relationships(
    document: Mapping[str, object],
) -> tuple[SearchRelationship, ...]:
    result: dict[tuple[str, str], SearchRelationship] = {}
    source_ref = _optional_str(document.get("source_ref"))
    for token in _string_values(document.get("relation_tokens")):
        relation_type, separator, target = token.partition(":")
        if not separator or not relation_type.strip() or not target.strip():
            continue
        result.setdefault(
            (relation_type.casefold(), target.casefold()),
            SearchRelationship(
                type=relation_type,
                target=target,
                discovered_from=source_ref,
            ),
        )
    return tuple(result.values())


def _string_values(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value if str(item).strip())
    return (str(value),)


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _score_distribution(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "max": None, "mean": None}
    return {
        "count": len(values),
        "min": min(values),
        "max": max(values),
        "mean": sum(values) / len(values),
    }
