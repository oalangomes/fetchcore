"""SolrCloud persistence for the optional, rebuildable GraphProjection v1."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TypedDict, cast

from fetchcore.adapters.retrieval.solr.backend import SolrSettings, UrllibSolrTransport
from fetchcore.adapters.retrieval.solr.graph_schema_validation import validate_graph_schema
from fetchcore.adapters.retrieval.solr.query import _term_filter
from fetchcore.knowledge.graph_projection import (
    GRAPH_PROJECTION_FINGERPRINT,
    GRAPH_PROJECTION_VERSION,
    GraphEdge,
    GraphProjection,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError


_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")
_RELATION = re.compile(r"[a-z][a-z0-9_-]*\Z")
class _SelectResponse(TypedDict):
    num_found: int
    docs: list[dict[str, object]]


def edge_checksum(edges: tuple[GraphEdge, ...]) -> str:
    """Digest the exact ordered edge identities, including parallel observations."""

    rendered = json.dumps([edge.id for edge in edges], separators=(",", ":"))
    return "sha256:" + hashlib.sha256(rendered.encode()).hexdigest()


@dataclass(frozen=True)
class GraphEdgePage:
    edges: tuple[GraphEdge, ...]
    has_more: bool
    backend_qtime_ms: int | None = None


class GraphStore:
    """Read and write a physical graph collection; alias policy lives above."""

    def __init__(self, settings: SolrSettings) -> None:
        if settings.deployment_mode != "solrcloud":
            raise KnowledgeSearchError(
                "GraphProjection requires SolrCloud", category="deployment_mode_mismatch",
            )
        self.settings = settings

    def _transport(self, collection: str, timeout_seconds: float | None = None) -> UrllibSolrTransport:
        if not _NAME.fullmatch(collection):
            raise KnowledgeSearchError("invalid graph collection name", category="schema_mismatch")
        if timeout_seconds is not None and not 0.05 <= timeout_seconds <= 300:
            raise KnowledgeSearchError("invalid graph query timeout", category="schema_mismatch")
        settings = replace(
            self.settings, collection=collection,
            timeout_seconds=(
                self.settings.timeout_seconds if timeout_seconds is None
                else min(self.settings.timeout_seconds, timeout_seconds)
            ),
        )
        return UrllibSolrTransport(settings)

    def validate_schema(self, collection: str) -> None:
        validate_graph_schema(self._transport(collection).get("schema", {"wt": "json"}).get("schema"))

    def write_projection(
        self, collection: str, projection: GraphProjection, manifest: Mapping[str, object],
    ) -> None:
        """Write a complete generation into an empty physical collection."""

        self.validate_schema(collection)
        transport = self._transport(collection)
        existing = self._select(collection, {"q": "*:*", "rows": 0})
        if existing["num_found"] != 0:
            raise KnowledgeSearchError(
                "graph staging collection is not empty", category="repository_resource_collision",
            )
        records: list[dict[str, object]] = []
        for edge in projection.edges:
            records.append({
                **edge.to_mapping(),
                "record_kind": "edge",
                "generation": projection.generation,
                "projection_version": projection.version,
                "projection_fingerprint": projection.fingerprint,
                "canonical_payload": json.dumps(edge.to_mapping(), sort_keys=True,
                                                separators=(",", ":"), ensure_ascii=False),
            })
        manifest_record = {
            "id": "graph-manifest:v1:" + hashlib.sha256(projection.repository.encode()).hexdigest(),
            "record_kind": "manifest",
            "repository": projection.repository,
            "revision": projection.revision,
            "generation": projection.generation,
            "projection_version": projection.version,
            "projection_fingerprint": projection.fingerprint,
            "corpus_checksum": projection.corpus_checksum,
            "indexing_fingerprint": projection.indexing_fingerprint,
            "edge_count": len(projection.edges),
            "canonical_payload": json.dumps(manifest, sort_keys=True,
                                            separators=(",", ":"), ensure_ascii=False),
        }
        records.append(manifest_record)
        for start in range(0, len(records), self.settings.batch_size):
            transport.post("update", records[start:start + self.settings.batch_size], {"wt": "json"})
        transport.post("update", {"commit": {}}, {"wt": "json"})

    def load_manifest(self, collection: str, repository: str) -> dict[str, object]:
        response = self._select(collection, {
            "q": "*:*", "fq": ["record_kind:manifest", _term_filter("repository", repository)],
            "rows": 2, "fl": "*", "sort": "id asc",
        })
        if response["num_found"] != 1 or len(response["docs"]) != 1:
            raise KnowledgeSearchError(
                "graph publication manifest is absent or ambiguous", category="snapshot_missing",
            )
        record = response["docs"][0]
        payload = record.get("canonical_payload")
        try:
            value = json.loads(cast(str, payload))
        except (TypeError, json.JSONDecodeError) as exc:
            raise KnowledgeSearchError(
                "graph publication manifest is invalid", category="response_parsing_failure",
            ) from exc
        if not isinstance(value, dict):
            raise KnowledgeSearchError(
                "graph publication manifest is invalid", category="response_parsing_failure",
            )
        for field in (
            "repository", "revision", "generation", "projection_version",
            "projection_fingerprint", "corpus_checksum", "indexing_fingerprint",
            "edge_count",
        ):
            if record.get(field) != value.get(field):
                raise KnowledgeSearchError(
                    "graph manifest indexed fields conflict with its payload",
                    category="schema_mismatch",
                )
        if (value.get("projection_version") != GRAPH_PROJECTION_VERSION
                or value.get("projection_fingerprint") != GRAPH_PROJECTION_FINGERPRINT):
            raise KnowledgeSearchError(
                "graph projection contract is incompatible", category="schema_mismatch",
            )
        return cast(dict[str, object], value)

    def count_records(self, collection: str) -> int:
        return self._select(collection, {"q": "*:*", "rows": 0})["num_found"]

    def load_all_edges(
        self, collection: str, repository: str, revision: str, generation: str,
    ) -> tuple[GraphEdge, ...]:
        """Stage verification only; page so a single Solr response stays bounded."""

        edges: list[GraphEdge] = []
        start = 0
        while True:
            response = self._select(collection, {
                "q": "*:*",
                "fq": ["record_kind:edge", _term_filter("repository", repository),
                       _term_filter("revision", revision), _term_filter("generation", generation)],
                "start": start, "rows": 200, "fl": "*", "sort": "id asc",
            })
            batch = response["docs"]
            edges.extend(self._read_edge(item, repository, revision, generation) for item in batch)
            start += len(batch)
            if start >= response["num_found"]:
                break
            if not batch:
                raise KnowledgeSearchError(
                    "graph edge scan ended early", category="response_parsing_failure",
                )
        return tuple(edges)

    def list_edges(
        self, collection: str, repository: str, revision: str, generation: str,
        *, source_identity: str | None = None, target_identity: str | None = None,
        relation_types: tuple[str, ...] = (), limit: int = 100,
        timeout_seconds: float | None = None,
    ) -> GraphEdgePage:
        if (source_identity is None) == (target_identity is None):
            raise KnowledgeSearchError(
                "graph edge query requires exactly one endpoint", category="schema_mismatch",
            )
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise KnowledgeSearchError("graph edge limit is invalid", category="schema_mismatch")
        if any(not _RELATION.fullmatch(kind) for kind in relation_types):
            raise KnowledgeSearchError("graph relation kind is invalid", category="schema_mismatch")
        filters = ["record_kind:edge", _term_filter("repository", repository),
                   _term_filter("revision", revision), _term_filter("generation", generation)]
        filters.append(_term_filter("source_identity" if source_identity is not None
                                    else "target_identity", source_identity or target_identity or ""))
        if relation_types:
            filters.append("{!terms f=relation_kind}" + ",".join(sorted(set(relation_types))))
        raw = self._transport(collection, timeout_seconds).get("select", {
            "q": "*:*", "fq": filters, "rows": limit + 1, "fl": "*", "sort": "id asc",
            "wt": "json", **({"timeAllowed": max(1, int(timeout_seconds * 1000))}
                           if timeout_seconds is not None else {}),
        })
        response = self._parse_response(raw)
        selected = response["docs"][:limit]
        edges = tuple(self._read_edge(item, repository, revision, generation) for item in selected)
        if any((edge.source_identity != source_identity if source_identity is not None
                else edge.target_identity != target_identity) for edge in edges):
            raise KnowledgeSearchError("graph edge query escaped exact scope", category="schema_mismatch")
        header = raw.get("responseHeader")
        qtime = header.get("QTime") if isinstance(header, dict) else None
        return GraphEdgePage(edges, response["num_found"] > limit,
                             qtime if type(qtime) is int else None)

    def _select(self, collection: str, params: Mapping[str, object]) -> _SelectResponse:
        return self._parse_response(self._transport(collection).get("select", {"wt": "json", **params}))

    @staticmethod
    def _parse_response(raw: Mapping[str, object]) -> _SelectResponse:
        header = raw.get("responseHeader")
        if isinstance(header, dict) and header.get("partialResults") is True:
            raise KnowledgeSearchError("graph query timed out with partial results", category="timeout")
        response = raw.get("response")
        if not isinstance(response, dict) or type(response.get("numFound")) is not int or not isinstance(response.get("docs"), list):
            raise KnowledgeSearchError("graph query response is invalid", category="response_parsing_failure")
        docs = response["docs"]
        if not all(isinstance(item, dict) for item in docs):
            raise KnowledgeSearchError("graph query documents are invalid", category="response_parsing_failure")
        return {"num_found": cast(int, response["numFound"]),
                "docs": cast(list[dict[str, object]], docs)}

    @staticmethod
    def _read_edge(
        record: Mapping[str, object], repository: str, revision: str, generation: str,
    ) -> GraphEdge:
        if (record.get("record_kind") != "edge" or record.get("repository") != repository
                or record.get("revision") != revision or record.get("generation") != generation
                or record.get("projection_version") != GRAPH_PROJECTION_VERSION
                or record.get("projection_fingerprint") != GRAPH_PROJECTION_FINGERPRINT):
            raise KnowledgeSearchError("graph edge scope or contract is incompatible", category="schema_mismatch")
        try:
            payload = json.loads(cast(str, record.get("canonical_payload")))
            if not isinstance(payload, dict):
                raise ValueError("edge payload is not an object")
            edge = GraphEdge.from_mapping(payload)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise KnowledgeSearchError("graph edge payload is invalid", category="response_parsing_failure") from exc
        for field in GraphEdge.__dataclass_fields__:
            if record.get(field) != payload[field]:
                raise KnowledgeSearchError("graph edge indexed fields conflict with payload", category="schema_mismatch")
        return edge
