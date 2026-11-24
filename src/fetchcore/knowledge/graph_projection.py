"""Deterministic, backend-neutral projection of resolved canonical relations.

This module never resolves a textual target. A graph edge exists only when the
canonical relation points at a declaration in the same repository revision.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass

from fetchcore.projection.core import corpus_checksum
from fetchcore.repository.indexing import KnowledgeDocument

GRAPH_PROJECTION_VERSION = "1"
_CONTRACT = "fetchcore.graph-projection.v1.resolved-semantic-observations"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class GraphEdge:
    """One source observation; parallel source observations remain distinct."""

    id: str
    repository: str
    revision: str
    source_identity: str
    target_identity: str
    relation_kind: str
    evidence_basis: str
    resolution: str
    source_ref: str
    source_document_id: str
    source_checksum: str
    line: int | None
    target_document_id: str
    target_source_ref: str
    target_checksum: str
    target_path: str
    target_text: str

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> GraphEdge:
        allowed = set(cls.__dataclass_fields__)
        if set(value) != allowed:
            raise ValueError("graph edge fields are incompatible")
        text_fields = allowed - {"line"}
        if any(not isinstance(value[key], str) or not value[key] for key in text_fields):
            raise ValueError("graph edge requires non-empty string fields")
        line = value["line"]
        if line is not None and (type(line) is not int or line < 1):
            raise ValueError("graph edge line is invalid")
        edge = cls(**value)  # type: ignore[arg-type]
        if edge.id != _edge_id(edge.to_mapping()):
            raise ValueError("graph edge identity is incompatible")
        if edge.resolution not in {"resolved", "exact-unique"}:
            raise ValueError("graph edge is not resolved")
        return edge


def _edge_id(value: Mapping[str, object]) -> str:
    payload = {key: item for key, item in value.items() if key != "id"}
    return "graph-edge:v1:sha256:" + hashlib.sha256(_canonical(payload).encode()).hexdigest()


GRAPH_PROJECTION_FINGERPRINT = "sha256:" + hashlib.sha256(_canonical({
    "contract": _CONTRACT,
    "edge_fields": tuple(GraphEdge.__dataclass_fields__),
    "version": GRAPH_PROJECTION_VERSION,
}).encode()).hexdigest()


@dataclass(frozen=True)
class GraphProjection:
    """Complete rebuild artifact bound to one canonical corpus and generation."""

    repository: str
    revision: str
    generation: str
    corpus_checksum: str
    indexing_fingerprint: str
    edges: tuple[GraphEdge, ...]
    version: str = GRAPH_PROJECTION_VERSION
    fingerprint: str = GRAPH_PROJECTION_FINGERPRINT

    def __post_init__(self) -> None:
        if not all((self.repository, self.revision, self.generation,
                    self.corpus_checksum, self.indexing_fingerprint)):
            raise ValueError("graph projection binding is incomplete")
        if self.version != GRAPH_PROJECTION_VERSION or self.fingerprint != GRAPH_PROJECTION_FINGERPRINT:
            raise ValueError("graph projection contract is incompatible")
        if tuple(sorted(set(self.edges), key=lambda edge: edge.id)) != self.edges:
            raise ValueError("graph edges must be unique and sorted")
        if any(edge.repository != self.repository or edge.revision != self.revision
               for edge in self.edges):
            raise ValueError("graph edge scope conflicts with projection")

    def to_mapping(self) -> dict[str, object]:
        return {
            "repository": self.repository, "revision": self.revision,
            "generation": self.generation, "corpus_checksum": self.corpus_checksum,
            "indexing_fingerprint": self.indexing_fingerprint,
            "version": self.version, "fingerprint": self.fingerprint,
            "edges": [edge.to_mapping() for edge in self.edges],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> GraphProjection:
        if set(value) != set(cls.__dataclass_fields__):
            raise ValueError("graph projection fields are incompatible")
        for key in ("repository", "revision", "generation", "corpus_checksum",
                    "indexing_fingerprint", "version", "fingerprint"):
            if not isinstance(value[key], str) or not value[key]:
                raise ValueError(f"graph projection {key} is invalid")
        raw_edges = value["edges"]
        if not isinstance(raw_edges, list) or not all(isinstance(item, Mapping) for item in raw_edges):
            raise ValueError("graph projection edges are invalid")
        edges = tuple(GraphEdge.from_mapping(item) for item in raw_edges)
        return cls(
            repository=value["repository"], revision=value["revision"],  # type: ignore[arg-type]
            generation=value["generation"], corpus_checksum=value["corpus_checksum"],  # type: ignore[arg-type]
            indexing_fingerprint=value["indexing_fingerprint"],  # type: ignore[arg-type]
            edges=edges, version=value["version"], fingerprint=value["fingerprint"],  # type: ignore[arg-type]
        )

    def serialize(self) -> bytes:
        return _canonical(self.to_mapping()).encode("utf-8")


def project_graph(
    documents: Iterable[KnowledgeDocument], *, generation: str,
    indexing_fingerprint: str,
) -> GraphProjection:
    """Compile exactly the eligible resolved observations in a canonical corpus."""

    docs = tuple(documents)
    scopes = {(doc.repository, doc.revision) for doc in docs}
    if len(scopes) != 1:
        raise ValueError("graph projection requires exactly one repository revision")
    repository, revision = scopes.pop()
    by_id = {doc.id: doc for doc in docs}
    if len(by_id) != len(docs):
        raise ValueError("canonical corpus contains duplicate document IDs")
    declarations = {
        doc.id: {symbol.identity.key for symbol in doc.evidence.declarations
                 if symbol.identity is not None}
        for doc in docs
    }
    edges: dict[str, GraphEdge] = {}
    for doc in docs:
        for relation in doc.evidence.relations.semantic:
            if relation.resolution not in {"resolved", "exact-unique"}:
                continue
            if relation.target_identity is None or relation.target_document_id is None:
                continue
            target = by_id.get(relation.target_document_id)
            if target is None or relation.target_identity not in declarations[target.id]:
                # A dangling target is a corpus inconsistency, not a navigable edge.
                continue
            if (relation.target_source_ref != target.source_ref
                    or relation.target_path != target.path):
                raise ValueError("resolved target provenance conflicts with canonical corpus")
            source = relation.source_identity or doc.source_ref
            if source != doc.source_ref and source not in declarations[doc.id]:
                raise ValueError("resolved source identity conflicts with canonical corpus")
            payload: dict[str, object] = {
                "repository": repository, "revision": revision,
                "source_identity": source, "target_identity": relation.target_identity,
                "relation_kind": relation.kind, "evidence_basis": relation.evidence_basis,
                "resolution": relation.resolution, "source_ref": doc.source_ref,
                "source_document_id": doc.id,
                "source_checksum": doc.provenance.source_checksum,
                "line": relation.line, "target_document_id": target.id,
                "target_source_ref": target.source_ref,
                "target_checksum": target.provenance.source_checksum,
                "target_path": target.path, "target_text": relation.target,
            }
            edge = GraphEdge(id=_edge_id(payload), **payload)  # type: ignore[arg-type]
            edges[edge.id] = edge
    return GraphProjection(
        repository=repository, revision=revision, generation=generation,
        corpus_checksum=corpus_checksum(docs), indexing_fingerprint=indexing_fingerprint,
        edges=tuple(sorted(edges.values(), key=lambda edge: edge.id)),
    )
