"""Typed SAP Commerce ``*-items.xml`` type-system evidence.

The extractor emits semantic units with source line provenance.  It records only
facts explicitly declared by the items XML and never resolves referenced types to
custom, SAP product, or extension-pack origins.
"""

from __future__ import annotations

import io
import xml.sax
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from xml.sax.handler import ContentHandler, feature_external_ges, feature_external_pes


SAP_COMMERCE_TYPE_SYSTEM_METADATA_KEY = "sap_commerce_type_system"


@dataclass(frozen=True)
class SapCommerceRelationEndpointEvidence:
    """One explicitly declared endpoint of an SAP Commerce relation."""

    type_name: str
    qualifier: str
    cardinality: str
    collection_type: str | None = None
    ordered: bool | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize relation-endpoint evidence."""

        return {
            "type_name": self.type_name,
            "qualifier": self.qualifier,
            "cardinality": self.cardinality,
            "collection_type": self.collection_type,
            "ordered": self.ordered,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceRelationEndpointEvidence:
        """Hydrate relation-endpoint evidence from a mapping."""

        return cls(
            type_name=str(value.get("type_name", "")),
            qualifier=str(value.get("qualifier", "")),
            cardinality=str(value.get("cardinality", "")),
            collection_type=_optional_string(value.get("collection_type")),
            ordered=_optional_bool(value.get("ordered")),
        )


@dataclass(frozen=True)
class SapCommerceItemTypeEvidence:
    """One item type declared by an SAP Commerce items file."""

    code: str
    extends: str | None = None
    autocreate: bool | None = None
    generate: bool | None = None
    abstract: bool | None = None
    jalo_class: str | None = None
    deployment_table: str | None = None
    deployment_typecode: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize item-type evidence."""

        return {
            "code": self.code,
            "extends": self.extends,
            "autocreate": self.autocreate,
            "generate": self.generate,
            "abstract": self.abstract,
            "jalo_class": self.jalo_class,
            "deployment_table": self.deployment_table,
            "deployment_typecode": self.deployment_typecode,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceItemTypeEvidence:
        """Hydrate item-type evidence from a mapping."""

        return cls(
            code=str(value.get("code", "")),
            extends=_optional_string(value.get("extends")),
            autocreate=_optional_bool(value.get("autocreate")),
            generate=_optional_bool(value.get("generate")),
            abstract=_optional_bool(value.get("abstract")),
            jalo_class=_optional_string(value.get("jalo_class")),
            deployment_table=_optional_string(value.get("deployment_table")),
            deployment_typecode=_optional_string(value.get("deployment_typecode")),
        )


@dataclass(frozen=True)
class SapCommerceAttributeEvidence:
    """One attribute declared on an SAP Commerce item type."""

    owner_type: str
    qualifier: str
    declared_type: str
    redeclare: bool | None = None
    autocreate: bool | None = None
    generate: bool | None = None
    persistence_type: str | None = None
    attribute_handler: str | None = None
    readable: bool | None = None
    writable: bool | None = None
    searchable: bool | None = None
    unique: bool | None = None
    initial: bool | None = None
    optional: bool | None = None
    partof: bool | None = None

    @property
    def qualified_name(self) -> str:
        """Return the owner-qualified attribute name."""

        return f"{self.owner_type}.{self.qualifier}"

    def to_mapping(self) -> dict[str, object]:
        """Serialize attribute evidence."""

        return {
            "owner_type": self.owner_type,
            "qualifier": self.qualifier,
            "declared_type": self.declared_type,
            "redeclare": self.redeclare,
            "autocreate": self.autocreate,
            "generate": self.generate,
            "persistence_type": self.persistence_type,
            "attribute_handler": self.attribute_handler,
            "readable": self.readable,
            "writable": self.writable,
            "searchable": self.searchable,
            "unique": self.unique,
            "initial": self.initial,
            "optional": self.optional,
            "partof": self.partof,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceAttributeEvidence:
        """Hydrate attribute evidence from a mapping."""

        return cls(
            owner_type=str(value.get("owner_type", "")),
            qualifier=str(value.get("qualifier", "")),
            declared_type=str(value.get("declared_type", "")),
            redeclare=_optional_bool(value.get("redeclare")),
            autocreate=_optional_bool(value.get("autocreate")),
            generate=_optional_bool(value.get("generate")),
            persistence_type=_optional_string(value.get("persistence_type")),
            attribute_handler=_optional_string(value.get("attribute_handler")),
            readable=_optional_bool(value.get("readable")),
            writable=_optional_bool(value.get("writable")),
            searchable=_optional_bool(value.get("searchable")),
            unique=_optional_bool(value.get("unique")),
            initial=_optional_bool(value.get("initial")),
            optional=_optional_bool(value.get("optional")),
            partof=_optional_bool(value.get("partof")),
        )


@dataclass(frozen=True)
class SapCommerceRelationEvidence:
    """One relation declaration and its explicit source/target endpoints."""

    code: str
    source: SapCommerceRelationEndpointEvidence
    target: SapCommerceRelationEndpointEvidence
    localized: bool | None = None
    autocreate: bool | None = None
    generate: bool | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize relation evidence."""

        return {
            "code": self.code,
            "source": self.source.to_mapping(),
            "target": self.target.to_mapping(),
            "localized": self.localized,
            "autocreate": self.autocreate,
            "generate": self.generate,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceRelationEvidence:
        """Hydrate relation evidence from a mapping."""

        raw_source = value.get("source")
        raw_target = value.get("target")
        return cls(
            code=str(value.get("code", "")),
            source=SapCommerceRelationEndpointEvidence.from_mapping(
                raw_source if isinstance(raw_source, Mapping) else {}
            ),
            target=SapCommerceRelationEndpointEvidence.from_mapping(
                raw_target if isinstance(raw_target, Mapping) else {}
            ),
            localized=_optional_bool(value.get("localized")),
            autocreate=_optional_bool(value.get("autocreate")),
            generate=_optional_bool(value.get("generate")),
        )


@dataclass(frozen=True)
class SapCommerceEnumTypeEvidence:
    """One enum type and the values explicitly declared in source."""

    code: str
    values: tuple[str, ...] = ()
    dynamic: bool | None = None
    autocreate: bool | None = None
    generate: bool | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize enum-type evidence."""

        return {
            "code": self.code,
            "values": list(self.values),
            "dynamic": self.dynamic,
            "autocreate": self.autocreate,
            "generate": self.generate,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceEnumTypeEvidence:
        """Hydrate enum-type evidence from a mapping."""

        return cls(
            code=str(value.get("code", "")),
            values=_strings(value.get("values")),
            dynamic=_optional_bool(value.get("dynamic")),
            autocreate=_optional_bool(value.get("autocreate")),
            generate=_optional_bool(value.get("generate")),
        )


@dataclass(frozen=True)
class SapCommerceCollectionTypeEvidence:
    """One collection type and its declared element type."""

    code: str
    element_type: str
    collection_kind: str
    autocreate: bool | None = None
    generate: bool | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize collection-type evidence."""

        return {
            "code": self.code,
            "element_type": self.element_type,
            "collection_kind": self.collection_kind,
            "autocreate": self.autocreate,
            "generate": self.generate,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceCollectionTypeEvidence:
        """Hydrate collection-type evidence from a mapping."""

        return cls(
            code=str(value.get("code", "")),
            element_type=str(value.get("element_type", "")),
            collection_kind=str(value.get("collection_kind", "")),
            autocreate=_optional_bool(value.get("autocreate")),
            generate=_optional_bool(value.get("generate")),
        )


@dataclass(frozen=True)
class SapCommerceMapTypeEvidence:
    """One map type with its declared argument and return types."""

    code: str
    argument_type: str
    return_type: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize map-type evidence."""

        return {
            "code": self.code,
            "argument_type": self.argument_type,
            "return_type": self.return_type,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceMapTypeEvidence:
        """Hydrate map-type evidence from a mapping."""

        return cls(
            code=str(value.get("code", "")),
            argument_type=str(value.get("argument_type", "")),
            return_type=str(value.get("return_type", "")),
        )


@dataclass(frozen=True)
class SapCommerceTypeSystemEvidence:
    """Exactly one typed semantic unit from an SAP Commerce items file."""

    kind: str
    item_type: SapCommerceItemTypeEvidence | None = None
    attribute: SapCommerceAttributeEvidence | None = None
    relation: SapCommerceRelationEvidence | None = None
    enum_type: SapCommerceEnumTypeEvidence | None = None
    collection_type: SapCommerceCollectionTypeEvidence | None = None
    map_type: SapCommerceMapTypeEvidence | None = None

    @property
    def symbol_name(self) -> str:
        """Return the semantic symbol represented by this type-system unit."""

        if self.item_type is not None:
            return self.item_type.code
        if self.attribute is not None:
            return self.attribute.qualified_name
        if self.relation is not None:
            return self.relation.code
        if self.enum_type is not None:
            return self.enum_type.code
        if self.collection_type is not None:
            return self.collection_type.code
        if self.map_type is not None:
            return self.map_type.code
        return "unknown"

    @property
    def referenced_types(self) -> tuple[str, ...]:
        """Return distinct type identities directly referenced by this unit."""

        values: list[str] = []
        if self.item_type is not None and self.item_type.extends:
            values.append(self.item_type.extends)
        if self.attribute is not None:
            values.append(self.attribute.declared_type)
        if self.relation is not None:
            values.extend((self.relation.source.type_name, self.relation.target.type_name))
        if self.collection_type is not None:
            values.append(self.collection_type.element_type)
        if self.map_type is not None:
            values.extend((self.map_type.argument_type, self.map_type.return_type))
        return tuple(dict.fromkeys(item for item in values if item))

    def to_mapping(self) -> dict[str, object]:
        """Serialize one typed type-system unit."""

        return {
            "kind": self.kind,
            "item_type": None if self.item_type is None else self.item_type.to_mapping(),
            "attribute": None if self.attribute is None else self.attribute.to_mapping(),
            "relation": None if self.relation is None else self.relation.to_mapping(),
            "enum_type": None if self.enum_type is None else self.enum_type.to_mapping(),
            "collection_type": (
                None if self.collection_type is None else self.collection_type.to_mapping()
            ),
            "map_type": None if self.map_type is None else self.map_type.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceTypeSystemEvidence:
        """Hydrate one typed type-system unit from a mapping."""

        return cls(
            kind=str(value.get("kind", "unknown")),
            item_type=_optional_mapped(value.get("item_type"), SapCommerceItemTypeEvidence),
            attribute=_optional_mapped(value.get("attribute"), SapCommerceAttributeEvidence),
            relation=_optional_mapped(value.get("relation"), SapCommerceRelationEvidence),
            enum_type=_optional_mapped(value.get("enum_type"), SapCommerceEnumTypeEvidence),
            collection_type=_optional_mapped(
                value.get("collection_type"), SapCommerceCollectionTypeEvidence
            ),
            map_type=_optional_mapped(value.get("map_type"), SapCommerceMapTypeEvidence),
        )


@dataclass(frozen=True)
class SapCommerceTypeSystemUnit:
    """One type-system semantic unit with exact source-line provenance."""

    evidence: SapCommerceTypeSystemEvidence
    line_start: int
    line_end: int


@dataclass
class _ItemBuilder:
    start: int
    code: str
    extends: str | None
    autocreate: bool | None
    generate: bool | None
    abstract: bool | None
    jalo_class: str | None
    deployment_table: str | None = None
    deployment_typecode: str | None = None


@dataclass
class _AttributeBuilder:
    start: int
    owner_type: str
    qualifier: str
    declared_type: str
    redeclare: bool | None
    autocreate: bool | None
    generate: bool | None
    persistence_type: str | None = None
    attribute_handler: str | None = None
    readable: bool | None = None
    writable: bool | None = None
    searchable: bool | None = None
    unique: bool | None = None
    initial: bool | None = None
    optional: bool | None = None
    partof: bool | None = None


@dataclass
class _RelationBuilder:
    start: int
    code: str
    localized: bool | None
    autocreate: bool | None
    generate: bool | None
    source: SapCommerceRelationEndpointEvidence | None = None
    target: SapCommerceRelationEndpointEvidence | None = None


@dataclass
class _EnumBuilder:
    start: int
    code: str
    dynamic: bool | None
    autocreate: bool | None
    generate: bool | None
    values: list[str]


class _ItemsHandler(ContentHandler):
    def __init__(self) -> None:
        super().__init__()
        self.locator: Any = None
        self.units: list[SapCommerceTypeSystemUnit] = []
        self.item: _ItemBuilder | None = None
        self.attribute: _AttributeBuilder | None = None
        self.relation: _RelationBuilder | None = None
        self.enum: _EnumBuilder | None = None

    def setDocumentLocator(self, locator: Any) -> None:
        self.locator = locator

    def startElement(self, name: str, attrs: Any) -> None:
        line = self._line()
        if name == "itemtype":
            code = attrs.get("code")
            if code:
                self.item = _ItemBuilder(
                    start=line,
                    code=code,
                    extends=attrs.get("extends"),
                    autocreate=_text_bool(attrs.get("autocreate")),
                    generate=_text_bool(attrs.get("generate")),
                    abstract=_text_bool(attrs.get("abstract")),
                    jalo_class=attrs.get("jaloclass"),
                )
        elif name == "attribute" and self.item is not None:
            qualifier = attrs.get("qualifier")
            declared_type = attrs.get("type")
            if qualifier and declared_type:
                self.attribute = _AttributeBuilder(
                    start=line,
                    owner_type=self.item.code,
                    qualifier=qualifier,
                    declared_type=declared_type,
                    redeclare=_text_bool(attrs.get("redeclare")),
                    autocreate=_text_bool(attrs.get("autocreate")),
                    generate=_text_bool(attrs.get("generate")),
                )
        elif name == "deployment" and self.item is not None and self.attribute is None:
            self.item.deployment_table = attrs.get("table")
            self.item.deployment_typecode = attrs.get("typecode")
        elif name == "persistence" and self.attribute is not None:
            self.attribute.persistence_type = attrs.get("type")
            self.attribute.attribute_handler = attrs.get("attributeHandler")
        elif name == "modifiers" and self.attribute is not None:
            self.attribute.readable = _text_bool(attrs.get("read"))
            self.attribute.writable = _text_bool(attrs.get("write"))
            self.attribute.searchable = _text_bool(attrs.get("search"))
            self.attribute.unique = _text_bool(attrs.get("unique"))
            self.attribute.initial = _text_bool(attrs.get("initial"))
            self.attribute.optional = _text_bool(attrs.get("optional"))
            self.attribute.partof = _text_bool(attrs.get("partof"))
        elif name == "relation":
            code = attrs.get("code")
            if code:
                self.relation = _RelationBuilder(
                    start=line,
                    code=code,
                    localized=_text_bool(attrs.get("localized")),
                    autocreate=_text_bool(attrs.get("autocreate")),
                    generate=_text_bool(attrs.get("generate")),
                )
        elif name == "sourceElement" and self.relation is not None:
            self.relation.source = _endpoint(attrs)
        elif name == "targetElement" and self.relation is not None:
            self.relation.target = _endpoint(attrs)
        elif name == "enumtype":
            code = attrs.get("code")
            if code:
                self.enum = _EnumBuilder(
                    start=line,
                    code=code,
                    dynamic=_text_bool(attrs.get("dynamic")),
                    autocreate=_text_bool(attrs.get("autocreate")),
                    generate=_text_bool(attrs.get("generate")),
                    values=[],
                )
        elif name == "value" and self.enum is not None:
            code = attrs.get("code")
            if code:
                self.enum.values.append(code)
        elif name == "collectiontype":
            code = attrs.get("code")
            element_type = attrs.get("elementtype")
            collection_kind = attrs.get("type")
            if code and element_type and collection_kind:
                self._append(
                    SapCommerceTypeSystemEvidence(
                        kind="collection-type",
                        collection_type=SapCommerceCollectionTypeEvidence(
                            code=code,
                            element_type=element_type,
                            collection_kind=collection_kind,
                            autocreate=_text_bool(attrs.get("autocreate")),
                            generate=_text_bool(attrs.get("generate")),
                        ),
                    ),
                    line,
                    line,
                )
        elif name == "maptype":
            code = attrs.get("code")
            argument_type = attrs.get("argumenttype")
            return_type = attrs.get("returntype")
            if code and argument_type and return_type:
                self._append(
                    SapCommerceTypeSystemEvidence(
                        kind="map-type",
                        map_type=SapCommerceMapTypeEvidence(
                            code=code,
                            argument_type=argument_type,
                            return_type=return_type,
                        ),
                    ),
                    line,
                    line,
                )

    def endElement(self, name: str) -> None:
        line = self._line()
        if name == "attribute" and self.attribute is not None:
            current_attribute = self.attribute
            self.attribute = None
            self._append(
                SapCommerceTypeSystemEvidence(
                    kind="attribute",
                    attribute=SapCommerceAttributeEvidence(
                        owner_type=current_attribute.owner_type,
                        qualifier=current_attribute.qualifier,
                        declared_type=current_attribute.declared_type,
                        redeclare=current_attribute.redeclare,
                        autocreate=current_attribute.autocreate,
                        generate=current_attribute.generate,
                        persistence_type=current_attribute.persistence_type,
                        attribute_handler=current_attribute.attribute_handler,
                        readable=current_attribute.readable,
                        writable=current_attribute.writable,
                        searchable=current_attribute.searchable,
                        unique=current_attribute.unique,
                        initial=current_attribute.initial,
                        optional=current_attribute.optional,
                        partof=current_attribute.partof,
                    ),
                ),
                current_attribute.start,
                line,
            )
        elif name == "itemtype" and self.item is not None:
            current_item = self.item
            self.item = None
            self._append(
                SapCommerceTypeSystemEvidence(
                    kind="item-type",
                    item_type=SapCommerceItemTypeEvidence(
                        code=current_item.code,
                        extends=current_item.extends,
                        autocreate=current_item.autocreate,
                        generate=current_item.generate,
                        abstract=current_item.abstract,
                        jalo_class=current_item.jalo_class,
                        deployment_table=current_item.deployment_table,
                        deployment_typecode=current_item.deployment_typecode,
                    ),
                ),
                current_item.start,
                line,
            )
        elif name == "relation" and self.relation is not None:
            current_relation = self.relation
            self.relation = None
            if current_relation.source is not None and current_relation.target is not None:
                self._append(
                    SapCommerceTypeSystemEvidence(
                        kind="relation",
                        relation=SapCommerceRelationEvidence(
                            code=current_relation.code,
                            source=current_relation.source,
                            target=current_relation.target,
                            localized=current_relation.localized,
                            autocreate=current_relation.autocreate,
                            generate=current_relation.generate,
                        ),
                    ),
                    current_relation.start,
                    line,
                )
        elif name == "enumtype" and self.enum is not None:
            current_enum = self.enum
            self.enum = None
            self._append(
                SapCommerceTypeSystemEvidence(
                    kind="enum-type",
                    enum_type=SapCommerceEnumTypeEvidence(
                        code=current_enum.code,
                        values=tuple(current_enum.values),
                        dynamic=current_enum.dynamic,
                        autocreate=current_enum.autocreate,
                        generate=current_enum.generate,
                    ),
                ),
                current_enum.start,
                line,
            )

    def _line(self) -> int:
        if self.locator is None:
            return 1
        return max(1, int(self.locator.getLineNumber()))

    def _append(
        self,
        evidence: SapCommerceTypeSystemEvidence,
        line_start: int,
        line_end: int,
    ) -> None:
        self.units.append(
            SapCommerceTypeSystemUnit(
                evidence=evidence,
                line_start=line_start,
                line_end=max(line_start, line_end),
            )
        )


def extract_sap_commerce_type_system_units(
    content: str,
) -> tuple[SapCommerceTypeSystemUnit, ...] | None:
    """Parse one items XML into typed semantic units, failing closed on unsafe XML."""

    if "<!DOCTYPE" in content.upper():
        return None
    handler = _ItemsHandler()
    parser = xml.sax.make_parser()
    for feature in (feature_external_ges, feature_external_pes):
        try:
            parser.setFeature(feature, False)
        except (xml.sax.SAXNotRecognizedException, xml.sax.SAXNotSupportedException):
            pass
    parser.setContentHandler(handler)
    try:
        parser.parse(io.StringIO(content))
    except xml.sax.SAXException:
        return None
    return tuple(
        sorted(
            handler.units,
            key=lambda item: (
                item.line_start,
                item.line_end,
                item.evidence.kind,
                item.evidence.symbol_name,
            ),
        )
    )


def render_sap_commerce_type_system_unit(evidence: SapCommerceTypeSystemEvidence) -> str:
    """Render a bounded searchable projection without copying unrelated XML payload."""

    lines = [f"SAP Commerce {evidence.kind}: {evidence.symbol_name}"]
    if evidence.item_type is not None:
        item_type = evidence.item_type
        _append_value(lines, "extends", item_type.extends)
        _append_value(lines, "deployment_table", item_type.deployment_table)
        _append_value(lines, "deployment_typecode", item_type.deployment_typecode)
    elif evidence.attribute is not None:
        attribute = evidence.attribute
        lines.append(f"owner_type: {attribute.owner_type}")
        lines.append(f"declared_type: {attribute.declared_type}")
        _append_value(lines, "persistence_type", attribute.persistence_type)
        _append_value(lines, "attribute_handler", attribute.attribute_handler)
    elif evidence.relation is not None:
        relation = evidence.relation
        lines.extend(
            (
                f"source_type: {relation.source.type_name}",
                f"source_qualifier: {relation.source.qualifier}",
                f"source_cardinality: {relation.source.cardinality}",
                f"target_type: {relation.target.type_name}",
                f"target_qualifier: {relation.target.qualifier}",
                f"target_cardinality: {relation.target.cardinality}",
            )
        )
    elif evidence.enum_type is not None:
        if evidence.enum_type.values:
            lines.append("values: " + ", ".join(evidence.enum_type.values))
    elif evidence.collection_type is not None:
        collection_type = evidence.collection_type
        lines.append(f"element_type: {collection_type.element_type}")
        lines.append(f"collection_kind: {collection_type.collection_kind}")
    elif evidence.map_type is not None:
        map_type = evidence.map_type
        lines.append(f"argument_type: {map_type.argument_type}")
        lines.append(f"return_type: {map_type.return_type}")
    return "\n".join(lines) + "\n"


def _endpoint(attrs: Any) -> SapCommerceRelationEndpointEvidence | None:
    type_name = attrs.get("type")
    qualifier = attrs.get("qualifier")
    cardinality = attrs.get("cardinality")
    if not type_name or not qualifier or not cardinality:
        return None
    return SapCommerceRelationEndpointEvidence(
        type_name=type_name,
        qualifier=qualifier,
        cardinality=cardinality,
        collection_type=attrs.get("collectiontype"),
        ordered=_text_bool(attrs.get("ordered")),
    )


def _append_value(lines: list[str], name: str, value: str | None) -> None:
    if value is not None:
        lines.append(f"{name}: {value}")


def _optional_mapped(value: object, factory: Any) -> Any:
    if not isinstance(value, Mapping):
        return None
    return factory.from_mapping(value)


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _text_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))