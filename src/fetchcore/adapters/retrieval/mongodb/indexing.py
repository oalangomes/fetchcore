"""Projection persistence with compare-and-swap snapshots and fail-closed writes."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from fetchcore.application.index_store import RepositoryIndexRuntimeView
from fetchcore.projection import (
    ProjectionV3CanonicalDocumentReader, ProjectionV3Compiler, corpus_checksum,
    projection_contract_fingerprint,
)
from fetchcore.repository.incremental import (
    GitRepositoryChangeDetector, index_repository_delta,
)
from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexResult, _canonical_payload
from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.embedding import (
    EmbeddingProvider, ProjectionV3EmbeddingSerializer,
)
from fetchcore.telemetry import IndexSyncTelemetry, elapsed_ms

from .query import VECTOR_FIELD, index_definitions, validate_vectors
from .settings import MongoDBSettings
from .transport import MongoDBTransport


def record_id(repository: str, identifier: str) -> str:
    return hashlib.sha256(f"{repository}\0{identifier}".encode()).hexdigest()


def _snapshot_id(repository: str) -> str:
    return "snapshot:" + hashlib.sha256(repository.encode()).hexdigest()


def _text(data: Mapping[str, object], name: str) -> str:
    value = data.get(name)
    if not isinstance(value, str) or not value:
        raise KnowledgeSearchError(
            f"MongoDB snapshot has invalid {name}", category="response_parsing_failure"
        )
    return value


@dataclass(frozen=True)
class MongoDBSnapshot:
    repository: str
    revision: str
    branch: str | None
    corpus_checksum: str
    document_count: int
    projection_version: str
    projection_fingerprint: str
    indexing_fingerprint: str | None
    embedding_identity: dict[str, object]
    token: str
    indexed_at: str
    status: str = "ready"

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> MongoDBSnapshot:
        count = data.get("document_count")
        identity = data.get("embedding_identity")
        branch, indexing = data.get("branch"), data.get("indexing_fingerprint")
        if (
            type(count) is not int or count < 0 or not isinstance(identity, dict)
            or any(not isinstance(key, str) for key in identity)
            or (branch is not None and not isinstance(branch, str))
            or (indexing is not None and not isinstance(indexing, str))
        ):
            raise KnowledgeSearchError(
                "MongoDB snapshot is malformed", category="response_parsing_failure"
            )
        return cls(
            repository=_text(data, "repository"), revision=_text(data, "revision"),
            branch=branch, corpus_checksum=_text(data, "corpus_checksum"), document_count=count,
            projection_version=_text(data, "projection_version"),
            projection_fingerprint=_text(data, "projection_fingerprint"),
            indexing_fingerprint=indexing, embedding_identity=dict(identity),
            token=_text(data, "token"), indexed_at=_text(data, "indexed_at"),
            status=_text(data, "status"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "_id": _snapshot_id(self.repository), "record_kind": "snapshot",
            "repository": self.repository, "revision": self.revision, "branch": self.branch,
            "corpus_checksum": self.corpus_checksum, "document_count": self.document_count,
            "projection_version": self.projection_version,
            "projection_fingerprint": self.projection_fingerprint,
            "indexing_fingerprint": self.indexing_fingerprint,
            "embedding_identity": self.embedding_identity, "token": self.token,
            "indexed_at": self.indexed_at, "status": self.status,
        }


@dataclass(frozen=True)
class MongoDBSyncResult:
    mode: str
    snapshot: MongoDBSnapshot
    added: int
    updated: int
    deleted: int
    unchanged: int
    telemetry: IndexSyncTelemetry

    def to_mapping(self) -> dict[str, object]:
        return {
            "mode": self.mode, "snapshot": self.snapshot.to_mapping(),
            "added": self.added, "updated": self.updated, "deleted": self.deleted,
            "unchanged": self.unchanged, "telemetry": self.telemetry.to_mapping(),
            "search_consistency": "eventual",
        }


class MongoDBIndexStore:
    def __init__(
        self, transport: MongoDBTransport, settings: MongoDBSettings, *,
        embedding_provider: EmbeddingProvider | None = None,
        clock: Callable[[], str] = lambda: datetime.now(UTC).isoformat(),
    ) -> None:
        self.transport, self.settings = transport, settings
        self.embedding_provider = embedding_provider
        self.embedding_serializer = ProjectionV3EmbeddingSerializer(settings.projection_version)
        self.projection_fingerprint = projection_contract_fingerprint(settings.projection_version)
        self.indexing_fingerprint: str | None = None
        self.clock = clock

    def health(self) -> None:
        self.transport.health()

    def embedding_identity(self) -> dict[str, object]:
        provider = self.embedding_provider
        if provider is None:
            return {}
        return {
            "provider": provider.provider_id, "model": provider.model_id,
            "dimensions": self.settings.vector_dimensions,
            "serializer_fingerprint": self.embedding_serializer.serializer_fingerprint,
            "projection_fingerprint": self.embedding_serializer.projection_fingerprint,
        }

    def embedding_snapshot_matches(self, snapshot: MongoDBSnapshot) -> bool:
        return snapshot.embedding_identity == self.embedding_identity()

    def initialize(self) -> dict[str, object]:
        try:
            existing = {item.get("name"): item for item in self.transport.indexes()}
        except KnowledgeSearchError as exc:
            if exc.category != "index_missing":
                raise
            existing = {}
        created: list[str] = []
        for definition in index_definitions(self.settings, vectors=self.embedding_provider is not None):
            name = str(definition["name"])
            if name not in existing:
                self.transport.create_index(definition)
                created.append(name)
        return {"created_indexes": created, "status": "building" if created else "existing",
                "readiness_required": True}

    def validate_schema(self, *, require_vectors: bool = False) -> None:
        existing = {item.get("name"): item for item in self.transport.indexes()}
        for definition in index_definitions(self.settings, vectors=require_vectors):
            item = existing.get(definition["name"])
            if item is None:
                raise KnowledgeSearchError("MongoDB search index is missing", category="index_missing")
            if item.get("latestDefinition") != definition["definition"]:
                raise KnowledgeSearchError(
                    "MongoDB search index definition is incompatible", category="schema_mismatch",
                    metadata={"index": definition["name"], "rebuild_required": True},
                )
            if item.get("status") != "READY" or item.get("queryable") is not True:
                raise KnowledgeSearchError(
                    "MongoDB search index is not ready", category="index_not_ready",
                    metadata={"index": definition["name"], "status": item.get("status")},
                )

    def raw_snapshot(self, repository: str) -> dict[str, object] | None:
        rows = self.transport.find({"_id": _snapshot_id(repository)})
        if len(rows) > 1:
            raise KnowledgeSearchError("Duplicate MongoDB snapshot", category="response_parsing_failure")
        return rows[0] if rows else None

    def load_snapshot(self, repository: str) -> MongoDBSnapshot:
        raw = self.raw_snapshot(repository)
        if raw is None:
            raise KnowledgeSearchError("MongoDB snapshot is absent", category="snapshot_missing")
        snapshot = MongoDBSnapshot.from_mapping(raw)
        if snapshot.repository != repository:
            raise KnowledgeSearchError(
                "MongoDB repository binding mismatch", category="repository_binding_mismatch"
            )
        if snapshot.status != "ready":
            raise KnowledgeSearchError(
                "MongoDB snapshot is not ready", category="index_not_ready",
                metadata={"status": snapshot.status, "rebuild_required": snapshot.status == "failed"},
            )
        if (
            snapshot.projection_version != self.settings.projection_version
            or snapshot.projection_fingerprint != self.projection_fingerprint
        ):
            raise KnowledgeSearchError(
                "MongoDB projection fingerprint mismatch", category="schema_mismatch",
                metadata={"rebuild_required": True},
            )
        return snapshot

    def load_documents(self, repository: str) -> tuple[KnowledgeDocument, ...]:
        before = self.load_snapshot(repository)
        reader = ProjectionV3CanonicalDocumentReader(self.settings.projection_version)
        documents = tuple(reader.read(item) for item in self.transport.find({
            "repository": repository, "record_kind": "knowledge",
        }))
        if self.load_snapshot(repository) != before:
            raise KnowledgeSearchError("MongoDB snapshot changed during read", category="concurrent_write")
        if len(documents) != before.document_count or corpus_checksum(documents) != before.corpus_checksum:
            raise KnowledgeSearchError("MongoDB corpus checksum mismatch", category="index_stale")
        return documents

    def full_rebuild(self, result: RepositoryIndexResult) -> MongoDBSyncResult:
        return self._sync(result, rebuild=True)

    def incremental_sync(
        self, result: RepositoryIndexResult, *, expected_snapshot: MongoDBSnapshot | None = None,
    ) -> MongoDBSyncResult:
        return self._sync(result, rebuild=False, expected_snapshot=expected_snapshot)

    def _sync(
        self, result: RepositoryIndexResult, *, rebuild: bool,
        expected_snapshot: MongoDBSnapshot | None = None,
    ) -> MongoDBSyncResult:
        started = time.monotonic()
        self.validate_schema(require_vectors=self.embedding_provider is not None)
        if not result.telemetry.removal_detection_complete:
            raise KnowledgeSearchError("Refusing incomplete MongoDB corpus", category="index_stale")
        old_raw = self.raw_snapshot(result.repository)
        if old_raw is not None and old_raw.get("status") == "writing":
            raise KnowledgeSearchError("MongoDB writer is active", category="concurrent_write")
        old: MongoDBSnapshot | None = None
        if not rebuild and old_raw is not None:
            old = self.load_snapshot(result.repository)
            if expected_snapshot is not None and old != expected_snapshot:
                raise KnowledgeSearchError("MongoDB snapshot changed during indexing", category="concurrent_write")
            if (
                old.indexing_fingerprint != self.indexing_fingerprint
                or not self.embedding_snapshot_matches(old)
            ):
                raise KnowledgeSearchError(
                    "MongoDB persisted indexing identity changed", category="schema_mismatch",
                    metadata={"rebuild_required": True},
                )
            self.load_documents(result.repository)
        compiler = ProjectionV3Compiler(self.settings.projection_version)
        previous = {
            str(item["id"]): item for item in self.transport.find({
                "repository": result.repository, "record_kind": "knowledge",
            })
        }
        reader = ProjectionV3CanonicalDocumentReader(self.settings.projection_version)
        previous_documents = (
            {} if rebuild else {key: reader.read(item) for key, item in previous.items()}
        )
        projected = {document.id: compiler.compile(document) for document in result.documents}
        changed_ids = {
            document.id for document in result.documents
            if rebuild or document.id not in previous_documents
            or _canonical_payload(previous_documents[document.id]) != _canonical_payload(document)
        }
        changed = [
            record for identifier, record in projected.items()
            if identifier in changed_ids
        ]
        deleted = set(previous) - set(projected)
        if (
            old is not None and not changed and not deleted and old.revision == result.revision
            and old.branch == result.branch
        ):
            if self.load_snapshot(result.repository) != old:
                raise KnowledgeSearchError("MongoDB snapshot changed during no-op", category="concurrent_write")
            return MongoDBSyncResult(
                "noop", old, 0, 0, 0, len(projected),
                IndexSyncTelemetry(elapsed_ms(started, time.monotonic()), 0, 0),
            )
        snapshot = MongoDBSnapshot(
            repository=result.repository, revision=result.revision, branch=result.branch,
            corpus_checksum=corpus_checksum(result.documents), document_count=len(projected),
            projection_version=self.settings.projection_version,
            projection_fingerprint=self.projection_fingerprint,
            indexing_fingerprint=self.indexing_fingerprint, embedding_identity=self.embedding_identity(),
            token=uuid4().hex, indexed_at=self.clock(),
        )
        locked = {**snapshot.to_mapping(), "status": "writing"}
        expected: dict[str, object] = (
            {"_absent": True} if old_raw is None
            else {"_id": old_raw["_id"], "token": old_raw.get("token"), "status": old_raw.get("status")}
        )
        if not self.transport.replace(locked, expected):
            raise KnowledgeSearchError("MongoDB snapshot lock failed", category="concurrent_write")
        embedding_requests = embedding_vectors = embedding_ms = 0
        try:
            for offset in range(0, len(changed), self.settings.batch_size):
                batch = changed[offset:offset + self.settings.batch_size]
                vector_by_id: dict[str, tuple[float, ...]] = {}
                if self.embedding_provider is not None:
                    to_embed = [
                        record for record in batch
                        if rebuild or str(record.to_mapping()["id"]) not in previous_documents
                        or self.embedding_serializer.serialize_record(record)
                        != self.embedding_serializer.serialize_record(
                            compiler.compile(previous_documents[str(record.to_mapping()["id"])])
                        )
                    ]
                    embedding_started = time.monotonic()
                    if to_embed:
                        vectors = self.embedding_provider.embed(tuple(
                            self.embedding_serializer.serialize_record(record) for record in to_embed
                        ))
                        validate_vectors(vectors, self.settings.vector_dimensions)
                        if len(vectors) != len(to_embed):
                            raise KnowledgeSearchError(
                                "MongoDB embedding dimensions/count mismatch", category="schema_mismatch"
                            )
                        vector_by_id = {
                            str(record.to_mapping()["id"]): vector
                            for record, vector in zip(to_embed, vectors, strict=True)
                        }
                        embedding_requests += 1
                        embedding_vectors += len(vectors)
                        embedding_ms += elapsed_ms(embedding_started, time.monotonic())
                for record in batch:
                    item = record.to_mapping()
                    item["_id"] = record_id(result.repository, str(item["id"]))
                    item["write_token"] = snapshot.token
                    if self.embedding_provider is not None:
                        identifier = str(item["id"])
                        if identifier in vector_by_id:
                            item[VECTOR_FIELD] = list(vector_by_id[identifier])
                        else:
                            raw_vector = previous[identifier].get(VECTOR_FIELD)
                            if (
                                not isinstance(raw_vector, list)
                                or len(raw_vector) != self.settings.vector_dimensions
                                or any(isinstance(value, bool) or not isinstance(value, (int, float))
                                       for value in raw_vector)
                            ):
                                raise KnowledgeSearchError(
                                    "MongoDB reusable vector is invalid", category="schema_mismatch"
                                )
                            validate_vectors(
                                (tuple(float(value) for value in raw_vector),),
                                self.settings.vector_dimensions,
                            )
                            item[VECTOR_FIELD] = raw_vector
                    if not self.transport.replace(item, None):
                        raise KnowledgeSearchError("MongoDB record write failed", category="index_update_failure")
            for identifier in deleted:
                self.transport.delete({"_id": record_id(result.repository, identifier)})
            if not self.transport.replace(
                snapshot.to_mapping(), {"_id": locked["_id"], "token": snapshot.token, "status": "writing"},
            ):
                raise KnowledgeSearchError("MongoDB promotion failed", category="concurrent_write")
        except (KnowledgeSearchError, ValueError, OSError) as exc:
            marked_failed = self.transport.replace(
                {**locked, "status": "failed"},
                {"_id": locked["_id"], "token": snapshot.token, "status": "writing"},
            )
            if not marked_failed:
                raise KnowledgeSearchError(
                    "MongoDB failed snapshot could not be marked", category="concurrent_write",
                ) from exc
            raise
        added = sum(record.to_mapping()["id"] not in previous for record in changed)
        return MongoDBSyncResult(
            "rebuild" if rebuild else "incremental", snapshot, added, len(changed) - added,
            len(deleted), len(projected) - len(changed),
            IndexSyncTelemetry(
                elapsed_ms(started, time.monotonic()), len(changed) + 2, len(deleted),
                embedding_requests, embedding_vectors, embedding_ms,
            ),
        )


class MongoDBOperationalIndex:
    def __init__(self, store: MongoDBIndexStore, runtime: RepositoryIndexRuntimeView) -> None:
        self.store, self.runtime = store, runtime
        self.store.indexing_fingerprint = runtime.fingerprint

    def run(self, operation: str) -> dict[str, object]:
        if operation == "bootstrap":
            return self.store.initialize()
        if operation == "rebuild":
            return self.store.full_rebuild(self.runtime.indexer.index(self.runtime.workspace)).to_mapping()
        try:
            snapshot = self.store.load_snapshot(self.runtime.repository)
        except KnowledgeSearchError as exc:
            if exc.category != "snapshot_missing":
                raise
            snapshot = None
        compatible = snapshot is None or (
            snapshot.indexing_fingerprint == self.runtime.fingerprint
            and self.store.embedding_snapshot_matches(snapshot)
        )
        plan = None if snapshot is None else GitRepositoryChangeDetector().plan(
            self.runtime.workspace, previous_revision=snapshot.revision,
            current_revision=self.runtime.revision,
        )
        clean = snapshot is not None and plan is not None and (
            plan.mode == "git-delta" and not plan.changed_paths and not plan.removed_paths
            and snapshot.revision == plan.current_revision
            and (self.runtime.branch is None or snapshot.branch == self.runtime.branch)
        )
        status: dict[str, object] = {
            "mode": "rebuild_required" if not compatible else "initial" if snapshot is None
            else "noop" if clean else "incremental",
            "currentness": "incompatible" if not compatible else "absent" if snapshot is None
            else "current" if clean else "stale",
            "indexed_revision": None if snapshot is None else snapshot.revision,
            "indexing_fingerprint": self.runtime.fingerprint,
            "snapshot_indexing_fingerprint": None if snapshot is None else snapshot.indexing_fingerprint,
            "search_consistency": "eventual",
        }
        if operation == "status":
            return status
        if not compatible:
            raise KnowledgeSearchError("MongoDB requires explicit rebuild", category="schema_mismatch")
        previous = () if snapshot is None else self.store.load_documents(self.runtime.repository)
        indexed = None
        if plan is not None and plan.mode == "git-delta":
            indexed = index_repository_delta(self.runtime.indexer, self.runtime.workspace, previous, plan)
        if indexed is None:
            indexed = self.runtime.indexer.index(self.runtime.workspace, previous_documents=previous)
        if operation == "plan":
            status["planned_mutations"] = indexed.changes.to_mapping()
            return status
        if operation != "sync":
            raise ValueError("Unsupported MongoDB indexing operation")
        return self.store.incremental_sync(indexed, expected_snapshot=snapshot).to_mapping()
