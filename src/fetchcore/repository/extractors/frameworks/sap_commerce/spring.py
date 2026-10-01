"""Typed Spring wiring evidence for SAP Commerce repository knowledge.

The extractor records bean identities, classes, aliases, imports, component scans,
named collection wiring, and explicit bean references. Literal values are
intentionally excluded so Spring configuration can be indexed without turning
arbitrary secrets/config values into canonical knowledge.
"""

from __future__ import annotations

import io
import re
import xml.sax
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from xml.sax.handler import ContentHandler, feature_external_ges, feature_external_pes


SAP_COMMERCE_SPRING_METADATA_KEY = "sap_commerce_spring"
_SPRING_SCHEMA_MARKER = "http://www.springframework.org/schema/"
_SPLIT_NAMES = re.compile(r"[\s,;]+")
_COLLECTION_ELEMENTS = frozenset({"list", "map", "set"})


@dataclass(frozen=True)
class SapCommerceSpringPropertyReferenceEvidence:
    """Explicit bean references attached to one Spring property."""

    name: str
    targets: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        """Serialize Spring property-reference evidence."""

        return {"name": self.name, "targets": list(self.targets)}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSpringPropertyReferenceEvidence:
        """Hydrate Spring property-reference evidence from a mapping."""

        return cls(name=str(value.get("name", "")), targets=_strings(value.get("targets")))


@dataclass(frozen=True)
class SapCommerceSpringNamespaceReferenceEvidence:
    """One explicit reference attribute on a Spring namespace element."""

    element: str
    attribute: str
    target: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize a namespace bean-reference attribute."""

        return {"element": self.element, "attribute": self.attribute, "target": self.target}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSpringNamespaceReferenceEvidence:
        """Hydrate a namespace bean-reference attribute from a mapping."""

        return cls(
            element=str(value.get("element", "")),
            attribute=str(value.get("attribute", "")),
            target=str(value.get("target", "")),
        )


@dataclass(frozen=True)
class SapCommerceSpringNamespaceWiringEvidence:
    """Top-level namespace element that explicitly references Spring beans."""

    element: str
    name: str
    references: tuple[SapCommerceSpringNamespaceReferenceEvidence, ...]

    @property
    def referenced_beans(self) -> tuple[str, ...]:
        """Return distinct bean identities referenced by the namespace element."""

        return _dedupe(tuple(item.target for item in self.references))

    def to_mapping(self) -> dict[str, object]:
        """Serialize namespace wiring evidence."""

        return {
            "element": self.element,
            "name": self.name,
            "references": [item.to_mapping() for item in self.references],
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSpringNamespaceWiringEvidence:
        """Hydrate namespace wiring evidence from a mapping."""

        return cls(
            element=str(value.get("element", "")),
            name=str(value.get("name", "")),
            references=_mapped_items(
                value.get("references"),
                SapCommerceSpringNamespaceReferenceEvidence.from_mapping,
            ),
        )


@dataclass(frozen=True)
class SapCommerceSpringCollectionWiringEvidence:
    """Named top-level Spring util collection and its explicit bean targets."""

    collection_kind: str
    name: str
    references: tuple[str, ...]

    @property
    def referenced_beans(self) -> tuple[str, ...]:
        """Return explicit bean identities referenced by the collection."""

        return self.references

    def to_mapping(self) -> dict[str, object]:
        """Serialize named collection wiring evidence."""

        return {
            "collection_kind": self.collection_kind,
            "name": self.name,
            "references": list(self.references),
        }

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSpringCollectionWiringEvidence:
        """Hydrate named collection wiring evidence from a mapping."""

        return cls(
            collection_kind=str(value.get("collection_kind", "unknown")),
            name=str(value.get("name", "")),
            references=_strings(value.get("references")),
        )


@dataclass(frozen=True)
class SapCommerceSpringBeanEvidence:
    """One Spring bean and only its structural/wiring facts."""

    name: str
    identity_kind: str
    aliases: tuple[str, ...] = ()
    class_name: str | None = None
    parent: str | None = None
    factory_bean: str | None = None
    factory_method: str | None = None
    depends_on: tuple[str, ...] = ()
    scope: str | None = None
    abstract: bool | None = None
    lazy_init: bool | None = None
    primary: bool | None = None
    property_references: tuple[SapCommerceSpringPropertyReferenceEvidence, ...] = ()
    constructor_references: tuple[str, ...] = ()
    namespace_references: tuple[SapCommerceSpringNamespaceReferenceEvidence, ...] = ()

    @property
    def referenced_beans(self) -> tuple[str, ...]:
        """Return all explicit bean identities referenced by this bean declaration."""

        values: list[str] = []
        if self.parent:
            values.append(self.parent)
        if self.factory_bean:
            values.append(self.factory_bean)
        values.extend(self.depends_on)
        for property_reference in self.property_references:
            values.extend(property_reference.targets)
        values.extend(self.constructor_references)
        values.extend(reference.target for reference in self.namespace_references)
        return _dedupe(tuple(values))

    def to_mapping(self) -> dict[str, object]:
        """Serialize Spring bean structural evidence."""

        return {
            "name": self.name,
            "identity_kind": self.identity_kind,
            "aliases": list(self.aliases),
            "class_name": self.class_name,
            "parent": self.parent,
            "factory_bean": self.factory_bean,
            "factory_method": self.factory_method,
            "depends_on": list(self.depends_on),
            "scope": self.scope,
            "abstract": self.abstract,
            "lazy_init": self.lazy_init,
            "primary": self.primary,
            "property_references": [item.to_mapping() for item in self.property_references],
            "constructor_references": list(self.constructor_references),
            "namespace_references": [item.to_mapping() for item in self.namespace_references],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceSpringBeanEvidence:
        """Hydrate Spring bean structural evidence from a mapping."""

        return cls(
            name=str(value.get("name", "")),
            identity_kind=str(value.get("identity_kind", "unknown")),
            aliases=_strings(value.get("aliases")),
            class_name=_optional_string(value.get("class_name")),
            parent=_optional_string(value.get("parent")),
            factory_bean=_optional_string(value.get("factory_bean")),
            factory_method=_optional_string(value.get("factory_method")),
            depends_on=_strings(value.get("depends_on")),
            scope=_optional_string(value.get("scope")),
            abstract=_optional_bool(value.get("abstract")),
            lazy_init=_optional_bool(value.get("lazy_init")),
            primary=_optional_bool(value.get("primary")),
            property_references=_mapped_items(
                value.get("property_references"),
                SapCommerceSpringPropertyReferenceEvidence.from_mapping,
            ),
            constructor_references=_strings(value.get("constructor_references")),
            namespace_references=_mapped_items(
                value.get("namespace_references"),
                SapCommerceSpringNamespaceReferenceEvidence.from_mapping,
            ),
        )


@dataclass(frozen=True)
class SapCommerceSpringAliasEvidence:
    """One explicit Spring alias declaration."""

    name: str
    alias: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize Spring alias evidence."""

        return {"name": self.name, "alias": self.alias}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceSpringAliasEvidence:
        """Hydrate Spring alias evidence from a mapping."""

        return cls(name=str(value.get("name", "")), alias=str(value.get("alias", "")))


@dataclass(frozen=True)
class SapCommerceSpringImportEvidence:
    """One Spring resource import declared in source."""

    resource: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize Spring import evidence."""

        return {"resource": self.resource}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceSpringImportEvidence:
        """Hydrate Spring import evidence from a mapping."""

        return cls(resource=str(value.get("resource", "")))


@dataclass(frozen=True)
class SapCommerceSpringComponentScanEvidence:
    """One Spring component-scan package declaration."""

    base_packages: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        """Serialize component-scan evidence."""

        return {"base_packages": list(self.base_packages)}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> SapCommerceSpringComponentScanEvidence:
        """Hydrate component-scan evidence from a mapping."""

        return cls(base_packages=_strings(value.get("base_packages")))


@dataclass(frozen=True)
class SapCommerceSpringEvidence:
    """Exactly one semantic Spring wiring unit."""

    kind: str
    bean: SapCommerceSpringBeanEvidence | None = None
    alias: SapCommerceSpringAliasEvidence | None = None
    imported_resource: SapCommerceSpringImportEvidence | None = None
    component_scan: SapCommerceSpringComponentScanEvidence | None = None
    namespace_wiring: SapCommerceSpringNamespaceWiringEvidence | None = None
    collection_wiring: SapCommerceSpringCollectionWiringEvidence | None = None

    @property
    def symbol_name(self) -> str:
        """Return the semantic identity represented by this Spring unit."""

        if self.bean is not None:
            return self.bean.name
        if self.alias is not None:
            return self.alias.alias
        if self.imported_resource is not None:
            return self.imported_resource.resource
        if self.component_scan is not None:
            return ",".join(self.component_scan.base_packages)
        if self.namespace_wiring is not None:
            return self.namespace_wiring.name
        if self.collection_wiring is not None:
            return self.collection_wiring.name
        return "unknown"

    @property
    def referenced_beans(self) -> tuple[str, ...]:
        """Return explicit bean identities referenced by this Spring unit."""

        if self.bean is not None:
            return self.bean.referenced_beans
        if self.alias is not None:
            return (self.alias.name,)
        if self.namespace_wiring is not None:
            return self.namespace_wiring.referenced_beans
        if self.collection_wiring is not None:
            return self.collection_wiring.referenced_beans
        return ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize one semantic Spring wiring unit."""

        return {
            "kind": self.kind,
            "bean": None if self.bean is None else self.bean.to_mapping(),
            "alias": None if self.alias is None else self.alias.to_mapping(),
            "imported_resource": (
                None if self.imported_resource is None else self.imported_resource.to_mapping()
            ),
            "component_scan": (
                None if self.component_scan is None else self.component_scan.to_mapping()
            ),
            "namespace_wiring": (
                None if self.namespace_wiring is None else self.namespace_wiring.to_mapping()
            ),
            "collection_wiring": (
                None if self.collection_wiring is None else self.collection_wiring.to_mapping()
            ),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceSpringEvidence:
        """Hydrate one semantic Spring wiring unit from a mapping."""

        return cls(
            kind=str(value.get("kind", "unknown")),
            bean=_optional_mapped(value.get("bean"), SapCommerceSpringBeanEvidence.from_mapping),
            alias=_optional_mapped(value.get("alias"), SapCommerceSpringAliasEvidence.from_mapping),
            imported_resource=_optional_mapped(
                value.get("imported_resource"), SapCommerceSpringImportEvidence.from_mapping
            ),
            component_scan=_optional_mapped(
                value.get("component_scan"), SapCommerceSpringComponentScanEvidence.from_mapping
            ),
            namespace_wiring=_optional_mapped(
                value.get("namespace_wiring"),
                SapCommerceSpringNamespaceWiringEvidence.from_mapping,
            ),
            collection_wiring=_optional_mapped(
                value.get("collection_wiring"),
                SapCommerceSpringCollectionWiringEvidence.from_mapping,
            ),
        )


@dataclass(frozen=True)
class SapCommerceSpringUnit:
    """One Spring semantic wiring unit with exact source-line provenance."""

    evidence: SapCommerceSpringEvidence
    line_start: int
    line_end: int


@dataclass
class _BeanBuilder:
    start: int
    name: str
    identity_kind: str
    aliases: tuple[str, ...]
    class_name: str | None
    parent: str | None
    factory_bean: str | None
    factory_method: str | None
    depends_on: tuple[str, ...]
    scope: str | None
    abstract: bool | None
    lazy_init: bool | None
    primary: bool | None
    property_references: dict[str, list[str]] = field(default_factory=dict)
    constructor_references: list[str] = field(default_factory=list)
    namespace_references: list[SapCommerceSpringNamespaceReferenceEvidence] = field(
        default_factory=list
    )
    current_property: str | None = None
    constructor_depth: int = 0


@dataclass
class _CollectionBuilder:
    start: int
    collection_kind: str
    name: str
    references: list[str] = field(default_factory=list)


class _SpringHandler(ContentHandler):
    def __init__(self) -> None:
        super().__init__()
        self.locator: Any = None
        self.units: list[SapCommerceSpringUnit] = []
        self.beans: list[_BeanBuilder] = []
        self.collections: list[_CollectionBuilder | None] = []

    def setDocumentLocator(self, locator: Any) -> None:
        self.locator = locator

    def startElement(self, raw_name: str, attrs: Any) -> None:
        name = _local_name(raw_name)
        line = self._line()
        if name == "bean":
            self._start_bean(attrs, line)
            return
        if name == "alias":
            target = attrs.get("name")
            alias = attrs.get("alias")
            if target and alias:
                self._append(
                    SapCommerceSpringEvidence(
                        kind="alias",
                        alias=SapCommerceSpringAliasEvidence(name=target, alias=alias),
                    ),
                    line,
                    line,
                )
            return
        if name == "import":
            resource = attrs.get("resource")
            if resource:
                self._append(
                    SapCommerceSpringEvidence(
                        kind="import",
                        imported_resource=SapCommerceSpringImportEvidence(resource=resource),
                    ),
                    line,
                    line,
                )
            return
        if name == "component-scan":
            packages = _split_names(attrs.get("base-package"))
            if packages:
                self._append(
                    SapCommerceSpringEvidence(
                        kind="component-scan",
                        component_scan=SapCommerceSpringComponentScanEvidence(
                            base_packages=packages
                        ),
                    ),
                    line,
                    line,
                )
            return

        if self.beans:
            bean = self.beans[-1]
            if name == "property":
                bean.current_property = attrs.get("name")
                target = attrs.get("ref")
                if bean.current_property and target:
                    self._add_property_reference(bean, bean.current_property, target)
                return
            if name == "constructor-arg":
                bean.constructor_depth += 1
                target = attrs.get("ref")
                if target:
                    bean.constructor_references.append(target)
                return
            if name == "ref":
                target = attrs.get("bean") or attrs.get("local")
                if target:
                    self._add_context_reference(bean, target)
                return
            if name == "entry":
                target = attrs.get("value-ref")
                if target:
                    self._add_context_reference(bean, target)
                return
        elif name in _COLLECTION_ELEMENTS:
            collection_name = attrs.get("id") or attrs.get("name")
            self.collections.append(
                _CollectionBuilder(
                    start=line,
                    collection_kind=name,
                    name=collection_name,
                )
                if collection_name
                else None
            )
            return
        elif name == "ref":
            target = attrs.get("bean") or attrs.get("local")
            if target:
                self._add_collection_reference(target)
            return
        elif name == "entry":
            target = attrs.get("value-ref")
            if target:
                self._add_collection_reference(target)
            return

        references = _reference_attributes(name, attrs)
        if not references:
            return
        if self.beans:
            self.beans[-1].namespace_references.extend(references)
            return
        if self.collections:
            for reference in references:
                self._add_collection_reference(reference.target)
            return
        unit_name = attrs.get("id") or attrs.get("name") or f"{name}:wiring"
        self._append(
            SapCommerceSpringEvidence(
                kind="namespace-wiring",
                namespace_wiring=SapCommerceSpringNamespaceWiringEvidence(
                    element=name,
                    name=unit_name,
                    references=references,
                ),
            ),
            line,
            line,
        )

    def endElement(self, raw_name: str) -> None:
        name = _local_name(raw_name)
        line = self._line()
        if name == "property" and self.beans:
            self.beans[-1].current_property = None
            return
        if name == "constructor-arg" and self.beans:
            self.beans[-1].constructor_depth = max(0, self.beans[-1].constructor_depth - 1)
            return
        if name == "bean" and self.beans:
            self._finish_bean(line)
            return
        if name in _COLLECTION_ELEMENTS and not self.beans and self.collections:
            current = self.collections.pop()
            if current is not None and current.references:
                self._append(
                    SapCommerceSpringEvidence(
                        kind="collection-wiring",
                        collection_wiring=SapCommerceSpringCollectionWiringEvidence(
                            collection_kind=current.collection_kind,
                            name=current.name,
                            references=_dedupe(tuple(current.references)),
                        ),
                    ),
                    current.start,
                    line,
                )

    def _start_bean(self, attrs: Any, line: int) -> None:
        bean_id = attrs.get("id")
        declared_names = _split_names(attrs.get("name"))
        class_name = attrs.get("class")
        if bean_id:
            bean_name = bean_id
            aliases = tuple(item for item in declared_names if item != bean_id)
            identity_kind = "named"
        elif declared_names:
            bean_name = declared_names[0]
            aliases = declared_names[1:]
            identity_kind = "named"
        else:
            bean_name = f"anonymous:{class_name or 'bean'}"
            aliases = ()
            identity_kind = "anonymous"
        self.beans.append(
            _BeanBuilder(
                start=line,
                name=bean_name,
                identity_kind=identity_kind,
                aliases=aliases,
                class_name=class_name,
                parent=attrs.get("parent"),
                factory_bean=attrs.get("factory-bean"),
                factory_method=attrs.get("factory-method"),
                depends_on=_split_names(attrs.get("depends-on")),
                scope=attrs.get("scope"),
                abstract=_text_bool(attrs.get("abstract")),
                lazy_init=_text_bool(attrs.get("lazy-init")),
                primary=_text_bool(attrs.get("primary")),
            )
        )

    def _finish_bean(self, line: int) -> None:
        current = self.beans.pop()
        property_references = tuple(
            SapCommerceSpringPropertyReferenceEvidence(
                name=property_name,
                targets=_dedupe(tuple(targets)),
            )
            for property_name, targets in current.property_references.items()
        )
        self._append(
            SapCommerceSpringEvidence(
                kind="bean",
                bean=SapCommerceSpringBeanEvidence(
                    name=current.name,
                    identity_kind=current.identity_kind,
                    aliases=current.aliases,
                    class_name=current.class_name,
                    parent=current.parent,
                    factory_bean=current.factory_bean,
                    factory_method=current.factory_method,
                    depends_on=current.depends_on,
                    scope=current.scope,
                    abstract=current.abstract,
                    lazy_init=current.lazy_init,
                    primary=current.primary,
                    property_references=property_references,
                    constructor_references=_dedupe(tuple(current.constructor_references)),
                    namespace_references=tuple(current.namespace_references),
                ),
            ),
            current.start,
            line,
        )

    def _add_property_reference(
        self,
        bean: _BeanBuilder,
        property_name: str,
        target: str,
    ) -> None:
        bean.property_references.setdefault(property_name, []).append(target)

    def _add_context_reference(self, bean: _BeanBuilder, target: str) -> None:
        if bean.current_property:
            self._add_property_reference(bean, bean.current_property, target)
        elif bean.constructor_depth:
            bean.constructor_references.append(target)
        else:
            bean.namespace_references.append(
                SapCommerceSpringNamespaceReferenceEvidence(
                    element="ref",
                    attribute="bean",
                    target=target,
                )
            )

    def _add_collection_reference(self, target: str) -> None:
        for collection in reversed(self.collections):
            if collection is not None:
                collection.references.append(target)
                return

    def _line(self) -> int:
        if self.locator is None:
            return 1
        return max(1, int(self.locator.getLineNumber()))

    def _append(
        self,
        evidence: SapCommerceSpringEvidence,
        line_start: int,
        line_end: int,
    ) -> None:
        self.units.append(
            SapCommerceSpringUnit(
                evidence=evidence,
                line_start=line_start,
                line_end=max(line_start, line_end),
            )
        )


def extract_sap_commerce_spring_units(
    content: str,
) -> tuple[SapCommerceSpringUnit, ...] | None:
    """Parse Spring XML into bounded semantic wiring units, failing closed."""

    if _SPRING_SCHEMA_MARKER not in content:
        return ()
    if "<!DOCTYPE" in content.upper():
        return None
    handler = _SpringHandler()
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


def render_sap_commerce_spring_unit(evidence: SapCommerceSpringEvidence) -> str:
    """Render searchable wiring facts without literal property/config values."""

    lines = [f"SAP Commerce Spring {evidence.kind}: {evidence.symbol_name}"]
    if evidence.bean is not None:
        bean = evidence.bean
        _append_value(lines, "class", bean.class_name)
        _append_value(lines, "parent", bean.parent)
        _append_value(lines, "factory_bean", bean.factory_bean)
        _append_value(lines, "factory_method", bean.factory_method)
        if bean.aliases:
            lines.append("aliases: " + ", ".join(bean.aliases))
        if bean.depends_on:
            lines.append("depends_on: " + ", ".join(bean.depends_on))
        for property_reference in bean.property_references:
            lines.append(
                f"property_ref {property_reference.name}: "
                + ", ".join(property_reference.targets)
            )
        if bean.constructor_references:
            lines.append("constructor_refs: " + ", ".join(bean.constructor_references))
        for namespace_reference in bean.namespace_references:
            lines.append(
                f"{namespace_reference.element}.{namespace_reference.attribute}: "
                f"{namespace_reference.target}"
            )
    elif evidence.alias is not None:
        lines.append(f"target: {evidence.alias.name}")
    elif evidence.imported_resource is not None:
        lines.append(f"resource: {evidence.imported_resource.resource}")
    elif evidence.component_scan is not None:
        lines.append("base_packages: " + ", ".join(evidence.component_scan.base_packages))
    elif evidence.namespace_wiring is not None:
        for namespace_reference in evidence.namespace_wiring.references:
            lines.append(
                f"{namespace_reference.element}.{namespace_reference.attribute}: "
                f"{namespace_reference.target}"
            )
    elif evidence.collection_wiring is not None:
        lines.append("references: " + ", ".join(evidence.collection_wiring.references))
    return "\n".join(lines) + "\n"


def _reference_attributes(
    element: str,
    attrs: Any,
) -> tuple[SapCommerceSpringNamespaceReferenceEvidence, ...]:
    values: list[SapCommerceSpringNamespaceReferenceEvidence] = []
    for attribute in attrs.getNames():
        attribute_name = _local_name(attribute)
        if attribute_name != "ref" and not attribute_name.endswith("-ref"):
            continue
        target = attrs.get(attribute)
        if target:
            values.append(
                SapCommerceSpringNamespaceReferenceEvidence(
                    element=element,
                    attribute=attribute_name,
                    target=target,
                )
            )
    return tuple(values)


def _local_name(value: str) -> str:
    return value.rsplit(":", 1)[-1].rsplit("}", 1)[-1]


def _split_names(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item for item in _SPLIT_NAMES.split(value.strip()) if item)


def _text_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item for item in values if item))


def _mapped_items(value: object, factory: Any) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(factory(item) for item in value if isinstance(item, Mapping))


def _optional_mapped(value: object, factory: Any) -> Any:
    if not isinstance(value, Mapping):
        return None
    return factory(value)


def _append_value(lines: list[str], name: str, value: str | None) -> None:
    if value is not None:
        lines.append(f"{name}: {value}")


# Persisted knowledge-pack fingerprints retain historical class paths.
SapCommerceSpringPropertyReferenceEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringNamespaceReferenceEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringNamespaceWiringEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringCollectionWiringEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringBeanEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringAliasEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringImportEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringComponentScanEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringEvidence.__module__ = "fetchcore.repository.sap_commerce_spring"
SapCommerceSpringUnit.__module__ = "fetchcore.repository.sap_commerce_spring"
_BeanBuilder.__module__ = "fetchcore.repository.sap_commerce_spring"
_CollectionBuilder.__module__ = "fetchcore.repository.sap_commerce_spring"
_SpringHandler.__module__ = "fetchcore.repository.sap_commerce_spring"
