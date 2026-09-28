"""Canonical SAP Commerce Spring wiring capability for Repository Knowledge."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.sap_commerce_spring import (
    SapCommerceSpringEvidence,
    SapCommerceSpringUnit,
    extract_sap_commerce_spring_units,
)
from fetchcore.repository.semantic import (
    SemanticEvidenceEnvelope,
    SemanticRelationEvidence,
    decode_semantic_evidence,
    encode_semantic_evidence,
)


class SapCommerceSpringCodec:
    """Typed versioned codec for structural Spring wiring evidence."""

    namespace = "sap.commerce"
    kind = "spring-wiring"
    version = "1"

    def to_payload(
        self,
        value: tuple[SapCommerceSpringUnit, ...],
    ) -> Mapping[str, object]:
        """Serialize canonical Spring wiring units with line provenance."""

        return {"units": [_unit_to_mapping(unit) for unit in value]}

    def from_payload(
        self,
        value: Mapping[str, object],
    ) -> tuple[SapCommerceSpringUnit, ...]:
        """Hydrate exact canonical Spring wiring units fail-closed."""

        if set(value) != {"units"}:
            raise ValueError("SAP Commerce Spring payload requires only the units field")
        raw_units = value.get("units")
        if not isinstance(raw_units, list):
            raise ValueError("SAP Commerce Spring units must be a list")

        units: list[SapCommerceSpringUnit] = []
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, Mapping):
                raise ValueError(f"SAP Commerce Spring unit {index} must be an object")
            expected_fields = {"evidence", "line_start", "line_end"}
            if set(raw_unit) != expected_fields:
                raise ValueError(
                    f"SAP Commerce Spring unit {index} requires exactly {sorted(expected_fields)}"
                )
            raw_evidence = raw_unit.get("evidence")
            if not isinstance(raw_evidence, Mapping):
                raise ValueError(f"SAP Commerce Spring unit {index} evidence must be an object")
            line_start = _positive_line(raw_unit.get("line_start"), index, "line_start")
            line_end = _positive_line(raw_unit.get("line_end"), index, "line_end")
            if line_end < line_start:
                raise ValueError(f"SAP Commerce Spring unit {index} line_end precedes line_start")
            evidence = SapCommerceSpringEvidence.from_mapping(
                cast(Mapping[str, object], raw_evidence)
            )
            _validate_evidence_shape(evidence, index)
            unit = SapCommerceSpringUnit(
                evidence=evidence,
                line_start=line_start,
                line_end=line_end,
            )
            if _unit_to_mapping(unit) != dict(raw_unit):
                raise ValueError(
                    f"SAP Commerce Spring unit {index} is not canonical for version 1"
                )
            units.append(unit)
        return tuple(units)


class SapCommerceSpringEnricher:
    """Project direct Spring structural facts into canonical semantic evidence."""

    indexing_contract_id = "sap.commerce.spring-wiring"
    indexing_contract_version = "1"

    def __init__(self) -> None:
        """Initialize the canonical Spring wiring codec."""

        self.codec = SapCommerceSpringCodec()

    def enrich(self, source: SafeSource, document: KnowledgeDocument) -> KnowledgeDocument:
        """Attach Spring units represented by the current canonical document window."""

        parsed = extract_sap_commerce_spring_units(source.content)
        if not parsed:
            return document
        units = tuple(
            unit
            for unit in parsed
            if document.line_start <= unit.line_start <= document.line_end
        )
        if not units:
            return document

        envelope = encode_semantic_evidence(self.codec, units)
        semantic = _append_semantic(document.evidence.semantic, envelope)
        relations = replace(
            document.evidence.relations,
            semantic=_append_relations(
                document.evidence.relations.semantic,
                _spring_relations(units),
            ),
        )
        return replace(
            document,
            evidence=replace(document.evidence, semantic=semantic, relations=relations),
        )


def sap_commerce_spring_evidence_from_document(
    document: KnowledgeDocument,
) -> tuple[SapCommerceSpringUnit, ...] | None:
    """Hydrate Spring wiring evidence from the canonical semantic surface."""

    codec = SapCommerceSpringCodec()
    matches = tuple(
        item
        for item in document.evidence.semantic
        if (item.namespace, item.kind, item.version)
        == (codec.namespace, codec.kind, codec.version)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("document contains duplicate SAP Commerce Spring evidence")
    return decode_semantic_evidence(codec, matches[0])


def _spring_relations(
    units: tuple[SapCommerceSpringUnit, ...],
) -> tuple[SemanticRelationEvidence, ...]:
    relations: list[SemanticRelationEvidence] = []
    for unit in units:
        evidence = unit.evidence
        if evidence.bean is not None:
            bean = evidence.bean
            if bean.class_name:
                relations.append(_observed_relation("implemented_by", bean.class_name))
            if bean.parent:
                relations.append(_observed_relation("parent_bean", bean.parent))
            if bean.factory_bean:
                relations.append(_observed_relation("factory_bean", bean.factory_bean))
            relations.extend(_observed_relation("depends_on", target) for target in bean.depends_on)
            relations.extend(
                _observed_relation("references", target)
                for target in _bean_explicit_references(bean)
            )
        elif evidence.alias is not None:
            relations.append(_observed_relation("alias_of", evidence.alias.name))
        elif evidence.imported_resource is not None:
            relations.append(_observed_relation("imports", evidence.imported_resource.resource))
        elif evidence.component_scan is not None:
            relations.extend(
                _observed_relation("scans_package", package)
                for package in evidence.component_scan.base_packages
            )
        elif evidence.namespace_wiring is not None:
            relations.extend(
                _observed_relation("references", target)
                for target in evidence.namespace_wiring.referenced_beans
            )
        elif evidence.collection_wiring is not None:
            relations.extend(
                _observed_relation("references", target)
                for target in evidence.collection_wiring.referenced_beans
            )
    return tuple(dict.fromkeys(relations))


def _bean_explicit_references(bean: object) -> tuple[str, ...]:
    from fetchcore.repository.sap_commerce_spring import SapCommerceSpringBeanEvidence

    if not isinstance(bean, SapCommerceSpringBeanEvidence):
        return ()
    values: list[str] = []
    for item in bean.property_references:
        values.extend(item.targets)
    values.extend(bean.constructor_references)
    values.extend(item.target for item in bean.namespace_references)
    return tuple(dict.fromkeys(item for item in values if item))


def _observed_relation(kind: str, target: str) -> SemanticRelationEvidence:
    return SemanticRelationEvidence(
        kind=kind,
        target=target,
        resolution="observed",
        evidence_basis="source",
    )


def _append_semantic(
    existing: tuple[SemanticEvidenceEnvelope, ...],
    value: SemanticEvidenceEnvelope,
) -> tuple[SemanticEvidenceEnvelope, ...]:
    schema = (value.namespace, value.kind, value.version)
    kept = tuple(
        item
        for item in existing
        if (item.namespace, item.kind, item.version) != schema
    )
    return (*kept, value)


def _append_relations(
    existing: tuple[SemanticRelationEvidence, ...],
    values: tuple[SemanticRelationEvidence, ...],
) -> tuple[SemanticRelationEvidence, ...]:
    return tuple(dict.fromkeys((*existing, *values)))


def _unit_to_mapping(unit: SapCommerceSpringUnit) -> dict[str, object]:
    return {
        "evidence": unit.evidence.to_mapping(),
        "line_start": unit.line_start,
        "line_end": unit.line_end,
    }


def _validate_evidence_shape(evidence: SapCommerceSpringEvidence, index: int) -> None:
    fields = {
        "bean": "bean",
        "alias": "alias",
        "import": "imported_resource",
        "component-scan": "component_scan",
        "namespace-wiring": "namespace_wiring",
        "collection-wiring": "collection_wiring",
    }
    expected = fields.get(evidence.kind)
    if expected is None:
        raise ValueError(
            f"SAP Commerce Spring unit {index} has unsupported evidence kind {evidence.kind!r}"
        )
    populated = tuple(field for field in fields.values() if getattr(evidence, field) is not None)
    if populated != (expected,):
        raise ValueError(
            f"SAP Commerce Spring unit {index} evidence kind {evidence.kind!r} "
            f"requires only {expected!r}"
        )
    if not evidence.symbol_name.strip():
        raise ValueError(f"SAP Commerce Spring unit {index} requires a non-empty semantic symbol")


def _positive_line(value: object, index: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"SAP Commerce Spring unit {index} {field} must be a positive integer"
        )
    return value
