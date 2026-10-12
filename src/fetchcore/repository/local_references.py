"""Bounded, exact joins between already admitted canonical evidence."""

from __future__ import annotations

import json
import posixpath
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from urllib.parse import unquote, urlsplit

from fetchcore.repository.api_contracts import ApiEvidenceV1
from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.semantic import SemanticEvidenceEnvelope, SemanticRelationEvidence


@dataclass(frozen=True)
class LocalResolutionObservationV1:
    """Closed diagnostic contract; only a resolved observation permits a link."""

    kind: str
    declaration: str
    source_locator: str | None
    status: str
    evidence_basis: str
    candidate_count: int
    target_document_id: str | None = None
    target_source_ref: str | None = None
    target_path: str | None = None
    target_revision: str | None = None
    target_checksum: str | None = None
    target_locator: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"api_reference", "markdown_reference", "route_correspondence"}:
            raise ValueError("local reference kind is unsupported")
        if self.status not in {"observed", "resolved", "unresolved", "ambiguous",
                               "truncated", "unsupported", "blocked"}:
            raise ValueError("local reference status is unsupported")
        if not self.declaration or not self.evidence_basis:
            raise ValueError("local reference declaration and basis are required")
        if type(self.candidate_count) is not int or self.candidate_count < 0:
            raise ValueError("local reference candidate count is invalid")
        if self.status != "resolved" and self.target_document_id is not None:
            raise ValueError("inconclusive reference cannot name a target document")
        if self.target_document_id is not None and (
            self.target_source_ref is None or self.target_path is None
            or self.target_revision is None or self.target_checksum is None
        ):
            raise ValueError("resolved target provenance is incomplete")

    def to_envelope(self) -> SemanticEvidenceEnvelope:
        return SemanticEvidenceEnvelope.from_payload(
            namespace="local_reference", kind="resolution", version="1",
            payload=asdict(self),
        )

    @classmethod
    def from_envelope(cls, value: SemanticEvidenceEnvelope) -> LocalResolutionObservationV1:
        if value.schema_id != "local_reference/resolution@1":
            raise ValueError("local reference schema is incompatible")
        payload = value.payload()
        if set(payload) != set(cls.__dataclass_fields__):
            raise ValueError("local reference payload has missing or unknown fields")
        if any(not isinstance(payload[key], str) for key in
               ("kind", "declaration", "status", "evidence_basis")):
            raise ValueError("local reference required strings are invalid")
        if any(payload[key] is not None and not isinstance(payload[key], str)
               for key in ("source_locator", "target_document_id", "target_source_ref",
                           "target_path", "target_revision", "target_checksum",
                           "target_locator")):
            raise ValueError("local reference optional strings are invalid")
        if type(payload["candidate_count"]) is not int:
            raise ValueError("local reference candidate count is invalid")
        return cls(**payload)  # type: ignore[arg-type]


@dataclass(frozen=True)
class LocalReferenceBudget:
    """Hard bounds for one corpus resolution pass. No source files are opened."""

    max_references: int = 10_000
    max_candidates: int = 32
    max_relations: int = 10_000
    max_documents: int = 100_000
    max_evidence_bytes: int = 16_000_000

    def __post_init__(self) -> None:
        if min(self.max_references, self.max_candidates, self.max_relations,
               self.max_documents, self.max_evidence_bytes) < 1:
            raise ValueError("local reference budgets must be positive")


class LocalReferenceResolver:
    """Resolve one hop from exact canonical facts within a repository revision.

    The pass never follows a target's references, so cycles cannot expand work.
    Its indexes are transient and its output is derived from the current corpus.
    """

    indexing_contract_id = "fetchcore.local-reference"
    indexing_contract_version = "1"

    def __init__(self, budget: LocalReferenceBudget | None = None) -> None:
        self.budget = budget or LocalReferenceBudget()
        self.indexing_contract_version = (
            "1:" + ":".join(str(value) for value in (
                self.budget.max_references, self.budget.max_candidates,
                self.budget.max_relations, self.budget.max_documents,
                self.budget.max_evidence_bytes,
            ))
        )

    def resolve(self, documents: tuple[KnowledgeDocument, ...]) -> tuple[KnowledgeDocument, ...]:
        budget = self.budget
        if len(documents) > budget.max_documents:
            raise ValueError("local reference document budget exceeded")
        # A document's sanitized evidence and reference lists are bounded at their
        # extraction boundary. Bound the aggregate before building any indexes.
        evidence_bytes = sum(len(json.dumps(doc.evidence.to_mapping(), sort_keys=True,
                                            separators=(",", ":")).encode("utf-8"))
                             for doc in documents)
        if evidence_bytes > budget.max_evidence_bytes:
            raise ValueError("local reference evidence byte budget exceeded")

        paths: dict[tuple[str, str, str], list[KnowledgeDocument]] = defaultdict(list)
        schemas: dict[tuple[str, str, str, str], list[KnowledgeDocument]] = defaultdict(list)
        sections: dict[tuple[str, str, str, str], list[KnowledgeDocument]] = defaultdict(list)
        routes: dict[tuple[str, str, str, str], list[KnowledgeDocument]] = defaultdict(list)
        facts: dict[str, ApiEvidenceV1] = {}
        for doc in documents:
            scope = (doc.repository, doc.revision, doc.path)
            paths[scope].append(doc)
            for envelope in doc.evidence.semantic:
                if envelope.namespace != "api_contract" or envelope.version != "1":
                    continue
                api_fact = ApiEvidenceV1.from_envelope(envelope)
                if api_fact.locator.path != doc.path:
                    raise ValueError("API evidence path disagrees with canonical provenance")
                facts[doc.id] = api_fact
                if api_fact.kind == "api_schema":
                    schemas[(*scope, api_fact.locator.pointer)].append(doc)
            section = doc.evidence.section
            if section is not None and section.fragment_id is not None:
                sections[(*scope, section.fragment_id)].append(doc)
            for route in doc.evidence.behavior.routes:
                if (route.http_method and route.path.startswith("/")
                        and route.resolution == "framework_pattern"
                        and route.mechanism and route.mechanism.startswith("@app.")
                        and route.line is not None):
                    routes[(doc.repository, doc.revision,
                            route.http_method.upper(), route.path)].append(doc)

        examined = 0
        emitted = 0
        result: list[KnowledgeDocument] = []
        for doc in documents:
            observations: list[SemanticEvidenceEnvelope] = []
            relations: list[SemanticRelationEvidence] = []

            def record(kind: str, declaration: str, locator: str | None,
                       candidates: list[KnowledgeDocument], status: str,
                       basis: str, target_path: str | None = None,
                       candidate_count: int | None = None) -> None:
                nonlocal examined, emitted
                if examined > budget.max_references:
                    return
                examined += 1
                candidate_count = len(candidates) if candidate_count is None else candidate_count
                if examined > budget.max_references or candidate_count > budget.max_candidates:
                    status = "truncated"
                    candidates = []
                if status == "resolved" and emitted >= budget.max_relations:
                    status = "truncated"
                    candidates = []
                target = candidates[0] if status == "resolved" and len(candidates) == 1 else None
                target_locator: str | None = None
                if target is not None:
                    target_fact = facts.get(target.id)
                    if kind == "api_reference" and target_fact is not None:
                        target_locator = target_fact.locator.pointer
                    elif kind == "markdown_reference" and target.evidence.section is not None:
                        target_locator = target.evidence.section.section_id
                    elif kind == "route_correspondence":
                        route_matches = [route for route in target.evidence.behavior.routes
                                         if route.http_method and route.path
                                         and f"{route.http_method} {route.path}" == declaration
                                         and route.mechanism and route.mechanism.startswith("@app.")]
                        if len(route_matches) == 1 and route_matches[0].line is not None:
                            target_locator = str(route_matches[0].line)
                observations.append(LocalResolutionObservationV1(  # noqa: B023
                    kind=kind, declaration=declaration, source_locator=locator,
                    status=status, evidence_basis=basis, candidate_count=candidate_count,
                    target_document_id=target.id if target else None,
                    target_source_ref=target.source_ref if target else None,
                    target_path=target.path if target else target_path,
                    target_revision=target.revision if target else None,
                    target_checksum=target.checksum if target else None,
                    target_locator=target_locator,
                ).to_envelope())
                if status == "resolved":
                    emitted += 1
                    relations.append(SemanticRelationEvidence(  # noqa: B023
                        kind=kind, target=declaration, resolution="resolved",
                        evidence_basis=basis,
                        target_document_id=target.id if target else None,
                        target_source_ref=target.source_ref if target else None,
                        target_path=target.path if target else target_path,
                    ))

            fact = facts.get(doc.id)
            if fact is not None and fact.kind == "api_reference" and fact.target:
                target_path, sep, pointer = fact.target.partition("#")
                if not sep or not pointer.startswith("/"):
                    record("api_reference", fact.target, fact.locator.pointer,
                           [], "unsupported", "api_pointer")
                else:
                    matches = schemas.get((doc.repository, doc.revision,
                                           target_path or doc.path,
                                           "#" + pointer), [])
                    status = _candidate_status(matches)
                    record("api_reference", fact.target, fact.locator.pointer,
                           matches, status, "exact_api_pointer")
            if fact is not None and fact.kind == "api_operation" and fact.method and fact.route:
                matches = routes.get((doc.repository, doc.revision,
                                      fact.method, fact.route), [])
                record("route_correspondence", f"{fact.method} {fact.route}",
                       fact.locator.pointer, matches, _candidate_status(matches),
                       "observed_method_and_path")

            section = doc.evidence.section
            if section is not None:
                for reference in section.references:
                    if reference.kind == "external":
                        record("markdown_reference", reference.target,
                               str(reference.line) if reference.line else None,
                               [], "blocked", "external_url")
                        continue
                    path, path_status = _local_path(doc.path, reference.target)
                    if path_status is not None or path is None:
                        record("markdown_reference", reference.target,
                               str(reference.line) if reference.line else None,
                               [], path_status or "unsupported", "canonical_path")
                        continue
                    fragment = urlsplit(reference.target).fragment
                    scope = (doc.repository, doc.revision, path)
                    file_docs = paths.get(scope, [])
                    if not file_docs:
                        record("markdown_reference", reference.target,
                               str(reference.line) if reference.line else None,
                               [], "unresolved", "canonical_path")
                    elif fragment:
                        matches = sections.get((*scope, fragment), [])
                        unique_sections = {candidate.evidence.section.section_id
                                           for candidate in matches
                                           if candidate.evidence.section is not None}
                        selected = ([min(matches, key=lambda item: (item.line_start, item.id))]
                                    if len(unique_sections) == 1 and matches else matches)
                        # A section split into chunks still has one section ID.
                        # Choose its first canonical chunk, while charging the
                        # full exact-bucket candidate count to the budget.
                        record("markdown_reference", reference.target,
                               str(reference.line) if reference.line else None,
                               selected, _candidate_status(selected),
                               "fetchcore_fragment_id", target_path=path,
                               candidate_count=len(matches))
                    else:
                        # Path identity proves the file, while chunk identity is
                        # deliberately left empty when a file has several chunks.
                        record("markdown_reference", reference.target,
                               str(reference.line) if reference.line else None,
                               file_docs,
                               "resolved", "canonical_path", target_path=path)

            if observations or relations:
                evidence = replace(doc.evidence,
                    semantic=doc.evidence.semantic + tuple(observations),
                    relations=replace(doc.evidence.relations,
                        semantic=doc.evidence.relations.semantic + tuple(relations)))
                result.append(replace(doc, evidence=evidence))
            else:
                result.append(doc)
        return tuple(result)


def _candidate_status(matches: list[KnowledgeDocument]) -> str:
    if not matches:
        return "unresolved"
    return "resolved" if len(matches) == 1 else "ambiguous"


def _local_path(source: str, target: str) -> tuple[str | None, str | None]:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or target.startswith(("/", "\\")):
        return None, "blocked"
    decoded = unquote(parsed.path)
    if not decoded:
        return source, None
    if "\\" in decoded or "\x00" in decoded or decoded.startswith("/"):
        return None, "blocked"
    parts = (posixpath.dirname(source) + "/" + decoded).split("/")
    depth = 0
    for part in parts:
        if part == "..":
            depth -= 1
        elif part not in {"", "."}:
            depth += 1
        if depth < 0:
            return None, "blocked"
    path = posixpath.normpath(posixpath.join(posixpath.dirname(source), decoded))
    return (path if path not in {"", "."} else source), None
