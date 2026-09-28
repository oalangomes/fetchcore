"""Canonical SAP Commerce Java/OCC role enrichment."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import cast

from fetchcore.repository.indexing import KnowledgeDocument
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.sap_commerce_java import (
    SAP_COMMERCE_JAVA_ROLE_SIGNALS,
    SAP_COMMERCE_OCC_CONTROLLER_ROLE,
    SapCommerceJavaExtensionPointEvidence,
    SapCommerceJavaExtensionPointUnit,
    SapCommerceJavaRoleEvidence,
    SapCommerceJavaRoleUnit,
    extract_sap_commerce_java_extension_points,
    is_well_formed_java_type_argument,
)
from fetchcore.repository.semantic import (
    SemanticEvidenceEnvelope,
    SemanticRelationEvidence,
    decode_semantic_evidence,
    encode_semantic_evidence,
)


_API_VERSION = "de.hybris.platform.commerceservices.request.mapping.annotation.ApiVersion"
_REQUEST_MAPPING_OVERRIDE = (
    "de.hybris.platform.commerceservices.request.mapping.annotation.RequestMappingOverride"
)
_SITE_CHANNEL_RESTRICTION = (
    "de.hybris.platform.commercewebservicescommons.annotation.SiteChannelRestriction"
)
_SPRING_CONTROLLER = "org.springframework.stereotype.Controller"
_SPRING_REST_CONTROLLER = "org.springframework.web.bind.annotation.RestController"
_LITERAL = re.compile(r'^(?:value\s*=\s*)?"([^"\\]*)"$')


class SapCommerceJavaRoleCodec:
    """Typed versioned codec for SAP-specific Java role specialization."""

    namespace = "sap.commerce"
    kind = "java-role"
    version = "1"

    def to_payload(
        self,
        value: tuple[SapCommerceJavaRoleUnit, ...],
    ) -> Mapping[str, object]:
        """Serialize only valid Java role units with declaration-line provenance."""

        units: list[dict[str, object]] = []
        for index, unit in enumerate(value):
            if not isinstance(unit, SapCommerceJavaRoleUnit):
                raise TypeError(f"SAP Commerce Java role unit {index} has invalid type")
            _validate_role_evidence(unit.evidence, index)
            _positive_line(unit.line_start, index, "line_start")
            _positive_line(unit.line_end, index, "line_end")
            if unit.line_end < unit.line_start:
                raise ValueError(
                    f"SAP Commerce Java role unit {index} line_end precedes line_start"
                )
            units.append(_unit_to_mapping(unit))
        return {"units": units}

    def from_payload(
        self,
        value: Mapping[str, object],
    ) -> tuple[SapCommerceJavaRoleUnit, ...]:
        """Hydrate only the exact canonical Java-role payload supported by v1."""

        if set(value) != {"units"}:
            raise ValueError("SAP Commerce Java role payload requires only the units field")
        raw_units = value.get("units")
        if not isinstance(raw_units, list):
            raise ValueError("SAP Commerce Java role units must be a list")

        units: list[SapCommerceJavaRoleUnit] = []
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, Mapping):
                raise ValueError(f"SAP Commerce Java role unit {index} must be an object")
            expected_fields = {"evidence", "line_start", "line_end"}
            if set(raw_unit) != expected_fields:
                raise ValueError(
                    f"SAP Commerce Java role unit {index} requires exactly "
                    f"{sorted(expected_fields)}"
                )
            raw_evidence = raw_unit.get("evidence")
            if not isinstance(raw_evidence, Mapping):
                raise ValueError(
                    f"SAP Commerce Java role unit {index} evidence must be an object"
                )
            if set(raw_evidence) != {"symbol", "role", "signals", "api_version"}:
                raise ValueError(
                    f"SAP Commerce Java role unit {index} evidence has unsupported fields"
                )
            line_start = _positive_line(raw_unit.get("line_start"), index, "line_start")
            line_end = _positive_line(raw_unit.get("line_end"), index, "line_end")
            if line_end < line_start:
                raise ValueError(
                    f"SAP Commerce Java role unit {index} line_end precedes line_start"
                )
            evidence = SapCommerceJavaRoleEvidence.from_mapping(
                cast(Mapping[str, object], raw_evidence)
            )
            _validate_role_evidence(evidence, index)
            unit = SapCommerceJavaRoleUnit(
                evidence=evidence,
                line_start=line_start,
                line_end=line_end,
            )
            if _unit_to_mapping(unit) != dict(raw_unit):
                raise ValueError(
                    f"SAP Commerce Java role unit {index} is not canonical for version 1"
                )
            units.append(unit)
        return tuple(units)


class SapCommerceJavaExtensionPointCodec:
    """Typed strict codec for direct SAP Commerce Java extension points."""

    namespace = "sap.commerce"
    kind = "java-extension-point"
    version = "1"

    def to_payload(
        self,
        value: tuple[SapCommerceJavaExtensionPointUnit, ...],
    ) -> Mapping[str, object]:
        """Serialize canonical extension-point units deterministically."""

        units: list[dict[str, object]] = []
        for index, unit in enumerate(value):
            if not isinstance(unit, SapCommerceJavaExtensionPointUnit):
                raise TypeError(f"SAP Commerce Java extension unit {index} has invalid type")
            _validate_extension_evidence(unit.evidence, index)
            _validate_extension_lines(unit, index)
            units.append(_extension_unit_to_mapping(unit))
        return {"units": units}

    def from_payload(
        self,
        value: Mapping[str, object],
    ) -> tuple[SapCommerceJavaExtensionPointUnit, ...]:
        """Hydrate only the exact version-one extension-point payload."""

        raw_units = value.get("units")
        if set(value) != {"units"} or not isinstance(raw_units, list):
            raise ValueError("SAP Commerce Java extension payload requires only a units list")
        units: list[SapCommerceJavaExtensionPointUnit] = []
        for index, raw_unit in enumerate(raw_units):
            if not isinstance(raw_unit, Mapping) or set(raw_unit) != {
                "evidence", "line_start", "line_end"
            }:
                raise ValueError(f"SAP Commerce Java extension unit {index} is not canonical")
            raw_evidence = raw_unit["evidence"]
            if not isinstance(raw_evidence, Mapping) or set(raw_evidence) != {
                "symbol", "role", "contract", "declaration", "type_arguments"
            }:
                raise ValueError(f"SAP Commerce Java extension evidence {index} is not canonical")
            arguments = raw_evidence["type_arguments"]
            if not isinstance(arguments, list) or any(not isinstance(item, str) for item in arguments):
                raise ValueError(f"SAP Commerce Java extension type_arguments {index} is invalid")
            evidence = SapCommerceJavaExtensionPointEvidence(
                symbol=_required_canonical_string(raw_evidence, "symbol", index),
                role=_required_canonical_string(raw_evidence, "role", index),
                contract=_required_canonical_string(raw_evidence, "contract", index),
                declaration=_required_canonical_string(raw_evidence, "declaration", index),
                type_arguments=tuple(arguments),
            )
            unit = SapCommerceJavaExtensionPointUnit(
                evidence=evidence,
                line_start=_positive_line(raw_unit.get("line_start"), index, "line_start"),
                line_end=_positive_line(raw_unit.get("line_end"), index, "line_end"),
            )
            _validate_extension_evidence(evidence, index)
            _validate_extension_lines(unit, index)
            if _extension_unit_to_mapping(unit) != dict(raw_unit):
                raise ValueError(f"SAP Commerce Java extension unit {index} is not canonical")
            units.append(unit)
        return tuple(units)


class SapCommerceJavaExtensionPointEnricher:
    """Promote only directly proven Java extension-point declarations."""

    indexing_contract_id = "sap.commerce.java-extension-point"
    indexing_contract_version = "1"

    def __init__(self) -> None:
        """Initialize the extension-point codec."""

        self.codec = SapCommerceJavaExtensionPointCodec()

    def enrich(self, source: SafeSource, document: KnowledgeDocument) -> KnowledgeDocument:
        """Attach units and generic relations owned by the current Java chunk."""

        del source
        if document.language != "java" or document.evidence.symbol is None:
            return document
        symbol = document.evidence.symbol
        if not document.line_start <= symbol.line_start <= document.line_end:
            return document
        observed = extract_sap_commerce_java_extension_points(
            symbol=symbol,
            imports=document.evidence.structure.import_sources,
        )
        if not observed:
            return document
        units = tuple(
            SapCommerceJavaExtensionPointUnit(
                evidence=item,
                line_start=symbol.line_start,
                line_end=symbol.line_end,
            )
            for item in observed
        )
        evidence = replace(
            document.evidence,
            semantic=_append_semantic(
                document.evidence.semantic,
                encode_semantic_evidence(self.codec, units),
            ),
            relations=replace(
                document.evidence.relations,
                semantic=_append_relations(
                    document.evidence.relations.semantic,
                    _extension_relations(units),
                ),
            ),
        )
        return replace(document, evidence=evidence)


def sap_commerce_java_extension_point_evidence_from_document(
    document: KnowledgeDocument,
) -> tuple[SapCommerceJavaExtensionPointUnit, ...] | None:
    """Hydrate S8 evidence from the canonical semantic surface."""

    codec = SapCommerceJavaExtensionPointCodec()
    matches = tuple(
        item for item in document.evidence.semantic
        if (item.namespace, item.kind, item.version)
        == (codec.namespace, codec.kind, codec.version)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("document contains duplicate SAP Commerce Java extension evidence")
    return decode_semantic_evidence(codec, matches[0])


class SapCommerceJavaRoleEnricher:
    """Specialize exact SAP/OCC Java roles without replacing generic role evidence."""

    indexing_contract_id = "sap.commerce.java-role"
    indexing_contract_version = "1"

    def __init__(self) -> None:
        """Initialize the canonical Java-role codec."""

        self.codec = SapCommerceJavaRoleCodec()

    def enrich(
        self,
        source: SafeSource,
        document: KnowledgeDocument,
    ) -> KnowledgeDocument:
        """Attach OCC controller specialization from existing canonical Java facts."""

        del source
        unit = _occ_controller_unit(document)
        if unit is None:
            return document
        envelope = encode_semantic_evidence(self.codec, (unit,))
        evidence = replace(
            document.evidence,
            semantic=_append_semantic(document.evidence.semantic, envelope),
        )
        return replace(document, evidence=evidence)


def sap_commerce_java_role_evidence_from_document(
    document: KnowledgeDocument,
) -> tuple[SapCommerceJavaRoleUnit, ...] | None:
    """Hydrate SAP Java-role evidence from the canonical semantic surface."""

    codec = SapCommerceJavaRoleCodec()
    matches = tuple(
        item
        for item in document.evidence.semantic
        if (item.namespace, item.kind, item.version)
        == (codec.namespace, codec.kind, codec.version)
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise ValueError("document contains duplicate SAP Commerce Java role evidence")
    return decode_semantic_evidence(codec, matches[0])


def _occ_controller_unit(document: KnowledgeDocument) -> SapCommerceJavaRoleUnit | None:
    if document.language != "java":
        return None
    symbol = document.evidence.symbol
    if symbol is None or symbol.kind != "class":
        return None
    if not document.line_start <= symbol.line_start <= document.line_end:
        return None

    imports = document.evidence.structure.import_sources
    api_version_annotation = _matching_annotation(
        symbol.annotations,
        _API_VERSION,
        imports,
    )
    request_mapping_override_applied = any(
        _matching_annotation(method.annotations, _REQUEST_MAPPING_OVERRIDE, imports)
        is not None
        for method in document.evidence.behavior.methods
    )
    site_channel_applied = any(
        _matching_annotation(method.annotations, _SITE_CHANNEL_RESTRICTION, imports)
        is not None
        for method in document.evidence.behavior.methods
    )

    signals: list[str] = []
    if api_version_annotation is not None:
        signals.append("api-version")
    if request_mapping_override_applied:
        signals.append("request-mapping-override")
    if site_channel_applied:
        signals.append("site-channel-restriction")
    if not signals or not _has_controller_basis(document, imports):
        return None

    qualified = symbol.qualified_name or symbol.name
    return SapCommerceJavaRoleUnit(
        evidence=SapCommerceJavaRoleEvidence(
            symbol=qualified,
            role=SAP_COMMERCE_OCC_CONTROLLER_ROLE,
            signals=tuple(signals),
            api_version=(
                _literal_annotation_value(api_version_annotation)
                if api_version_annotation is not None
                else None
            ),
        ),
        line_start=symbol.line_start,
        line_end=symbol.line_end,
    )


def _has_controller_basis(
    document: KnowledgeDocument,
    imports: tuple[str, ...],
) -> bool:
    if any(route.framework == "spring-mvc" for route in document.evidence.behavior.routes):
        return True
    symbol = document.evidence.symbol
    if symbol is None:
        return False
    return any(
        _matching_annotation(symbol.annotations, annotation, imports) is not None
        for annotation in (_SPRING_CONTROLLER, _SPRING_REST_CONTROLLER)
    )


def _matching_annotation(
    annotations: tuple[str, ...],
    fqcn: str,
    imports: tuple[str, ...],
) -> str | None:
    simple = fqcn.rsplit(".", 1)[-1]
    package_wildcard = fqcn.rsplit(".", 1)[0] + ".*"
    imported = fqcn in imports or package_wildcard in imports
    for annotation in annotations:
        head = annotation.split("(", 1)[0].strip()
        if head == fqcn or (imported and head == simple):
            return annotation
    return None


def _literal_annotation_value(annotation: str) -> str | None:
    if "(" not in annotation or not annotation.endswith(")"):
        return None
    arguments = annotation.split("(", 1)[1][:-1].strip()
    match = _LITERAL.fullmatch(arguments)
    if match is None:
        return None
    return match.group(1)


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
    """Append deterministic, duplicate-free generic relations."""

    return tuple(dict.fromkeys((*existing, *values)))


def _extension_relations(
    units: tuple[SapCommerceJavaExtensionPointUnit, ...],
) -> tuple[SemanticRelationEvidence, ...]:
    relations: list[SemanticRelationEvidence] = []
    for unit in units:
        evidence = unit.evidence
        relations.append(
            SemanticRelationEvidence(
                kind=(
                    "implements_contract"
                    if evidence.declaration == "implements"
                    else "extends_contract"
                ),
                target=evidence.contract,
                resolution="observed",
                evidence_basis="source",
            )
        )
        relation_kind = {
            "populator": ("populates_from", "populates_to"),
            "converter": ("converts_from", "converts_to"),
            "validate-interceptor": ("intercepts_type",),
            "prepare-interceptor": ("intercepts_type",),
            "init-defaults-interceptor": ("intercepts_type",),
            "remove-interceptor": ("intercepts_type",),
            "load-interceptor": ("intercepts_type",),
            "event-listener": ("listens_to",),
        }[evidence.role]
        for index, target in enumerate(evidence.type_arguments):
            if index >= len(relation_kind):
                break
            relations.append(
                SemanticRelationEvidence(
                    kind=relation_kind[index],
                    target=target,
                    resolution="observed",
                    evidence_basis="source",
                )
            )
    return tuple(dict.fromkeys(relations))


def _extension_unit_to_mapping(
    unit: SapCommerceJavaExtensionPointUnit,
) -> dict[str, object]:
    return {
        "evidence": unit.evidence.to_mapping(),
        "line_start": unit.line_start,
        "line_end": unit.line_end,
    }


def _validate_extension_evidence(
    evidence: SapCommerceJavaExtensionPointEvidence,
    index: int,
) -> None:
    if any(
        not value or value != value.strip()
        for value in (evidence.symbol, evidence.role, evidence.contract, evidence.declaration)
    ):
        raise ValueError(f"SAP Commerce Java extension evidence {index} has non-canonical text")
    expected_role = {
        "de.hybris.platform.converters.Populator": "populator",
        "de.hybris.platform.servicelayer.dto.converter.Converter": "converter",
        "de.hybris.platform.servicelayer.interceptor.ValidateInterceptor": "validate-interceptor",
        "de.hybris.platform.servicelayer.interceptor.PrepareInterceptor": "prepare-interceptor",
        "de.hybris.platform.servicelayer.interceptor.InitDefaultsInterceptor": "init-defaults-interceptor",
        "de.hybris.platform.servicelayer.interceptor.RemoveInterceptor": "remove-interceptor",
        "de.hybris.platform.servicelayer.interceptor.LoadInterceptor": "load-interceptor",
        "de.hybris.platform.servicelayer.event.impl.AbstractEventListener": "event-listener",
    }.get(evidence.contract)
    if expected_role != evidence.role:
        raise ValueError(f"SAP Commerce Java extension evidence {index} is unsupported")
    expected_arity = 2 if evidence.role in {"populator", "converter"} else 1
    valid_declarations = (
        {"extends"}
        if evidence.role == "event-listener"
        else {"implements", "extends"}
    )
    if (
        evidence.declaration not in valid_declarations
        or len(evidence.type_arguments) not in {0, expected_arity}
    ):
        raise ValueError(
            f"SAP Commerce Java extension evidence {index} has invalid contract shape"
        )
    if any(not is_well_formed_java_type_argument(item) for item in evidence.type_arguments):
        raise ValueError(f"SAP Commerce Java extension evidence {index} has invalid type arguments")


def _validate_extension_lines(
    unit: SapCommerceJavaExtensionPointUnit,
    index: int,
) -> None:
    _positive_line(unit.line_start, index, "line_start")
    _positive_line(unit.line_end, index, "line_end")
    if unit.line_end < unit.line_start:
        raise ValueError(f"SAP Commerce Java extension unit {index} line_end precedes line_start")


def _required_canonical_string(
    value: Mapping[str, object],
    key: str,
    index: int,
) -> str:
    raw = value.get(key)
    if not isinstance(raw, str) or not raw or raw != raw.strip():
        raise ValueError(f"SAP Commerce Java extension evidence {index} {key} is invalid")
    return raw


def _unit_to_mapping(unit: SapCommerceJavaRoleUnit) -> dict[str, object]:
    return {
        "evidence": unit.evidence.to_mapping(),
        "line_start": unit.line_start,
        "line_end": unit.line_end,
    }


def _validate_role_evidence(
    evidence: SapCommerceJavaRoleEvidence,
    index: int,
) -> None:
    if not evidence.symbol.strip() or evidence.symbol != evidence.symbol.strip():
        raise ValueError(
            f"SAP Commerce Java role unit {index} requires a canonical symbol"
        )
    if evidence.role != SAP_COMMERCE_OCC_CONTROLLER_ROLE:
        raise ValueError(
            f"SAP Commerce Java role unit {index} has unsupported role {evidence.role!r}"
        )
    canonical_signals = tuple(
        signal
        for signal in SAP_COMMERCE_JAVA_ROLE_SIGNALS
        if signal in evidence.signals
    )
    if not canonical_signals or evidence.signals != canonical_signals:
        raise ValueError(
            f"SAP Commerce Java role unit {index} signals are not canonical"
        )
    if evidence.api_version is not None and (
        not evidence.api_version.strip()
        or evidence.api_version != evidence.api_version.strip()
    ):
        raise ValueError(
            f"SAP Commerce Java role unit {index} api_version is not canonical"
        )


def _positive_line(value: object, index: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"SAP Commerce Java role unit {index} {field} must be a positive integer"
        )
    return value
