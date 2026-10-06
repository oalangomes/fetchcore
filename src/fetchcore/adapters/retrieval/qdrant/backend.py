"""Dense and sparse-hybrid retrieval over canonical Projection v3 using Qdrant."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
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
from fetchcore.retrieval.fusion import (
    DEFAULT_RRF_K,
    FusionCandidate,
    FusionChannelSignal,
    FusionInput,
    FusionKernel,
    FusionRequest,
)
from fetchcore.projection.semantics import (
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
    ProjectionV3Compiler,
    corpus_checksum,
    projection_contract_fingerprint,
)
from fetchcore.repository.indexing import RepositoryIndexResult, RepositoryIndexer
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

    def to_mapping(self) -> dict[str, object]:
        """Return a stable mapping representation of this value."""
        return {
            "mode": self.mode,
            "snapshot": self.snapshot.to_mapping(),
            "added": self.added,
            "updated": 0,
            "deleted": 0,
            "unchanged": 0,
            "duration_ms": self.duration_ms,
            **(
                {"telemetry": self.telemetry.to_mapping()}
                if self.telemetry is not None
                else {}
            ),
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
        self.serializer = ProjectionV3EmbeddingSerializer(
            settings.projection_version,
            settings.embedding_serializer_mode,
        )
        self.projection_fingerprint = projection_contract_fingerprint(
            settings.projection_version
        )
        self.clock = clock
        self.timer = timer

    def health(self) -> dict[str, object]:
        """Return backend health information required by the lifecycle."""
        qdrant = self.transport.request("GET", "collections")
        probe = self.embedder.embed(("fetchcore embedding probe",))
        dimensions = _validate_vector_dimensions(probe)
        return {
            "qdrant": qdrant,
            "embedding_provider": self.embedder.provider_id,
            "embedding_model": self.embedder.model_id,
            "vector_dimensions": dimensions,
        }

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
        try:
            payload = self.transport.request(
                "POST",
                f"collections/{quote(alias, safe='')}/points",
                {
                    "ids": [_snapshot_point_id(repository)],
                    "with_payload": True,
                    "with_vector": False,
                },
            )
        except KnowledgeSearchError as exc:
            if exc.category == "index_missing":
                raise KnowledgeSearchError(
                    "Qdrant has no active vector snapshot",
                    category="snapshot_missing",
                    metadata={"repository": repository},
                ) from exc
            raise
        raw_payload = _single_point_payload(payload, "active Qdrant snapshot")
        snapshot = QdrantVectorSnapshot.from_mapping(raw_payload)
        self._ensure_snapshot_compatible(snapshot)
        if snapshot.status != "ready":
            raise KnowledgeSearchError(
                "Qdrant vector snapshot is not ready",
                category="index_stale",
            )
        return snapshot

    def full_rebuild(self, result: RepositoryIndexResult) -> QdrantVectorIndexSyncResult:
        """Replace the repository projection with a complete rebuild."""
        self._ensure_complete(result)
        if not result.documents:
            raise KnowledgeSearchError(
                "cannot build an empty Qdrant vector corpus",
                category="empty_index",
            )
        started = self.timer()
        indexed_at = self.clock()
        corpus = corpus_checksum(result.documents)
        alias = _repository_alias(self.settings, result.repository)
        concrete = _concrete_collection(
            self.settings,
            result.repository,
            result.revision,
            corpus,
            indexed_at,
        )
        prior = self._active_collection(alias)
        self._delete_collection_if_present(concrete)

        dimensions: int | None = None
        inserted = 0
        embedding_requests = 0
        embedding_vectors = 0
        embedding_duration_ms = 0
        alias_switched = False
        try:
            for document_batch in _batches(result.documents, self.settings.batch_size):
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
                    self._create_collection(concrete, dimensions)
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
                self.transport.request(
                    "PUT",
                    f"collections/{quote(concrete, safe='')}/points?wait=true",
                    {"points": points},
                )
                inserted += len(points)

            if dimensions is None:
                raise KnowledgeSearchError(
                    "Qdrant rebuild did not determine vector dimensions",
                    category="embedding_failed",
                )
            snapshot = QdrantVectorSnapshot(
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
            )
            control_vector = [1.0, *([0.0] * (dimensions - 1))]
            self.transport.request(
                "PUT",
                f"collections/{quote(concrete, safe='')}/points?wait=true",
                {
                    "points": [
                        {
                            "id": snapshot.point_id,
                            "vector": {"": control_vector},
                            "payload": snapshot.to_mapping(),
                        }
                    ]
                },
            )
            self._validate_staged(concrete, snapshot)
            actions: list[dict[str, object]] = []
            if prior is not None:
                actions.append({"delete_alias": {"alias_name": alias}})
            actions.append(
                {
                    "create_alias": {
                        "collection_name": concrete,
                        "alias_name": alias,
                    }
                }
            )
            self.transport.request(
                "POST",
                "collections/aliases",
                {"actions": actions},
            )
            alias_switched = True
            if self.load_snapshot(result.repository) != snapshot:
                raise KnowledgeSearchError(
                    "Qdrant alias did not expose the staged snapshot",
                    category="index_update_failure",
                )
        except Exception:
            if alias_switched:
                rollback_actions: list[dict[str, object]] = [
                    {"delete_alias": {"alias_name": alias}}
                ]
                if prior is not None:
                    rollback_actions.append(
                        {
                            "create_alias": {
                                "collection_name": prior,
                                "alias_name": alias,
                            }
                        }
                    )
                self.transport.request(
                    "POST",
                    "collections/aliases",
                    {"actions": rollback_actions},
                )
            self._delete_collection_if_present(concrete)
            raise

        if prior is not None and prior != concrete:
            self._delete_collection_if_present(prior)
        duration_ms = elapsed_ms(started, self.timer())
        telemetry = IndexSyncTelemetry(
            duration_ms=duration_ms,
            backend_writes=inserted,
            backend_deletes=0,
            embedding_requests=embedding_requests,
            embedding_vectors=embedding_vectors,
            embedding_duration_ms=embedding_duration_ms,
        )
        return QdrantVectorIndexSyncResult(
            mode="full_rebuild",
            snapshot=snapshot,
            added=inserted,
            duration_ms=duration_ms,
            telemetry=telemetry,
        )

    def reset(self, repository: str) -> None:
        """Remove the active alias and every concrete collection owned by a repository."""

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
        raw_snapshot = _single_point_payload(
            self.transport.request(
                "POST",
                f"collections/{quote(collection, safe='')}/points",
                {
                    "ids": [snapshot.point_id],
                    "with_payload": True,
                    "with_vector": False,
                },
            ),
            "staged Qdrant snapshot",
        )
        observed = QdrantVectorSnapshot.from_mapping(raw_snapshot)
        self._ensure_snapshot_compatible(observed)
        if observed != snapshot:
            raise KnowledgeSearchError(
                "Qdrant staged vector snapshot does not match compiled corpus",
                category="index_update_failure",
            )

    def _owned_collections(self, repository: str) -> tuple[str, ...]:
        payload = self.transport.request("GET", "collections")
        result = payload.get("result")
        if not isinstance(result, Mapping):
            raise KnowledgeSearchError(
                "Qdrant collections response is invalid",
                category="response_parsing_failure",
            )
        raw_collections = result.get("collections", [])
        if not isinstance(raw_collections, list):
            raise KnowledgeSearchError(
                "Qdrant collections response is invalid",
                category="response_parsing_failure",
            )
        prefix = _repository_collection_prefix(self.settings, repository)
        names = {
            str(item.get("name"))
            for item in raw_collections
            if isinstance(item, Mapping)
            and item.get("name")
            and str(item.get("name")).startswith(prefix)
        }
        return tuple(sorted(names))

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
            "embedding_model": self.embedder.model_id,
            "serializer_mode": self.settings.embedding_serializer_mode.value,
            "serializer_version": VECTOR_EMBEDDING_SERIALIZATION_VERSION,
            "serializer_fingerprint": self.serializer.serializer_fingerprint,
        }
        actual = {
            "projection_version": snapshot.projection_version,
            "projection_fingerprint": snapshot.projection_fingerprint,
            "embedding_provider": snapshot.embedding_provider,
            "embedding_model": snapshot.embedding_model,
            "serializer_mode": snapshot.serializer_mode.value,
            "serializer_version": snapshot.serializer_version,
            "serializer_fingerprint": snapshot.serializer_fingerprint,
        }
        if actual == expected and snapshot.vector_dimensions > 0:
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

    def rebuild(self, workspace: Path) -> QdrantVectorIndexSyncResult:
        """Rebuild the repository projection from canonical source state."""
        return self.store.full_rebuild(self.indexer.index(workspace))

    def sync(self, workspace: Path) -> QdrantVectorIndexSyncResult:
        """Synchronize the repository projection with canonical source state."""
        # Deliberately safe/full for the parity experiment; no second incremental runtime.
        return self.rebuild(workspace)


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
    ) -> None:
        """Initialize QdrantVectorBackend with its explicit dependencies."""
        self.transport_factory = transport_factory
        self.embedding_factory = embedding_factory
        self.timer = timer

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
        settings = QdrantVectorSettings.from_mapping(effective_settings)
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
        alias = _repository_alias(settings, repository)
        pool_count = _qdrant_count(
            transport.request(
                "POST",
                f"collections/{quote(alias, safe='')}/points/count",
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
            )

        dense_started = self.timer()
        raw_query = transport.request(
            "POST",
            f"collections/{quote(alias, safe='')}/points/query",
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
    alias = _repository_alias(settings, snapshot.repository)
    dense_started = timer()
    dense_points = _qdrant_query_points(
        transport.request(
            "POST",
            f"collections/{quote(alias, safe='')}/points/query",
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
            f"collections/{quote(alias, safe='')}/points/query",
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
    fusion_input = FusionInput(
        candidates=tuple(fusion_candidates),
        max_results=scope.max_results,
    )
    fusion_result = FusionKernel().fuse(
        FusionRequest(
            input=fusion_input,
            strategy="rrf",
            parameters={"k": DEFAULT_RRF_K},
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
        sparse_terms = sparse_terms_by_id.get(fused.candidate_id, ())
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
        "rrf_k": DEFAULT_RRF_K,
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
