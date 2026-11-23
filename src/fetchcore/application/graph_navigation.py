"""Bounded composition of a technical knowledge map from resolved graph edges.

The graph is an optional, rebuildable acquisition path.  Each returned edge is
an individual canonical observation; no score or graph adjacency confers
authority or proves runtime execution.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from typing import Literal, Protocol

from fetchcore.knowledge.evidence import SymbolEvidence
from fetchcore.knowledge.structure import ExtractionCapabilities, StructuralCoverage
from fetchcore.knowledge.graph_projection import GraphEdge
from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.navigation import StructuralNavigationError
from fetchcore.retrieval.contracts import KnowledgeSearchError

from .navigation import RepositoryKnowledgeNavigationService


_SYMBOL_ID = re.compile(r"symbol:v1:sha256:[0-9a-f]{64}\Z")
GraphDirection = Literal["outgoing", "incoming"]


@dataclass(frozen=True)
class NavigateGraphRequest:
    """An exact seed and hard resource ceilings for at most three hops."""

    seed: str
    target: str | None = None
    repository: str | None = None
    revision: str | None = None
    direction: GraphDirection = "outgoing"
    relation_types: tuple[str, ...] = ()
    depth: int = 3
    max_nodes: int = 50
    max_edges: int = 100
    max_paths: int = 100
    expansion_budget: int = 100
    timeout_seconds: float = 30.0
    max_output_bytes: int = 100_000

    def __post_init__(self) -> None:
        if not _SYMBOL_ID.fullmatch(self.seed):
            raise KnowledgeSearchError(
                "graph seed must be an exact canonical symbol identity",
                category="schema_mismatch",
            )
        if self.target is not None and not _SYMBOL_ID.fullmatch(self.target):
            raise KnowledgeSearchError(
                "graph target must be an exact canonical symbol identity",
                category="schema_mismatch",
            )
        if self.direction not in {"outgoing", "incoming"}:
            raise KnowledgeSearchError("invalid graph direction", category="schema_mismatch")
        if type(self.depth) is not int or not 1 <= self.depth <= 3:
            raise KnowledgeSearchError("graph depth must be between 1 and 3", category="schema_mismatch")
        for name in ("max_nodes", "max_edges", "max_paths", "expansion_budget", "max_output_bytes"):
            value = getattr(self, name)
            ceiling = 1_000_000 if name == "max_output_bytes" else 1_000
            if type(value) is not int or value < 1 or value > ceiling:
                raise KnowledgeSearchError(f"invalid graph {name}", category="schema_mismatch")
        if not 0 < self.timeout_seconds <= 30:
            raise KnowledgeSearchError("invalid graph timeout", category="schema_mismatch")
        if any(not value or not isinstance(value, str) for value in self.relation_types):
            raise KnowledgeSearchError("invalid graph relation type", category="schema_mismatch")


@dataclass(frozen=True)
class GraphPath:
    """One observed route; parallel observations yield distinct paths."""

    nodes: tuple[str, ...]
    edge_ids: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {"nodes": list(self.nodes), "edge_ids": list(self.edge_ids)}


@dataclass(frozen=True)
class GraphEntity:
    """Source-backed entity location without a second canonical ontology."""

    identity: str
    repository: str
    revision: str
    kind: str
    source_ref: str
    path: str
    checksum: str
    line_start: int | None
    line_end: int | None
    extraction_status: str
    extraction: ExtractionCapabilities | None = None

    def to_mapping(self) -> dict[str, object]:
        return {**asdict(self), "extraction": (
            self.extraction.to_mapping() if self.extraction else None
        )}


@dataclass(frozen=True)
class GraphInconclusiveObservation:
    source_identity: str
    kind: str
    target: str
    resolution: str
    evidence_basis: str
    source_ref: str
    line: int | None

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class GraphNavigationResult:
    repository: str
    revision: str
    generation: str
    seed: str
    target: str | None
    target_reached: bool
    direction: GraphDirection
    depth_requested: int
    depth_examined: int
    status: Literal["success", "partial", "not_found"]
    entities: tuple[GraphEntity, ...]
    edges: tuple[GraphEdge, ...]
    paths: tuple[GraphPath, ...]
    inconclusive_observations: tuple[GraphInconclusiveObservation, ...]
    coverage: tuple[StructuralCoverage, ...]
    diagnostics: tuple[str, ...]
    nodes_examined: int
    edges_examined: int
    backend_queries: int
    backend_qtime_ms: int | None
    budgets: dict[str, int | float]

    def to_mapping(self) -> dict[str, object]:
        return {
            "repository": self.repository, "revision": self.revision,
            "generation": self.generation, "seed": self.seed,
            "target": self.target, "target_reached": self.target_reached,
            "direction": self.direction,
            "depth_requested": self.depth_requested,
            "depth_examined": self.depth_examined, "status": self.status,
            "entities": [entity.to_mapping() for entity in self.entities],
            "edges": [edge.to_mapping() for edge in self.edges],
            "paths": [path.to_mapping() for path in self.paths],
            "inconclusive_observations": [item.to_mapping() for item in self.inconclusive_observations],
            "coverage": [item.to_mapping() for item in self.coverage],
            "diagnostics": list(self.diagnostics),
            "nodes_examined": self.nodes_examined,
            "edges_examined": self.edges_examined,
            "backend_queries": self.backend_queries,
            "backend_qtime_ms": self.backend_qtime_ms,
            "budgets": self.budgets,
        }


class GraphEdgePage(Protocol):
    @property
    def edges(self) -> tuple[GraphEdge, ...]: ...

    @property
    def has_more(self) -> bool: ...

    @property
    def backend_qtime_ms(self) -> int | None: ...


class GraphEdgeStore(Protocol):
    def list_edges(
        self, collection: str, repository: str, revision: str, generation: str,
        *, source_identity: str | None = None, target_identity: str | None = None,
        relation_types: tuple[str, ...] = (), limit: int,
        timeout_seconds: float | None = None,
    ) -> GraphEdgePage: ...


@dataclass(frozen=True)
class GraphWalk:
    """Safe acquisition result before canonical document hydration."""

    node_ids: tuple[str, ...]
    edges: tuple[GraphEdge, ...]
    paths: tuple[GraphPath, ...]
    diagnostics: tuple[str, ...]
    depth_examined: int
    nodes_examined: int
    edges_examined: int
    backend_queries: int
    backend_qtime_ms: int | None


def walk_graph(
    store: GraphEdgeStore, collection: str, repository: str, revision: str,
    generation: str, request: NavigateGraphRequest,
    *, allowed_paths: tuple[str, ...] = (),
) -> GraphWalk:
    """Expand exact adjacency one node at a time, never materializing unbounded fan-out."""

    started = time.monotonic()
    deadline = started + request.timeout_seconds
    frontier = [GraphPath((request.seed,), ())]
    paths: list[GraphPath] = []
    edges: dict[str, GraphEdge] = {}
    nodes = {request.seed}
    queried_nodes: set[str] = set()
    cached: dict[str, GraphEdgePage] = {}
    diagnostics: set[str] = set()
    edges_examined = 0
    backend_queries = 0
    qtime = 0
    qtime_known = True
    depth_examined = 0
    for hop in range(1, request.depth + 1):
        if not frontier:
            break
        next_frontier: list[GraphPath] = []
        for path in frontier:
            if time.monotonic() >= deadline:
                diagnostics.add("timeout_reached")
                break
            current = path.nodes[-1]
            if current not in cached:
                if len(queried_nodes) >= request.expansion_budget:
                    diagnostics.add("expansion_budget_reached")
                    break
                remaining = request.max_edges - len(edges)
                if remaining < 1:
                    diagnostics.add("max_edges_reached")
                    break
                # A single backend request can return no more than the remaining
                # edge ceiling.  The adapter separately checks rows=limit+1.
                page = store.list_edges(
                    collection, repository, revision, generation,
                    source_identity=current if request.direction == "outgoing" else None,
                    target_identity=current if request.direction == "incoming" else None,
                    relation_types=request.relation_types,
                    limit=remaining,
                    timeout_seconds=max(0.05, deadline - time.monotonic()),
                )
                if len(page.edges) > remaining:
                    raise KnowledgeSearchError(
                        "graph adapter exceeded adjacency response budget",
                        category="schema_mismatch",
                    )
                queried_nodes.add(current)
                cached[current] = page
                backend_queries += 1
                if page.backend_qtime_ms is None:
                    qtime_known = False
                else:
                    qtime += page.backend_qtime_ms
                if page.has_more:
                    diagnostics.add("adjacency_truncated")
                if time.monotonic() >= deadline:
                    diagnostics.add("timeout_reached")
                    break
            page = cached[current]
            for edge in page.edges:
                edges_examined += 1
                if (edge.repository != repository or edge.revision != revision
                        or edge.resolution not in {"resolved", "exact-unique"}
                        or (request.direction == "outgoing" and edge.source_identity != current)
                        or (request.direction == "incoming" and edge.target_identity != current)
                        or (request.relation_types
                            and edge.relation_kind not in request.relation_types)):
                    raise KnowledgeSearchError(
                        "graph adjacency conflicts with requested scope",
                        category="schema_mismatch",
                    )
                if not _path_allowed(edge.source_ref, repository, allowed_paths) or not _path_allowed(
                    edge.target_path, repository, allowed_paths,
                ):
                    diagnostics.add("scope_excluded")
                    continue
                neighbor = (edge.target_identity if request.direction == "outgoing"
                            else edge.source_identity)
                if neighbor not in nodes and len(nodes) >= request.max_nodes:
                    diagnostics.add("max_nodes_reached")
                    continue
                if edge.id not in edges and len(edges) >= request.max_edges:
                    diagnostics.add("max_edges_reached")
                    continue
                if len(paths) >= request.max_paths:
                    diagnostics.add("max_paths_reached")
                    break
                edges[edge.id] = edge
                nodes.add(neighbor)
                candidate = GraphPath(path.nodes + (neighbor,), path.edge_ids + (edge.id,))
                paths.append(candidate)
                if neighbor not in path.nodes and hop < request.depth:
                    next_frontier.append(candidate)
            if len(paths) >= request.max_paths:
                break
        depth_examined = hop
        if diagnostics & {"timeout_reached", "expansion_budget_reached", "max_edges_reached", "max_paths_reached"}:
            break
        frontier = next_frontier
    return GraphWalk(
        node_ids=tuple(sorted(nodes)), edges=tuple(sorted(edges.values(), key=lambda item: item.id)),
        paths=tuple(paths), diagnostics=tuple(sorted(diagnostics)),
        depth_examined=depth_examined, nodes_examined=len(queried_nodes),
        edges_examined=edges_examined, backend_queries=backend_queries,
        backend_qtime_ms=qtime if qtime_known else None,
    )


def compose_graph_map(
    request: NavigateGraphRequest, walk: GraphWalk,
    documents: tuple[KnowledgeDocument, ...],
    *, repository: str, revision: str, generation: str,
    coverage: tuple[StructuralCoverage, ...],
) -> GraphNavigationResult:
    """Hydrate each returned edge from its exact canonical source and target."""

    by_id = {doc.id: doc for doc in documents}
    if len(by_id) != len(documents):
        raise KnowledgeSearchError("graph hydration has duplicate documents",
                                   category="schema_mismatch")
    if any(doc.repository != repository or doc.revision != revision for doc in documents):
        raise KnowledgeSearchError("graph hydration conflicts with snapshot",
                                   category="snapshot_revision_mismatch")
    for edge in walk.edges:
        source = by_id.get(edge.source_document_id)
        target = by_id.get(edge.target_document_id)
        if source is None or target is None:
            raise KnowledgeSearchError("graph edge canonical document is absent",
                                       category="snapshot_revision_mismatch")
        if (source.source_ref != edge.source_ref
                or source.provenance.source_checksum != edge.source_checksum
                or target.source_ref != edge.target_source_ref
                or target.path != edge.target_path
                or target.provenance.source_checksum != edge.target_checksum):
            raise KnowledgeSearchError("graph edge provenance conflicts with canonical knowledge",
                                       category="schema_mismatch")
        if edge.source_identity != source.source_ref and not any(
            item.identity is not None and item.identity.key == edge.source_identity
            for item in source.evidence.declarations
        ):
            raise KnowledgeSearchError("graph edge source identity is absent",
                                       category="schema_mismatch")
        if not any(item.identity is not None and item.identity.key == edge.target_identity
                   for item in target.evidence.declarations):
            raise KnowledgeSearchError("graph edge target identity is absent",
                                       category="schema_mismatch")
        if not any(
            (item.source_identity or source.source_ref) == edge.source_identity
            and item.target_identity == edge.target_identity
            and item.target_document_id == edge.target_document_id
            and item.kind == edge.relation_kind and item.target == edge.target_text
            and item.resolution == edge.resolution and item.evidence_basis == edge.evidence_basis
            and item.line == edge.line
            for item in source.evidence.relations.semantic
        ):
            raise KnowledgeSearchError("graph edge has no matching canonical observation",
                                       category="schema_mismatch")

    entities: list[GraphEntity] = []
    for identity in walk.node_ids:
        definitions: list[tuple[KnowledgeDocument, SymbolEvidence]] = [
            (doc, item) for doc in documents for item in doc.evidence.declarations
            if item.identity is not None and item.identity.key == identity
        ]
        if definitions:
            doc, item = min(definitions, key=lambda pair: pair[0].id)
            if any(other.path != doc.path or other.provenance.source_checksum
                   != doc.provenance.source_checksum for other, _ in definitions):
                raise KnowledgeSearchError("graph entity replicas disagree",
                                           category="schema_mismatch")
            entities.append(GraphEntity(
                identity, repository, revision, item.kind, doc.source_ref, doc.path,
                doc.provenance.source_checksum, item.line_start, item.line_end,
                "known" if doc.evidence.extraction else "unknown", doc.evidence.extraction,
            ))
        else:
            file_doc = next((doc for doc in documents if doc.source_ref == identity), None)
            if file_doc is None:
                raise KnowledgeSearchError("graph entity is absent from canonical knowledge",
                                           category="schema_mismatch")
            entities.append(GraphEntity(
                identity, repository, revision, "source", file_doc.source_ref,
                file_doc.path, file_doc.provenance.source_checksum, None, None,
                "known" if file_doc.evidence.extraction else "unknown",
                file_doc.evidence.extraction,
            ))

    visited = set(walk.node_ids)
    diagnostics = set(walk.diagnostics)
    inconclusive: dict[tuple[str, str, str, str, str, int | None], GraphInconclusiveObservation] = {}
    navigation = RepositoryKnowledgeNavigationService(documents)
    for identity in visited:
        if not _SYMBOL_ID.fullmatch(identity):
            continue
        matched_definitions = navigation.find_symbol(identity, limit=2)
        if len(matched_definitions) != 1:
            raise KnowledgeSearchError("graph entity identity is ambiguous or absent",
                                       category="schema_mismatch")
        try:
            related = navigation.get_related(
                matched_definitions[0], relation_types=request.relation_types,
                limit=min(request.max_edges + 1, 100),
            )
        except StructuralNavigationError as exc:
            if exc.category != "unsupported_capability":
                raise KnowledgeSearchError(str(exc), category=exc.category) from exc
            diagnostics.add("relation_extraction_unavailable")
            continue
        if len(related) == min(request.max_edges + 1, 100):
            diagnostics.add("inconclusive_scan_may_be_truncated")
        for relation in related:
            if relation.status == "resolved" and relation.target_identity is not None:
                continue
            observation = GraphInconclusiveObservation(
                relation.source_identity, relation.kind, relation.target,
                relation.resolution, relation.evidence_basis, relation.source_ref,
                relation.line,
            )
            key = (observation.source_identity, observation.kind, observation.target,
                   observation.resolution, observation.source_ref, observation.line)
            inconclusive[key] = observation
    if len(inconclusive) > request.max_edges:
        diagnostics.add("inconclusive_observations_truncated")
    if inconclusive:
        diagnostics.add("inconclusive_observations_present")
    if any(item.status != "supported" for item in coverage):
        diagnostics.add("extraction_coverage_partial")
    target_reached = request.target is not None and any(
        path.nodes[-1] == request.target for path in walk.paths
    )
    status: Literal["success", "partial", "not_found"] = (
        "partial" if diagnostics else "success"
        if (target_reached if request.target is not None else bool(walk.edges))
        else "not_found"
    )
    result = GraphNavigationResult(
        repository, revision, generation, request.seed, request.target,
        target_reached, request.direction,
        request.depth, walk.depth_examined, status, tuple(entities), walk.edges,
        walk.paths, tuple(sorted(inconclusive.values(), key=lambda item: (
            item.source_identity, item.kind, item.target, item.resolution,
            item.source_ref, item.line or 0,
        ))[:request.max_edges]),
        coverage, tuple(sorted(diagnostics)), walk.nodes_examined, walk.edges_examined,
        walk.backend_queries, walk.backend_qtime_ms,
        {"max_nodes": request.max_nodes, "max_edges": request.max_edges,
         "max_paths": request.max_paths, "expansion_budget": request.expansion_budget,
         "timeout_seconds": request.timeout_seconds,
         "max_output_bytes": request.max_output_bytes},
    )
    enforce_output_bound(result, request.max_output_bytes)
    return result


def enforce_output_bound(result: GraphNavigationResult, max_output_bytes: int) -> None:
    size = len(json.dumps(result.to_mapping(), sort_keys=True, ensure_ascii=False).encode())
    if size > max_output_bytes:
        raise KnowledgeSearchError(
            "graph map exceeds output budget", category="output_budget_exceeded",
            metadata={"serialized_bytes": size, "max_output_bytes": max_output_bytes},
        )


def _path_allowed(value: str, repository: str, allowed_paths: tuple[str, ...]) -> bool:
    if not allowed_paths:
        return True
    prefix = f"repository://{repository}/"
    path = value.removeprefix(prefix)
    return any(path == allowed or path.startswith(allowed.rstrip("/") + "/")
               for allowed in allowed_paths)
