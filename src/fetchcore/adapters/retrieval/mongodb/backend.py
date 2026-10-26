"""MongoDB lexical/vector acquisition behind the FetchCore retrieval contracts."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fetchcore.repository.indexing import RepositoryIndexer
from fetchcore.retrieval.config import (
    RetrievalBackendCapabilities, RetrievalConfig, validate_backend_retrieval,
)
from fetchcore.retrieval.contracts import (
    Anchor, KnowledgeSearchError, KnowledgeSearchResponse, SearchIntent, SearchRequirement,
    SearchResult, SearchScope,
)
from fetchcore.retrieval.embedding import EmbeddingProvider
from fetchcore.retrieval.fusion import (
    FusionCandidate, FusionChannelSignal, FusionInput, FusionKernel, FusionRequest,
)
from fetchcore.retrieval.projection_matching import (
    classify_capabilities, match_projection_record, optional_int, optional_str,
    normalize_provider_scores, query_match_evidence, relationships, structural_observations,
)
from fetchcore.retrieval.translation import translation_diagnostics, validate_intent_translation
from fetchcore.telemetry import (
    RetrievalStageTelemetry, RetrievalTelemetry, elapsed_ms, returned_chars,
)

from .indexing import MongoDBIndexStore, MongoDBSnapshot
from .query import lexical_pipeline, scope_filter, validate_vectors, vector_pipeline
from .settings import MongoDBSettings
from .transport import MongoDBTransport, PyMongoTransport


def _unconfigured_provider(retrieval: RetrievalConfig) -> EmbeddingProvider:
    raise KnowledgeSearchError(
        "MongoDB dense retrieval requires a composed embedding provider",
        category="unsupported_capability",
    )


def _score(item: dict[str, object]) -> float:
    value = item.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise KnowledgeSearchError("MongoDB returned an invalid score", category="response_parsing_failure")
    return float(value)


class MongoDBSearchBackend:
    backend_id = "mongodb-search"
    retrieval_capabilities = RetrievalBackendCapabilities(
        lexical=True, dense=True, hybrid=True, canonical_query=True,
        structured_anchors=True, boolean_expressions=True, targeted_lookup=True,
    )

    def __init__(
        self, *, transport_factory: Callable[[MongoDBSettings], MongoDBTransport] = PyMongoTransport,
        embedding_factory: Callable[[RetrievalConfig], EmbeddingProvider] = _unconfigured_provider,
    ) -> None:
        self.transport_factory, self.embedding_factory = transport_factory, embedding_factory

    def lookup_exact(self, anchor: Anchor, scope: SearchScope) -> KnowledgeSearchResponse:
        response = self.search(
            SearchIntent(anchors=(anchor,)), replace(scope, retrieval=None, max_results=2), (),
        )
        return replace(response, metadata={**response.metadata, "acquisition": "targeted"})

    def search(
        self, intent: SearchIntent, scope: SearchScope, requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        started = time.monotonic()
        retrieval = scope.retrieval or RetrievalConfig()
        validate_backend_retrieval(
            retrieval, backend_id=self.backend_id, capabilities=self.retrieval_capabilities,
        )
        mode = retrieval.mode
        for channel in (("lexical", "dense") if mode == "hybrid" else (mode,)):
            validate_intent_translation(intent, channel=channel, capabilities=self.retrieval_capabilities)
        if type(scope.max_results) is not int or not 1 <= scope.max_results <= 5000:
            raise KnowledgeSearchError("Invalid MongoDB result budget", category="schema_mismatch")
        settings = MongoDBSettings.from_mapping({
            key: value for key, value in scope.settings.items() if key not in {"language", "source_type"}
        })
        limit = max(scope.max_results, settings.diagnostic_candidate_window or scope.max_results)
        transport = self.transport_factory(settings)
        try:
            store = MongoDBIndexStore(transport, settings)
            store.validate_schema(require_vectors=mode in {"dense", "hybrid"})
            repository = scope.repository or RepositoryIndexer().resolve_repository_identity(scope.workspace)
            snapshot = store.load_snapshot(repository)
            if snapshot.document_count == 0:
                raise KnowledgeSearchError("MongoDB snapshot is empty", category="empty_index")
            if scope.revision is not None and scope.revision != snapshot.revision:
                raise KnowledgeSearchError(
                    "Requested revision differs from MongoDB snapshot",
                    category="snapshot_revision_mismatch",
                )
            filters = scope_filter(scope, repository, settings)
            filters["projection_fingerprint"] = snapshot.projection_fingerprint
            stages: list[RetrievalStageTelemetry] = []
            vector: tuple[float, ...] | None = None
            if mode in {"dense", "hybrid"}:
                embedding_started = time.monotonic()
                provider = self.embedding_factory(retrieval)
                requested = retrieval.dense.embedding
                if (
                    requested.dimensions not in {None, settings.vector_dimensions}
                    or provider.provider_id != requested.provider or provider.model_id != requested.model
                ):
                    raise KnowledgeSearchError("MongoDB embedding identity mismatch", category="schema_mismatch")
                store.embedding_provider = provider
                if not store.embedding_snapshot_matches(snapshot):
                    raise KnowledgeSearchError(
                        "MongoDB embedding snapshot mismatch", category="index_stale",
                        metadata={"rebuild_required": True},
                    )
                vectors = provider.embed((store.embedding_serializer.serialize_query(intent),))
                validate_vectors(vectors, settings.vector_dimensions)
                if len(vectors) != 1:
                    raise KnowledgeSearchError("Invalid MongoDB query vector", category="schema_mismatch")
                vector = vectors[0]
                stages.append(RetrievalStageTelemetry(
                    "embedding", elapsed_ms(embedding_started, time.monotonic()),
                ))
            lexical = lexical_pipeline(intent, filters, settings, limit + 1)
            dense = None if vector is None else vector_pipeline(vector, filters, settings, limit + 1)

            def acquire(
                channel: str, pipeline: list[dict[str, object]],
            ) -> tuple[tuple[dict[str, object], ...], RetrievalStageTelemetry]:
                channel_started = time.monotonic()
                rows = transport.aggregate(pipeline)
                self._validate_results(rows, snapshot, scope, settings)
                return rows, RetrievalStageTelemetry(
                    channel, elapsed_ms(channel_started, time.monotonic()), candidates_out=len(rows),
                )

            channels: dict[str, tuple[dict[str, object], ...]] = {}
            if mode == "hybrid" and dense is not None:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    lexical_task = pool.submit(acquire, "lexical", lexical)
                    dense_task = pool.submit(acquire, "dense", dense)
                    for name, task in (("lexical", lexical_task), ("dense", dense_task)):
                        channels[name], stage = task.result()
                        stages.append(stage)
            else:
                pipeline = dense if mode == "dense" and dense is not None else lexical
                channels[mode], stage = acquire(mode, pipeline)
                stages.append(stage)
            if store.load_snapshot(repository) != snapshot:
                raise KnowledgeSearchError("MongoDB snapshot changed during search", category="concurrent_write")
            response = self._response(intent, scope, settings, snapshot, channels, limit, requirements)
            duration = elapsed_ms(started, time.monotonic())
            return replace(
                response, duration_ms=duration,
                telemetry=RetrievalTelemetry(
                    mode, duration, sum(len(rows) for rows in channels.values()),
                    len(response.results), returned_chars(response.results), stages=tuple(stages),
                    budget={"candidate_window": limit, "output_limit": scope.max_results},
                    fingerprints={"projection": snapshot.projection_fingerprint},
                ),
            )
        finally:
            transport.close()

    @staticmethod
    def _validate_results(
        rows: tuple[dict[str, object], ...], snapshot: MongoDBSnapshot,
        scope: SearchScope, settings: MongoDBSettings,
    ) -> None:
        seen: set[str] = set()
        for item in rows:
            if (
                any(not isinstance(item.get(name), str) for name in (
                    "id", "path", "source_ref", "content", "checksum", "knowledge_type",
                ))
                or not item.get("id") or item["id"] in seen
                or item.get("repository") != snapshot.repository
                or item.get("record_kind") != "knowledge"
                or item.get("projection_fingerprint") != snapshot.projection_fingerprint
                or item.get("projection_version") != settings.projection_version
                or (scope.repository_paths and item.get("path") not in scope.repository_paths)
                or (scope.knowledge_types and item.get("knowledge_type") not in scope.knowledge_types)
            ):
                raise KnowledgeSearchError(
                    "MongoDB returned incompatible/out-of-scope evidence",
                    category="response_parsing_failure",
                )
            score = item.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise KnowledgeSearchError("MongoDB returned an invalid score", category="response_parsing_failure")
            seen.add(str(item["id"]))

    def _response(
        self, intent: SearchIntent, scope: SearchScope, settings: MongoDBSettings,
        snapshot: MongoDBSnapshot, channels: dict[str, tuple[dict[str, object], ...]],
        limit: int, requirements: tuple[SearchRequirement, ...],
    ) -> KnowledgeSearchResponse:
        candidates: dict[str, FusionCandidate] = {}
        documents: dict[str, dict[str, object]] = {}
        for channel, rows in channels.items():
            maximum = max((_score(item) for item in rows), default=1.0)
            lexical_scores = (
                normalize_provider_scores(
                    list(rows), tuple(match_projection_record(intent, item, settings) for item in rows),
                    maximum,
                ) if channel == "lexical" else ()
            )
            for rank, item in enumerate(rows[:limit], 1):
                identifier = str(item["id"])
                if identifier in documents and documents[identifier] != item:
                    # Scores are channel-specific; evidence itself must be identical.
                    left = {key: value for key, value in documents[identifier].items() if key != "score"}
                    right = {key: value for key, value in item.items() if key != "score"}
                    if left != right:
                        raise KnowledgeSearchError("MongoDB channels disagree on evidence", category="index_stale")
                documents[identifier] = item
                raw = _score(item)
                normalized = (
                    lexical_scores[rank - 1] if channel == "lexical"
                    else max(0.0, min(1.0, raw))
                )
                signal = FusionChannelSignal(rank, normalized, raw)
                candidate = candidates.get(identifier, FusionCandidate(
                    identifier, str(item["path"]),
                    lexical=signal if channel == "lexical" else None,
                    dense=signal if channel == "dense" else None,
                ))
                candidates[identifier] = (
                    replace(candidate, lexical=signal) if channel == "lexical"
                    else replace(candidate, dense=signal)
                )
        fusion = FusionKernel().fuse(FusionRequest(FusionInput(tuple(candidates.values()), scope.max_results)))
        ordered = [(item.candidate_id, item.normalized_score) for item in fusion.candidates if item.returned]
        if len(channels) == 1:
            ordered = []
            for rows in channels.values():
                for item in rows[:scope.max_results]:
                    candidate = candidates[str(item["id"])]
                    participation = candidate.lexical or candidate.dense
                    assert participation is not None
                    ordered.append((str(item["id"]), participation.normalized_score))
        results: list[SearchResult] = []
        for identifier, score in ordered:
            item = documents[identifier]
            match = match_projection_record(intent, item, settings)
            capabilities = classify_capabilities(str(item["knowledge_type"]), match)
            ref = str(item["source_ref"])
            acquisition = tuple(channel for channel, rows in channels.items() if any(
                row["id"] == identifier for row in rows[:limit]
            ))
            results.append(SearchResult(
                id=identifier, source=str(item["path"]), source_ref=ref,
                knowledge_type=str(item["knowledge_type"]), content=str(item["content"]), score=score,
                query_alignment_score=match.alignment if "lexical" in acquisition else None,
                capabilities=capabilities,
                capability_sources={name: "provider_classification" for name in capabilities},
                matched_terms=match.matched_terms, matched_signals=match.matched_signals,
                reasons=tuple(f"mongodb_{name}" for name in acquisition),
                line_start=optional_int(item.get("line_start")), line_end=optional_int(item.get("line_end")),
                revision=snapshot.revision, checksum=optional_str(item.get("checksum")),
                source_type=str(item.get("source_type", "repository_file")),
                observations=structural_observations(item, source_ref=ref, revision=snapshot.revision),
                relationships=relationships(item),
                query_matches=query_match_evidence(match, source_ref=ref, provenance="mongodb_projection_v3"),
                metadata={
                    "repository": snapshot.repository, "document_revision": item.get("document_revision"),
                    "snapshot_revision": snapshot.revision, "source_checksum": item.get("source_checksum"),
                    "chunk_ref": item.get("chunk_ref"), "branch": item.get("branch"),
                    "projection_fingerprint": snapshot.projection_fingerprint,
                    "acquisition_channels": list(acquisition),
                },
            ))
        truncated = any(len(rows) > scope.max_results for rows in channels.values()) or len(candidates) > scope.max_results
        diagnostics = (
            translation_diagnostics(intent, scope, lexical="MongoDB Search", dense="MongoDB Vector Search")
            if len(channels) == 2 else
            translation_diagnostics(intent, scope, dense="MongoDB Vector Search")
            if "dense" in channels else
            translation_diagnostics(intent, scope, lexical="MongoDB Search")
        )
        return KnowledgeSearchResponse(
            self.backend_id, "ok", tuple(results),
            metadata={
                "retrieval_mode": scope.retrieval.mode if scope.retrieval else "lexical",
                "output_truncated": truncated, "candidate_window": limit,
                "candidate_window_truncated": any(len(rows) > limit for rows in channels.values()),
                "search_consistency": "eventual", "snapshot_revision": snapshot.revision,
                "search_requirements": [item.to_mapping() for item in requirements],
                "requirement_allocation": "disabled_v1",
                **({"fusion": fusion.to_mapping()} if len(channels) == 2 else {}),
                **diagnostics,
            },
        )
