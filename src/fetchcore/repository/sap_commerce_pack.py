"""SAP Commerce knowledge pack for activation and typed canonical evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast

from fetchcore.repository.indexing import KnowledgeDocument, RepositoryIndexConfig
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.sap_commerce import (
    SapCommerceEvidence,
    extract_sap_commerce_evidence,
    sap_commerce_declared_dependencies,
)
from fetchcore.repository.sap_commerce_detection import is_sap_commerce_workspace
from fetchcore.repository.sap_commerce_impex_pack import (
    SapCommerceImpexEnricher,
    SapCommerceImpexSourceInterpreter,
)
from fetchcore.repository.sap_commerce_items import (
    SapCommerceTypeSystemEvidence,
    SapCommerceTypeSystemUnit,
    extract_sap_commerce_type_system_units,
)
from fetchcore.repository.sap_commerce_java_pack import (
    SapCommerceJavaExtensionPointEnricher,
    SapCommerceJavaRoleEnricher,
)
from fetchcore.repository.sap_commerce_resolution import SapCommerceCorpusResolver
from fetchcore.repository.sap_commerce_solr_pack import (
    SapCommerceSolrFacetSearchEnricher,
)
from fetchcore.repository.sap_commerce_spring_pack import (
    SapCommerceSpringEnricher,
)
from fetchcore.repository.semantic import (
    SemanticEvidenceEnvelope,
    SemanticRelationEvidence,
    decode_semantic_evidence,
    encode_semantic_evidence,
)
from fetchcore.repository.semantic_indexing import (
    KnowledgeCorpusResolver,
    KnowledgeDocumentEnricher,
    KnowledgeSourceInterpreter,
)


SAP_COMMERCE_SENSITIVE_EXCLUDE_PATTERNS = (
    "local.properties",
    "*.jks",
    "*.p12",
    "*.pfx",
    "*.pem",
    "*.key",
    "*.keystore",
)
SAP_COMMERCE_UNSUPPORTED_BINARY_EXCLUDE_PATTERNS = (
    "*.jar",
    "*.war",
    "*.ear",
    "*.class",
    "*.zip",
)
SAP_COMMERCE_DEFAULT_MAX_FILES = 10_000
SAP_COMMERCE_DEFAULT_MAX_TOTAL_BYTES = 128_000_000
_SAP_COMMERCE_NAMESPACE = "sap.commerce"


class SapCommerceTopologyCodec:
    """Typed versioned codec for direct SAP Commerce project topology evidence."""

    namespace = _SAP_COMMERCE_NAMESPACE
    kind = "extension-topology"
    version = "1"

    def to_payload(self, value: SapCommerceEvidence) -> Mapping[str, object]:
        """Serialize typed topology without leaking it into operational metadata."""

        return value.to_mapping()

    def from_payload(self, value: Mapping[str, object]) -> SapCommerceEvidence:
        """Hydrate only the exact canonical topology payload supported by version 1."""

        expected = {"project_manifest", "extension_set", "extension"}
        unsupported = set(value) - expected
        if unsupported:
            raise ValueError(
                f"unsupported SAP Commerce topology fields for version 1: {sorted(unsupported)}"
            )
        hydrated = SapCommerceEvidence.from_mapping(value)
        if hydrated.to_mapping() != dict(value):
            raise ValueError("SAP Commerce topology payload is not canonical for version 1")
        return hydrated


class SapCommerceTypeSystemCodec:
    """Typed versioned codec for one SAP Commerce ``*-items.xml`` source."""

    namespace = _SAP_COMMERCE_NAMESPACE
    kind = "type-system"
    version = "1"

    def to_payload(
        self,
        value: tuple[SapCommerceTypeSystemUnit, ...],
    ) -> Mapping[str, object]:
        """Serialize typed items units while retaining exact source-line provenance."""

        return {"units": [_type_system_unit_to_mapping(unit) for unit in value]}

    def from_payload(
        self,
        value: Mapping[str, object],
    ) -> tuple[SapCommerceTypeSystemUnit, ...]:
        """Hydrate only the exact canonical type-system payload supported by version 1."""

        if set(value) != {"units"}:
            raise ValueError("SAP Commerce type-system payload requires only the units field")
        raw_units = value.get("units")
        if not isinstance(raw_units, list):
            raise ValueError("SAP Commerce type-system units must be a list")

        units: list[SapCommerceTypeSystemUnit] = []
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, Mapping):
                raise ValueError(f"SAP Commerce type-system unit {index} must be an object")
            expected_fields = {"evidence", "line_start", "line_end"}
            if set(raw_unit) != expected_fields:
                raise ValueError(
                    f"SAP Commerce type-system unit {index} requires exactly {sorted(expected_fields)}"
                )
            raw_evidence = raw_unit.get("evidence")
            if not isinstance(raw_evidence, Mapping):
                raise ValueError(
                    f"SAP Commerce type-system unit {index} evidence must be an object"
                )
            line_start = _positive_line(raw_unit.get("line_start"), index, "line_start")
            line_end = _positive_line(raw_unit.get("line_end"), index, "line_end")
            if line_end < line_start:
                raise ValueError(
                    f"SAP Commerce type-system unit {index} line_end precedes line_start"
                )
            evidence = SapCommerceTypeSystemEvidence.from_mapping(
                cast(Mapping[str, object], raw_evidence)
            )
            _validate_type_system_evidence_shape(evidence, index)
            unit = SapCommerceTypeSystemUnit(
                evidence=evidence,
                line_start=line_start,
                line_end=line_end,
            )
            if _type_system_unit_to_mapping(unit) != dict(raw_unit):
                raise ValueError(
                    f"SAP Commerce type-system unit {index} is not canonical for version 1"
                )
            units.append(unit)
        return tuple(units)


class SapCommerceTopologyEnricher:
    """Project direct SAP Commerce topology into canonical semantic evidence."""

    indexing_contract_id = "sap.commerce.extension-topology"
    indexing_contract_version = "1"

    def __init__(self) -> None:
        """Initialize the topology evidence codec."""

        self.codec = SapCommerceTopologyCodec()

    def enrich(
        self,
        source: SafeSource,
        document: KnowledgeDocument,
    ) -> KnowledgeDocument:
        """Attach direct topology evidence and generic relations for one source."""

        observed = extract_sap_commerce_evidence(source.relative, source.content)
        if observed is None:
            return document

        envelope = encode_semantic_evidence(self.codec, observed)
        semantic = _append_semantic(document.evidence.semantic, envelope)
        dependencies = tuple(
            dict.fromkeys(
                (
                    *document.evidence.relations.dependencies,
                    *sap_commerce_declared_dependencies(observed),
                )
            )
        )
        semantic_relations = _topology_relations(observed)
        relations = replace(
            document.evidence.relations,
            dependencies=dependencies,
            semantic=_append_relations(
                document.evidence.relations.semantic,
                semantic_relations,
            ),
        )
        evidence = replace(
            document.evidence,
            semantic=semantic,
            relations=relations,
        )
        return replace(document, evidence=evidence)


class SapCommerceTypeSystemEnricher:
    """Project ``*-items.xml`` facts into typed evidence and generic relations."""

    indexing_contract_id = "sap.commerce.type-system"
    indexing_contract_version = "2"

    def __init__(self) -> None:
        """Initialize the type-system evidence codec."""

        self.codec = SapCommerceTypeSystemCodec()

    def enrich(
        self,
        source: SafeSource,
        document: KnowledgeDocument,
    ) -> KnowledgeDocument:
        """Attach only type-system units represented by the current canonical chunk."""

        if not source.relative.casefold().endswith("-items.xml"):
            return document
        parsed = extract_sap_commerce_type_system_units(source.content)
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
        evidence = replace(
            document.evidence,
            semantic=_append_semantic(document.evidence.semantic, envelope),
            relations=replace(
                document.evidence.relations,
                semantic=_append_relations(
                    document.evidence.relations.semantic,
                    _type_system_relations(units),
                ),
            ),
        )
        return replace(document, evidence=evidence)


class SapCommerceKnowledgePack:
    """Fail-closed SAP Commerce activation, safety policy and semantic capabilities."""

    pack_id = "sap.commerce"
    version = "8"

    def matches(self, workspace: Path) -> bool:
        """Activate only from explicit SAP Commerce workspace evidence."""

        return is_sap_commerce_workspace(workspace)

    def configure(
        self,
        config: RepositoryIndexConfig,
        *,
        apply_defaults: bool,
    ) -> RepositoryIndexConfig:
        """Apply mandatory safety and optional bounded SAP corpus defaults."""

        safe_excludes = tuple(
            dict.fromkeys(
                (
                    *config.exclude_patterns,
                    *SAP_COMMERCE_SENSITIVE_EXCLUDE_PATTERNS,
                    *SAP_COMMERCE_UNSUPPORTED_BINARY_EXCLUDE_PATTERNS,
                )
            )
        )
        if not apply_defaults:
            return replace(config, exclude_patterns=safe_excludes)
        includes = tuple(dict.fromkeys((*config.include_patterns, "*.impex")))
        return replace(
            config,
            include_patterns=includes,
            exclude_patterns=safe_excludes,
            max_files=max(config.max_files, SAP_COMMERCE_DEFAULT_MAX_FILES),
            max_total_bytes=max(
                config.max_total_bytes,
                SAP_COMMERCE_DEFAULT_MAX_TOTAL_BYTES,
            ),
        )

    def source_interpreters(self) -> tuple[KnowledgeSourceInterpreter, ...]:
        """Return source interpreters that must run before ordinary raw chunking."""

        return (SapCommerceImpexSourceInterpreter(include_solr_facts=True),)

    def semantic_enrichers(self) -> tuple[KnowledgeDocumentEnricher, ...]:
        """Return ordered source-local canonical capabilities contributed by this pack."""

        return (
            SapCommerceTopologyEnricher(),
            SapCommerceTypeSystemEnricher(),
            SapCommerceJavaRoleEnricher(),
            SapCommerceJavaExtensionPointEnricher(),
            SapCommerceSpringEnricher(),
            SapCommerceImpexEnricher(),
            SapCommerceSolrFacetSearchEnricher(),
        )

    def corpus_resolvers(self) -> tuple[KnowledgeCorpusResolver, ...]:
        """Return exact corpus-wide resolvers contributed by this pack."""

        return (SapCommerceCorpusResolver(),)


def sap_commerce_topology_evidence_from_document(
    document: KnowledgeDocument,
) -> SapCommerceEvidence | None:
    """Hydrate typed topology from canonical semantic evidence, never metadata."""

    codec = SapCommerceTopologyCodec()
    matches = tuple(
        item
        for item in document.evidence.semantic
        if (item.namespace, item.kind, item.version)
        == (codec.namespace, codec.kind, codec.version)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("document contains duplicate SAP Commerce topology evidence")
    return decode_semantic_evidence(codec, matches[0])


def sap_commerce_type_system_evidence_from_document(
    document: KnowledgeDocument,
) -> tuple[SapCommerceTypeSystemUnit, ...] | None:
    """Hydrate typed items evidence from the canonical semantic surface."""

    codec = SapCommerceTypeSystemCodec()
    matches = tuple(
        item
        for item in document.evidence.semantic
        if (item.namespace, item.kind, item.version)
        == (codec.namespace, codec.kind, codec.version)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("document contains duplicate SAP Commerce type-system evidence")
    return decode_semantic_evidence(codec, matches[0])


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
    combined = (*existing, *values)
    return tuple(dict.fromkeys(combined))


def _topology_relations(
    evidence: SapCommerceEvidence,
) -> tuple[SemanticRelationEvidence, ...]:
    extension_set = evidence.extension_set
    if extension_set is None:
        return ()
    return tuple(
        SemanticRelationEvidence(
            kind="includes",
            target=extension,
            resolution="observed",
            evidence_basis="source",
        )
        for extension in extension_set.extensions
        if extension
    )


def _type_system_relations(
    units: tuple[SapCommerceTypeSystemUnit, ...],
) -> tuple[SemanticRelationEvidence, ...]:
    relations: list[SemanticRelationEvidence] = []
    for unit in units:
        evidence = unit.evidence
        if evidence.item_type is not None and evidence.item_type.extends:
            relations.append(_observed_relation("extends", evidence.item_type.extends))
        if evidence.attribute is not None and evidence.attribute.declared_type:
            relations.append(_observed_relation("typed_as", evidence.attribute.declared_type))
        if evidence.relation is not None:
            if evidence.relation.source.type_name:
                relations.append(
                    _observed_relation("source_type", evidence.relation.source.type_name)
                )
            if evidence.relation.target.type_name:
                relations.append(
                    _observed_relation("target_type", evidence.relation.target.type_name)
                )
        if evidence.collection_type is not None and evidence.collection_type.element_type:
            relations.append(
                _observed_relation("element_type", evidence.collection_type.element_type)
            )
        if evidence.map_type is not None:
            if evidence.map_type.argument_type:
                relations.append(
                    _observed_relation("argument_type", evidence.map_type.argument_type)
                )
            if evidence.map_type.return_type:
                relations.append(
                    _observed_relation("return_type", evidence.map_type.return_type)
                )
    return tuple(dict.fromkeys(relations))


def _observed_relation(kind: str, target: str) -> SemanticRelationEvidence:
    return SemanticRelationEvidence(
        kind=kind,
        target=target,
        resolution="observed",
        evidence_basis="source",
    )


def _type_system_unit_to_mapping(unit: SapCommerceTypeSystemUnit) -> dict[str, object]:
    return {
        "evidence": unit.evidence.to_mapping(),
        "line_start": unit.line_start,
        "line_end": unit.line_end,
    }


def _validate_type_system_evidence_shape(
    evidence: SapCommerceTypeSystemEvidence,
    index: int,
) -> None:
    fields = {
        "item-type": "item_type",
        "attribute": "attribute",
        "relation": "relation",
        "enum-type": "enum_type",
        "collection-type": "collection_type",
        "map-type": "map_type",
    }
    expected = fields.get(evidence.kind)
    if expected is None:
        raise ValueError(
            f"SAP Commerce type-system unit {index} has unsupported evidence kind {evidence.kind!r}"
        )
    populated = tuple(
        field
        for field in fields.values()
        if getattr(evidence, field) is not None
    )
    if populated != (expected,):
        raise ValueError(
            f"SAP Commerce type-system unit {index} evidence kind {evidence.kind!r} "
            f"requires only {expected!r}"
        )
    if not evidence.symbol_name.strip(". "):
        raise ValueError(
            f"SAP Commerce type-system unit {index} requires a non-empty semantic symbol"
        )


def _positive_line(value: object, index: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"SAP Commerce type-system unit {index} {field} must be a positive integer"
        )
    return value
