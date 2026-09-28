"""Canonical SAP Commerce ImpEx source interpretation and semantic evidence."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import replace

from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.sap_commerce_impex import (
    SapCommerceImpexHeaderEvidence,
    SapCommerceImpexUnit,
    extract_sap_commerce_impex_units,
    render_sap_commerce_impex_unit,
)
from fetchcore.repository.sap_commerce_solr import (
    extract_sap_commerce_solr_units,
    render_sap_commerce_solr_unit,
)
from fetchcore.repository.semantic import (
    SemanticEvidenceEnvelope,
    SemanticRelationEvidence,
    decode_semantic_evidence,
    encode_semantic_evidence,
)
from fetchcore.repository.semantic_indexing import KnowledgeSourceUnit

_SUPPORTED_OPERATIONS = frozenset({"INSERT_UPDATE", "INSERT", "UPDATE", "REMOVE"})
_IMPEX_IDENTIFIER = re.compile(r"^[A-Za-z_@&][A-Za-z0-9_.@&-]*$")
_IMPEX_EXPRESSION = re.compile(r"^[A-Za-z0-9_.$@&(),:\- \t]+$")
_MACRO_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_JAVA_CLASS = re.compile(
    r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$"
)


class SapCommerceImpexCodec:
    """Typed versioned codec for exactly one observed ImpEx header unit."""

    namespace = "sap.commerce"
    kind = "impex-header"
    version = "1"

    def to_payload(self, value: SapCommerceImpexUnit) -> Mapping[str, object]:
        """Serialize one canonical ImpEx header unit."""

        return _unit_to_mapping(value)

    def from_payload(self, value: Mapping[str, object]) -> SapCommerceImpexUnit:
        """Hydrate one exact canonical ImpEx header payload fail-closed."""

        expected = {"evidence", "line_start", "line_end"}
        if set(value) != expected:
            raise ValueError(
                "SAP Commerce ImpEx payload requires exactly evidence, line_start and line_end"
            )
        raw_evidence = value.get("evidence")
        if not isinstance(raw_evidence, Mapping):
            raise ValueError("SAP Commerce ImpEx evidence must be an object")
        line_start = _positive_line(value.get("line_start"), "line_start")
        line_end = _positive_line(value.get("line_end"), "line_end")
        if line_end < line_start:
            raise ValueError("SAP Commerce ImpEx line_end precedes line_start")
        evidence = SapCommerceImpexHeaderEvidence.from_mapping(raw_evidence)
        _validate_header_evidence(evidence)
        unit = SapCommerceImpexUnit(
            evidence=evidence,
            line_start=line_start,
            line_end=line_end,
        )
        if _unit_to_mapping(unit) != dict(value):
            raise ValueError("SAP Commerce ImpEx payload is not canonical for version 1")
        return unit


class SapCommerceImpexSourceInterpreter:
    """Claim ``*.impex`` and emit only sanitized governed source units."""

    indexing_contract_id = "sap.commerce.impex-source"

    def __init__(self, *, include_solr_facts: bool = False) -> None:
        """Configure optional S7 safe-content projection explicitly."""

        self.include_solr_facts = include_solr_facts
        self.indexing_contract_version = "2" if include_solr_facts else "1"

    def interpret(self, source: SafeSource) -> tuple[KnowledgeSourceUnit, ...] | None:
        """Replace raw ImpEx rows with sanitized headers plus allowlisted S7 facts."""

        if not source.relative.casefold().endswith(".impex"):
            return None

        units = extract_sap_commerce_impex_units(source.content)
        solr_by_header: dict[int, list[tuple[int, str]]] = {}
        if self.include_solr_facts:
            for solr_unit in extract_sap_commerce_solr_units(source.content):
                solr_by_header.setdefault(solr_unit.header_line, []).append(
                    (
                        solr_unit.line_end,
                        render_sap_commerce_solr_unit(solr_unit.evidence),
                    )
                )

        occurrences: Counter[tuple[str, str]] = Counter()
        result: list[KnowledgeSourceUnit] = []
        for unit in units:
            key = (unit.evidence.operation, unit.evidence.item_type)
            occurrence = occurrences[key]
            occurrences[key] += 1
            solr_rows = solr_by_header.get(unit.line_start, ())
            result.append(
                KnowledgeSourceUnit(
                    discriminator=(
                        f"sap:impex:{unit.evidence.operation}:{unit.evidence.item_type}:"
                        f"occurrence:{occurrence}"
                    ),
                    content=(
                        render_sap_commerce_impex_unit(unit.evidence)
                        + "".join(rendered for _, rendered in solr_rows)
                    ),
                    line_start=unit.line_start,
                    line_end=max(
                        (line_end for line_end, _ in solr_rows),
                        default=unit.line_end,
                    ),
                    language="impex",
                    knowledge_type="configuration",
                    strategy="sap-commerce-impex-header",
                )
            )
        return tuple(result)


class SapCommerceImpexEnricher:
    """Attach typed ImpEx evidence and only directly observed generic relations."""

    indexing_contract_id = "sap.commerce.impex-header"
    indexing_contract_version = "1"

    def __init__(self) -> None:
        """Initialize the canonical ImpEx evidence codec."""

        self.codec = SapCommerceImpexCodec()

    def enrich(
        self,
        source: SafeSource,
        document: KnowledgeDocument,
    ) -> KnowledgeDocument:
        """Attach typed evidence for the exact ImpEx header represented by the document."""

        if not source.relative.casefold().endswith(".impex"):
            return document
        if document.metadata.get("chunk_strategy") != "sap-commerce-impex-header":
            return document
        matches = tuple(
            unit
            for unit in extract_sap_commerce_impex_units(source.content)
            if unit.line_start == document.line_start
        )
        if not matches:
            return document
        if len(matches) != 1:
            raise ValueError(
                "SAP Commerce ImpEx document line provenance matched multiple header units"
            )
        unit = matches[0]
        envelope = encode_semantic_evidence(self.codec, unit)
        evidence = replace(
            document.evidence,
            semantic=_append_semantic(document.evidence.semantic, envelope),
            relations=replace(
                document.evidence.relations,
                semantic=_append_relations(
                    document.evidence.relations.semantic,
                    _relations(unit.evidence),
                ),
            ),
        )
        return replace(document, evidence=evidence)


def sap_commerce_impex_evidence_from_document(
    document: KnowledgeDocument,
) -> SapCommerceImpexUnit | None:
    """Hydrate the one canonical ImpEx header represented by a document."""

    codec = SapCommerceImpexCodec()
    matches = tuple(
        item
        for item in document.evidence.semantic
        if (item.namespace, item.kind, item.version)
        == (codec.namespace, codec.kind, codec.version)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("document contains duplicate SAP Commerce ImpEx evidence")
    return decode_semantic_evidence(codec, matches[0])


def _relations(
    evidence: SapCommerceImpexHeaderEvidence,
) -> tuple[SemanticRelationEvidence, ...]:
    values = [_observed_relation("targets_type", evidence.item_type)]
    if evidence.processor:
        values.append(_observed_relation("uses_processor", evidence.processor))
    values.extend(
        _observed_relation("uses_translator", column.translator)
        for column in evidence.columns
        if column.translator
    )
    return tuple(dict.fromkeys(values))


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


def _unit_to_mapping(unit: SapCommerceImpexUnit) -> dict[str, object]:
    return {
        "evidence": unit.evidence.to_mapping(),
        "line_start": unit.line_start,
        "line_end": unit.line_end,
    }


def _validate_header_evidence(evidence: SapCommerceImpexHeaderEvidence) -> None:
    if evidence.operation not in _SUPPORTED_OPERATIONS:
        raise ValueError(f"unsupported SAP Commerce ImpEx operation {evidence.operation!r}")
    if _IMPEX_IDENTIFIER.fullmatch(evidence.item_type) is None:
        raise ValueError("SAP Commerce ImpEx item type is not a safe structural identifier")
    if evidence.processor and _JAVA_CLASS.fullmatch(evidence.processor) is None:
        raise ValueError("SAP Commerce ImpEx processor must be a safe Java class reference")
    for macro in evidence.referenced_macros:
        if _MACRO_NAME.fullmatch(macro) is None:
            raise ValueError("SAP Commerce ImpEx referenced macro is invalid")
    for column in evidence.columns:
        if _IMPEX_IDENTIFIER.fullmatch(column.name) is None:
            raise ValueError("SAP Commerce ImpEx column name is not a safe identifier")
        if _IMPEX_EXPRESSION.fullmatch(column.expression) is None:
            raise ValueError("SAP Commerce ImpEx column expression is not structurally safe")
        if any(_IMPEX_IDENTIFIER.fullmatch(item) is None for item in column.reference_path):
            raise ValueError("SAP Commerce ImpEx reference path contains an invalid identifier")
        if column.source_macro and _MACRO_NAME.fullmatch(column.source_macro) is None:
            raise ValueError("SAP Commerce ImpEx source macro is invalid")
        if column.translator and _JAVA_CLASS.fullmatch(column.translator) is None:
            raise ValueError("SAP Commerce ImpEx translator must be a safe Java class reference")
        if any("\r" in key or "\n" in key or not key.strip() for key in column.modifier_keys):
            raise ValueError("SAP Commerce ImpEx column modifier key is invalid")
    if any("\r" in key or "\n" in key or not key.strip() for key in evidence.modifier_keys):
        raise ValueError("SAP Commerce ImpEx header modifier key is invalid")


def _positive_line(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"SAP Commerce ImpEx {field} must be a positive integer")
    return value
