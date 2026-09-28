"""Canonical SAP Commerce Solr facet-search semantic enrichment."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.sap_commerce_solr import (
    SapCommerceSolrEvidence,
    SapCommerceSolrUnit,
    extract_sap_commerce_solr_units,
)
from fetchcore.repository.semantic import (
    SemanticEvidenceEnvelope,
    SemanticRelationEvidence,
    decode_semantic_evidence,
    encode_semantic_evidence,
)

_SAFE_ATOM = re.compile(r"^[A-Za-z0-9_.:-]+$")
_SUPPORTED_KINDS = {
    "indexed-type": "indexed_type",
    "indexed-property": "indexed_property",
    "facet-search-config": "facet_search_config",
}
_SUPPORTED_OPERATIONS = frozenset({"INSERT_UPDATE", "INSERT"})


class SapCommerceSolrFacetSearchCodec:
    """Typed v1 codec for allowlisted SAP Commerce Solr facet-search evidence."""

    namespace = "sap.commerce"
    kind = "solr-facet-search"
    version = "1"

    def to_payload(
        self,
        value: tuple[SapCommerceSolrUnit, ...],
    ) -> Mapping[str, object]:
        """Serialize S7 units after strict canonical validation."""

        units: list[dict[str, object]] = []
        for index, unit in enumerate(value):
            if not isinstance(unit, SapCommerceSolrUnit):
                raise TypeError(f"SAP Commerce Solr unit {index} has invalid type")
            _validate_unit(unit, index)
            units.append(_unit_to_mapping(unit))
        return {"units": units}

    def from_payload(
        self,
        value: Mapping[str, object],
    ) -> tuple[SapCommerceSolrUnit, ...]:
        """Hydrate only the exact canonical S7 payload supported by v1."""

        if set(value) != {"units"}:
            raise ValueError("SAP Commerce Solr payload requires only the units field")
        raw_units = value.get("units")
        if not isinstance(raw_units, list):
            raise ValueError("SAP Commerce Solr units must be a list")

        units: list[SapCommerceSolrUnit] = []
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, Mapping):
                raise ValueError(f"SAP Commerce Solr unit {index} must be an object")
            expected_fields = {"evidence", "header_line", "line_start", "line_end"}
            if set(raw_unit) != expected_fields:
                raise ValueError(
                    f"SAP Commerce Solr unit {index} requires exactly "
                    f"{sorted(expected_fields)}"
                )
            raw_evidence = raw_unit.get("evidence")
            if not isinstance(raw_evidence, Mapping):
                raise ValueError(
                    f"SAP Commerce Solr unit {index} evidence must be an object"
                )
            evidence = SapCommerceSolrEvidence.from_mapping(
                cast(Mapping[str, object], raw_evidence)
            )
            unit = SapCommerceSolrUnit(
                evidence=evidence,
                header_line=_positive_line(
                    raw_unit.get("header_line"), index, "header_line"
                ),
                line_start=_positive_line(
                    raw_unit.get("line_start"), index, "line_start"
                ),
                line_end=_positive_line(raw_unit.get("line_end"), index, "line_end"),
            )
            _validate_unit(unit, index)
            if _unit_to_mapping(unit) != dict(raw_unit):
                raise ValueError(
                    f"SAP Commerce Solr unit {index} is not canonical for version 1"
                )
            units.append(unit)
        return tuple(units)


class SapCommerceSolrFacetSearchEnricher:
    """Attach S7 evidence to sanitized ImpEx header documents only."""

    indexing_contract_id = "sap.commerce.solr-facet-search"
    indexing_contract_version = "1"

    def __init__(self) -> None:
        """Initialize the S7 codec and one-source deterministic parse cache."""

        self.codec = SapCommerceSolrFacetSearchCodec()
        self._cached_source_key: tuple[str, str] | None = None
        self._cached_units: tuple[SapCommerceSolrUnit, ...] = ()

    def enrich(
        self,
        source: SafeSource,
        document: KnowledgeDocument,
    ) -> KnowledgeDocument:
        """Attach only allowlisted Solr rows owned by this document's ImpEx header."""

        if not source.relative.casefold().endswith(".impex"):
            return document
        if document.metadata.get("chunk_strategy") != "sap-commerce-impex-header":
            return document
        units = tuple(
            unit
            for unit in self._units_for_source(source)
            if unit.header_line == document.line_start
        )
        if not units:
            return document

        envelope = encode_semantic_evidence(self.codec, units)
        evidence = replace(
            document.evidence,
            semantic=_append_semantic(document.evidence.semantic, envelope),
            relations=replace(
                document.evidence.relations,
                semantic=_append_relations(
                    document.evidence.relations.semantic,
                    _relations(units),
                ),
            ),
        )
        return replace(document, evidence=evidence)

    def _units_for_source(
        self,
        source: SafeSource,
    ) -> tuple[SapCommerceSolrUnit, ...]:
        """Parse each source checksum once while preserving deterministic output."""

        key = (source.relative, source.checksum)
        if self._cached_source_key != key:
            units = extract_sap_commerce_solr_units(source.content)
            self._cached_source_key = key
            self._cached_units = units
        return self._cached_units


def sap_commerce_solr_evidence_from_document(
    document: KnowledgeDocument,
) -> tuple[SapCommerceSolrUnit, ...] | None:
    """Hydrate S7 evidence from the canonical semantic surface."""

    codec = SapCommerceSolrFacetSearchCodec()
    matches = tuple(
        item
        for item in document.evidence.semantic
        if (item.namespace, item.kind, item.version)
        == (codec.namespace, codec.kind, codec.version)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("document contains duplicate SAP Commerce Solr evidence")
    return decode_semantic_evidence(codec, matches[0])


def _relations(
    units: tuple[SapCommerceSolrUnit, ...],
) -> tuple[SemanticRelationEvidence, ...]:
    values: list[SemanticRelationEvidence] = []
    for unit in units:
        evidence = unit.evidence
        if evidence.indexed_type is not None:
            observed = evidence.indexed_type
            if observed.item_type:
                values.append(_observed_relation("indexes_type", observed.item_type))
            if observed.default_field_value_provider:
                values.append(
                    _observed_relation(
                        "uses_value_provider",
                        observed.default_field_value_provider,
                    )
                )
        elif evidence.indexed_property is not None:
            observed_property = evidence.indexed_property
            if observed_property.indexed_type:
                values.append(
                    _observed_relation(
                        "belongs_to_indexed_type",
                        observed_property.indexed_type,
                    )
                )
            if observed_property.field_value_provider:
                values.append(
                    _observed_relation(
                        "uses_value_provider",
                        observed_property.field_value_provider,
                    )
                )
        elif evidence.facet_search_config is not None:
            values.extend(
                _observed_relation("uses_indexed_type", target)
                for target in evidence.facet_search_config.indexed_types
            )
    return tuple(dict.fromkeys(values))


def _observed_relation(kind: str, target: str) -> SemanticRelationEvidence:
    return SemanticRelationEvidence(
        kind=kind,
        target=target,
        resolution="observed",
        evidence_basis="source",
    )


def _validate_unit(unit: SapCommerceSolrUnit, index: int) -> None:
    if unit.evidence.operation not in _SUPPORTED_OPERATIONS:
        raise ValueError(
            f"SAP Commerce Solr unit {index} has unsupported operation "
            f"{unit.evidence.operation!r}"
        )
    _positive_line(unit.header_line, index, "header_line")
    _positive_line(unit.line_start, index, "line_start")
    _positive_line(unit.line_end, index, "line_end")
    if unit.line_end < unit.line_start:
        raise ValueError(f"SAP Commerce Solr unit {index} line_end precedes line_start")
    if unit.header_line > unit.line_start:
        raise ValueError(
            f"SAP Commerce Solr unit {index} header_line follows its data row"
        )

    expected_field = _SUPPORTED_KINDS.get(unit.evidence.kind)
    if expected_field is None:
        raise ValueError(
            f"SAP Commerce Solr unit {index} has unsupported kind "
            f"{unit.evidence.kind!r}"
        )
    populated = tuple(
        field
        for field in _SUPPORTED_KINDS.values()
        if getattr(unit.evidence, field) is not None
    )
    if populated != (expected_field,):
        raise ValueError(
            f"SAP Commerce Solr unit {index} kind {unit.evidence.kind!r} "
            f"requires only {expected_field!r}"
        )
    _validate_evidence_atoms(unit.evidence, index)


def _validate_evidence_atoms(evidence: SapCommerceSolrEvidence, index: int) -> None:
    values: list[str] = []
    if evidence.indexed_type is not None:
        indexed_type = evidence.indexed_type
        values.extend(
            value
            for value in (
                indexed_type.identifier,
                indexed_type.item_type,
                indexed_type.default_field_value_provider,
                indexed_type.identity_provider,
                indexed_type.config_set,
                indexed_type.index_name,
            )
            if value is not None
        )
    elif evidence.indexed_property is not None:
        indexed_property = evidence.indexed_property
        values.extend(
            value
            for value in (
                indexed_property.name,
                indexed_property.indexed_type,
                indexed_property.value_type,
                indexed_property.sortable_type,
                indexed_property.field_value_provider,
            )
            if value is not None
        )
    elif evidence.facet_search_config is not None:
        facet_search_config = evidence.facet_search_config
        values.extend(
            value
            for value in (
                facet_search_config.name,
                facet_search_config.index_name_prefix,
                facet_search_config.search_config,
                facet_search_config.index_config,
                facet_search_config.server_config,
            )
            if value is not None
        )
        values.extend(facet_search_config.indexed_types)
        values.extend(facet_search_config.languages)
        values.extend(facet_search_config.currencies)

    if not values or any(_SAFE_ATOM.fullmatch(value) is None for value in values):
        raise ValueError(
            f"SAP Commerce Solr unit {index} contains a non-canonical structural value"
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


def _unit_to_mapping(unit: SapCommerceSolrUnit) -> dict[str, object]:
    return {
        "evidence": unit.evidence.to_mapping(),
        "header_line": unit.header_line,
        "line_start": unit.line_start,
        "line_end": unit.line_end,
    }


def _positive_line(value: object, index: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"SAP Commerce Solr unit {index} {field} must be a positive integer"
        )
    return value


__all__ = [
    "SapCommerceSolrFacetSearchCodec",
    "SapCommerceSolrFacetSearchEnricher",
    "sap_commerce_solr_evidence_from_document",
]
