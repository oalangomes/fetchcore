"""Exact-unique SAP Commerce resolution over canonical Repository Knowledge.

The resolver consumes only already-canonical evidence from the complete indexed
corpus. It never performs fuzzy matching, suffix guessing, product-origin
inference, or backend-specific graph construction. Ambiguous and missing targets
remain unresolved.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace

from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.sap_commerce import SapCommerceEvidence
from fetchcore.repository.sap_commerce_items import SapCommerceTypeSystemEvidence
from fetchcore.repository.sap_commerce_solr import SapCommerceSolrEvidence
from fetchcore.repository.sap_commerce_spring import SapCommerceSpringEvidence
from fetchcore.repository.semantic import SemanticRelationEvidence


_TYPE_RELATION_KINDS = frozenset(
    {
        "extends",
        "typed_as",
        "source_type",
        "target_type",
        "element_type",
        "argument_type",
        "return_type",
    }
)
_SPRING_BEAN_RELATION_KINDS = frozenset(
    {"parent_bean", "factory_bean", "depends_on", "references", "alias_of"}
)
_SPRING_JAVA_RELATION_KINDS = frozenset({"implemented_by"})
_IMPEX_TYPE_RELATION_KINDS = frozenset({"targets_type"})
_IMPEX_JAVA_RELATION_KINDS = frozenset({"uses_processor", "uses_translator"})
_SOLR_TYPE_RELATION_KINDS = frozenset({"indexes_type"})
_SOLR_BEAN_RELATION_KINDS = frozenset({"uses_value_provider"})
_SOLR_INDEXED_TYPE_RELATION_KINDS = frozenset(
    {"belongs_to_indexed_type", "uses_indexed_type"}
)
_TYPE_DECLARATION_KINDS = frozenset(
    {"item-type", "enum-type", "collection-type", "map-type"}
)
_JAVA_DECLARATION_KINDS = frozenset({"class", "interface", "enum", "record"})


@dataclass(frozen=True)
class _Declaration:
    identity: tuple[object, ...]
    document: KnowledgeDocument


class SapCommerceCorpusResolver:
    """Resolve SAP Commerce cross-source relations by exact canonical identity only."""

    indexing_contract_id = "sap.commerce.exact-cross-source"
    indexing_contract_version = "1"

    def resolve(
        self,
        documents: tuple[KnowledgeDocument, ...],
    ) -> tuple[KnowledgeDocument, ...]:
        """Attach exact-unique provenance to relations supported by canonical facts."""

        type_index = _type_declaration_index(documents)
        bean_index = _spring_bean_index(documents)
        java_index = _java_declaration_index(documents)
        extension_index = _extension_declaration_index(documents)
        solr_indexed_type_index = _solr_indexed_type_index(documents)

        resolved_documents: list[KnowledgeDocument] = []
        for document in documents:
            derived: list[SemanticRelationEvidence] = []
            if _has_schema(document, "type-system"):
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_TYPE_RELATION_KINDS,
                        index=type_index,
                    )
                )
            if _has_schema(document, "spring-wiring"):
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_SPRING_BEAN_RELATION_KINDS,
                        index=bean_index,
                    )
                )
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_SPRING_JAVA_RELATION_KINDS,
                        index=java_index,
                    )
                )
            if _has_schema(document, "impex-header"):
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_IMPEX_TYPE_RELATION_KINDS,
                        index=type_index,
                    )
                )
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_IMPEX_JAVA_RELATION_KINDS,
                        index=java_index,
                    )
                )
            if _has_schema(document, "solr-facet-search"):
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_SOLR_TYPE_RELATION_KINDS,
                        index=type_index,
                    )
                )
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_SOLR_BEAN_RELATION_KINDS,
                        index=bean_index,
                    )
                )
                derived.extend(
                    _resolve_existing_relations(
                        document,
                        kinds=_SOLR_INDEXED_TYPE_RELATION_KINDS,
                        index=solr_indexed_type_index,
                    )
                )
            extension_dependencies = _extension_dependencies(document)
            for target in extension_dependencies:
                relation = _resolved_relation(
                    kind="depends_on",
                    target=target,
                    declaration=_unique_declaration(extension_index.get(target, ())),
                )
                if relation is not None:
                    derived.append(relation)

            if not derived:
                resolved_documents.append(document)
                continue
            relations = replace(
                document.evidence.relations,
                semantic=tuple(
                    dict.fromkeys((*document.evidence.relations.semantic, *derived))
                ),
            )
            resolved_documents.append(
                replace(
                    document,
                    evidence=replace(document.evidence, relations=relations),
                )
            )
        return tuple(resolved_documents)


def _resolve_existing_relations(
    document: KnowledgeDocument,
    *,
    kinds: frozenset[str],
    index: Mapping[str, tuple[_Declaration, ...]],
) -> tuple[SemanticRelationEvidence, ...]:
    resolved: list[SemanticRelationEvidence] = []
    for relation in document.evidence.relations.semantic:
        if relation.resolution != "observed" or relation.kind not in kinds:
            continue
        derived = _resolved_relation(
            kind=relation.kind,
            target=relation.target,
            declaration=_unique_declaration(index.get(relation.target, ())),
        )
        if derived is not None:
            resolved.append(derived)
    return tuple(resolved)


def _resolved_relation(
    *,
    kind: str,
    target: str,
    declaration: _Declaration | None,
) -> SemanticRelationEvidence | None:
    if declaration is None:
        return None
    document = declaration.document
    return SemanticRelationEvidence(
        kind=kind,
        target=target,
        resolution="exact-unique",
        evidence_basis="canonical-corpus",
        target_document_id=document.id,
        target_source_ref=document.source_ref,
        target_path=document.path,
    )


def _type_declaration_index(
    documents: tuple[KnowledgeDocument, ...],
) -> dict[str, tuple[_Declaration, ...]]:
    values: dict[str, list[_Declaration]] = defaultdict(list)
    for document in documents:
        for evidence, line_start, line_end in _type_system_units(document):
            if evidence.kind not in _TYPE_DECLARATION_KINDS:
                continue
            symbol = evidence.symbol_name
            if not symbol or symbol == "unknown":
                continue
            values[symbol].append(
                _Declaration(
                    identity=(
                        "sap-type",
                        document.source_ref,
                        line_start,
                        line_end,
                        evidence.kind,
                        symbol,
                    ),
                    document=document,
                )
            )
    return _freeze_index(values)


def _spring_bean_index(
    documents: tuple[KnowledgeDocument, ...],
) -> dict[str, tuple[_Declaration, ...]]:
    direct: dict[str, list[_Declaration]] = defaultdict(list)
    alias_targets: dict[str, set[str]] = defaultdict(set)
    for document in documents:
        for evidence, line_start, line_end in _spring_units(document):
            if evidence.bean is not None and evidence.bean.identity_kind == "named":
                bean = evidence.bean
                bean_declaration = _Declaration(
                    identity=(
                        "spring-bean",
                        document.source_ref,
                        line_start,
                        line_end,
                        bean.name,
                    ),
                    document=document,
                )
                direct[bean.name].append(bean_declaration)
                for alias in bean.aliases:
                    direct[alias].append(bean_declaration)
            elif evidence.alias is not None:
                alias_targets[evidence.alias.alias].add(evidence.alias.name)

    def resolve_alias(name: str, trail: frozenset[str]) -> tuple[_Declaration, ...]:
        values: list[_Declaration] = list(direct.get(name, ()))
        if name in trail:
            return _dedupe_declarations(values)
        next_trail = frozenset((*trail, name))
        for target in sorted(alias_targets.get(name, ())):
            if target in next_trail:
                continue
            values.extend(resolve_alias(target, next_trail))
        return _dedupe_declarations(values)

    names = set(direct) | set(alias_targets)
    return {name: resolve_alias(name, frozenset()) for name in sorted(names)}


def _java_declaration_index(
    documents: tuple[KnowledgeDocument, ...],
) -> dict[str, tuple[_Declaration, ...]]:
    values: dict[str, list[_Declaration]] = defaultdict(list)
    for document in documents:
        symbol = document.evidence.symbol
        if document.language != "java" or symbol is None:
            continue
        if symbol.kind not in _JAVA_DECLARATION_KINDS:
            continue
        names: list[str] = []
        if symbol.qualified_name:
            names.append(symbol.qualified_name)
        package = document.evidence.structure.package
        if package and symbol.name:
            names.append(f"{package}.{symbol.name}")
        for name in dict.fromkeys(names):
            values[name].append(
                _Declaration(
                    identity=(
                        "java-type",
                        document.source_ref,
                        symbol.line_start,
                        symbol.line_end,
                        symbol.kind,
                        name,
                    ),
                    document=document,
                )
            )
    return _freeze_index(values)


def _solr_indexed_type_index(
    documents: tuple[KnowledgeDocument, ...],
) -> dict[str, tuple[_Declaration, ...]]:
    values: dict[str, list[_Declaration]] = defaultdict(list)
    for document in documents:
        for evidence, line_start, line_end in _solr_units(document):
            if evidence.kind != "indexed-type" or evidence.indexed_type is None:
                continue
            identifier = evidence.indexed_type.identifier
            if not identifier:
                continue
            values[identifier].append(
                _Declaration(
                    identity=(
                        "sap-solr-indexed-type",
                        document.source_ref,
                        line_start,
                        line_end,
                        identifier,
                    ),
                    document=document,
                )
            )
    return _freeze_index(values)


def _extension_declaration_index(
    documents: tuple[KnowledgeDocument, ...],
) -> dict[str, tuple[_Declaration, ...]]:
    values: dict[str, list[_Declaration]] = defaultdict(list)
    for document in documents:
        topology = _topology_evidence(document)
        if topology is None or topology.extension is None:
            continue
        name = topology.extension.name
        if not name:
            continue
        values[name].append(
            _Declaration(
                identity=("sap-extension", document.source_ref, name),
                document=document,
            )
        )
    return _freeze_index(values)


def _extension_dependencies(document: KnowledgeDocument) -> tuple[str, ...]:
    topology = _topology_evidence(document)
    if topology is None or topology.extension is None:
        return ()
    return tuple(item for item in topology.extension.requires_extensions if item)


def _topology_evidence(document: KnowledgeDocument) -> SapCommerceEvidence | None:
    payloads = _semantic_payloads(document, "extension-topology")
    if not payloads:
        return None
    if len(payloads) != 1:
        raise ValueError("document contains duplicate SAP Commerce topology evidence")
    evidence = SapCommerceEvidence.from_mapping(payloads[0])
    if evidence.to_mapping() != dict(payloads[0]):
        raise ValueError("SAP Commerce topology evidence is not canonical")
    return evidence


def _type_system_units(
    document: KnowledgeDocument,
) -> tuple[tuple[SapCommerceTypeSystemEvidence, int, int], ...]:
    result: list[tuple[SapCommerceTypeSystemEvidence, int, int]] = []
    for payload in _semantic_payloads(document, "type-system"):
        for raw_unit in _required_units(payload, "type-system"):
            raw_evidence = raw_unit.get("evidence")
            if not isinstance(raw_evidence, Mapping):
                raise ValueError("SAP Commerce type-system unit evidence must be an object")
            evidence = SapCommerceTypeSystemEvidence.from_mapping(raw_evidence)
            if evidence.to_mapping() != dict(raw_evidence):
                raise ValueError("SAP Commerce type-system evidence is not canonical")
            result.append(
                (
                    evidence,
                    _required_positive_line(raw_unit, "line_start", "type-system"),
                    _required_positive_line(raw_unit, "line_end", "type-system"),
                )
            )
    return tuple(result)


def _spring_units(
    document: KnowledgeDocument,
) -> tuple[tuple[SapCommerceSpringEvidence, int, int], ...]:
    result: list[tuple[SapCommerceSpringEvidence, int, int]] = []
    for payload in _semantic_payloads(document, "spring-wiring"):
        for raw_unit in _required_units(payload, "Spring"):
            raw_evidence = raw_unit.get("evidence")
            if not isinstance(raw_evidence, Mapping):
                raise ValueError("SAP Commerce Spring unit evidence must be an object")
            evidence = SapCommerceSpringEvidence.from_mapping(raw_evidence)
            if evidence.to_mapping() != dict(raw_evidence):
                raise ValueError("SAP Commerce Spring evidence is not canonical")
            result.append(
                (
                    evidence,
                    _required_positive_line(raw_unit, "line_start", "Spring"),
                    _required_positive_line(raw_unit, "line_end", "Spring"),
                )
            )
    return tuple(result)


def _solr_units(
    document: KnowledgeDocument,
) -> tuple[tuple[SapCommerceSolrEvidence, int, int], ...]:
    result: list[tuple[SapCommerceSolrEvidence, int, int]] = []
    for payload in _semantic_payloads(document, "solr-facet-search"):
        for raw_unit in _required_units(payload, "Solr"):
            raw_evidence = raw_unit.get("evidence")
            if not isinstance(raw_evidence, Mapping):
                raise ValueError("SAP Commerce Solr unit evidence must be an object")
            evidence = SapCommerceSolrEvidence.from_mapping(raw_evidence)
            if evidence.to_mapping() != dict(raw_evidence):
                raise ValueError("SAP Commerce Solr evidence is not canonical")
            result.append(
                (
                    evidence,
                    _required_positive_line(raw_unit, "line_start", "Solr"),
                    _required_positive_line(raw_unit, "line_end", "Solr"),
                )
            )
    return tuple(result)


def _required_units(
    payload: Mapping[str, object],
    subject: str,
) -> tuple[Mapping[str, object], ...]:
    raw_units = payload.get("units")
    if not isinstance(raw_units, list):
        raise ValueError(f"SAP Commerce {subject} payload requires a units list")
    units: list[Mapping[str, object]] = []
    for raw_unit in raw_units:
        if not isinstance(raw_unit, Mapping):
            raise ValueError(f"SAP Commerce {subject} unit must be an object")
        units.append(raw_unit)
    return tuple(units)


def _required_positive_line(
    value: Mapping[str, object],
    field: str,
    subject: str,
) -> int:
    raw = value.get(field)
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError(f"SAP Commerce {subject} {field} must be a positive integer")
    return raw


def _semantic_payloads(
    document: KnowledgeDocument,
    kind: str,
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        envelope.payload()
        for envelope in document.evidence.semantic
        if envelope.namespace == "sap.commerce"
        and envelope.kind == kind
        and envelope.version == "1"
    )


def _has_schema(document: KnowledgeDocument, kind: str) -> bool:
    return any(
        envelope.namespace == "sap.commerce"
        and envelope.kind == kind
        and envelope.version == "1"
        for envelope in document.evidence.semantic
    )


def _freeze_index(
    values: Mapping[str, Iterable[_Declaration]],
) -> dict[str, tuple[_Declaration, ...]]:
    return {
        key: _dedupe_declarations(items)
        for key, items in values.items()
    }


def _dedupe_declarations(
    values: Iterable[_Declaration],
) -> tuple[_Declaration, ...]:
    by_identity: dict[tuple[object, ...], _Declaration] = {}
    for value in values:
        previous = by_identity.get(value.identity)
        if previous is None or _representative_key(value.document) < _representative_key(
            previous.document
        ):
            by_identity[value.identity] = value
    return tuple(by_identity[key] for key in sorted(by_identity, key=repr))


def _unique_declaration(values: Iterable[_Declaration]) -> _Declaration | None:
    unique = _dedupe_declarations(values)
    return unique[0] if len(unique) == 1 else None


def _representative_key(document: KnowledgeDocument) -> tuple[int, str]:
    span = max(0, document.line_end - document.line_start)
    return span, document.chunk_ref


__all__ = ["SapCommerceCorpusResolver"]
