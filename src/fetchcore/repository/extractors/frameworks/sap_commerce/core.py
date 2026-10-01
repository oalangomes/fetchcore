"""Typed SAP Commerce source evidence for canonical repository knowledge.

This module intentionally records only facts directly observable from SAP Commerce
project sources. It does not resolve dependency origin, compute effective runtime
closure, fetch product baselines, or create graph-specific inferred relationships.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TypeVar


_T = TypeVar("_T")


@dataclass(frozen=True)
class SapCommerceExtensionPackEvidence:
    """One extension pack declared by the SAP Commerce project manifest."""

    name: str
    version: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize extension-pack evidence."""

        return {"name": self.name, "version": self.version}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceExtensionPackEvidence:
        """Hydrate extension-pack evidence from a mapping."""

        return cls(
            name=str(value.get("name", "")),
            version=_optional_string(value.get("version")),
        )


@dataclass(frozen=True)
class SapCommercePropertySourceEvidence:
    """One properties source selected by `useConfig.properties`."""

    location: str
    persona: str | None = None
    aspect: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize manifest property-source evidence."""

        return {
            "location": self.location,
            "persona": self.persona,
            "aspect": self.aspect,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommercePropertySourceEvidence:
        """Hydrate manifest property-source evidence from a mapping."""

        return cls(
            location=str(value.get("location", "")),
            persona=_optional_string(value.get("persona")),
            aspect=_optional_string(value.get("aspect")),
        )


@dataclass(frozen=True)
class SapCommerceStorefrontAddonEvidence:
    """One storefront addon relationship declared by the project manifest."""

    addon: str
    storefront: str
    template: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize storefront-addon evidence."""

        return {
            "addon": self.addon,
            "storefront": self.storefront,
            "template": self.template,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceStorefrontAddonEvidence:
        """Hydrate storefront-addon evidence from a mapping."""

        return cls(
            addon=str(value.get("addon", "")),
            storefront=str(value.get("storefront", "")),
            template=_optional_string(value.get("template")),
        )


@dataclass(frozen=True)
class SapCommerceWebappEvidence:
    """One web application exposed by an SAP Commerce cloud aspect."""

    name: str
    context_path: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize webapp evidence."""

        return {"name": self.name, "context_path": self.context_path}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceWebappEvidence:
        """Hydrate webapp evidence from a mapping."""

        return cls(
            name=str(value.get("name", "")),
            context_path=_optional_string(value.get("context_path")),
        )


@dataclass(frozen=True)
class SapCommerceAspectEvidence:
    """One project manifest aspect and its explicitly declared webapps."""

    name: str
    webapps: tuple[SapCommerceWebappEvidence, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize cloud-aspect evidence."""

        return {
            "name": self.name,
            "webapps": [item.to_mapping() for item in self.webapps],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceAspectEvidence:
        """Hydrate cloud-aspect evidence from a mapping."""

        return cls(
            name=str(value.get("name", "")),
            webapps=_mapped_items(value.get("webapps"), SapCommerceWebappEvidence.from_mapping),
        )


@dataclass(frozen=True)
class SapCommerceProjectManifestEvidence:
    """Explicit SAP Commerce project/product coordinates and runtime selectors."""

    commerce_suite_version: str
    solr_version: str | None = None
    extension_packs: tuple[SapCommerceExtensionPackEvidence, ...] = ()
    extension_config_location: str | None = None
    property_sources: tuple[SapCommercePropertySourceEvidence, ...] = ()
    storefront_addons: tuple[SapCommerceStorefrontAddonEvidence, ...] = ()
    aspects: tuple[SapCommerceAspectEvidence, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize project-manifest evidence."""

        return {
            "commerce_suite_version": self.commerce_suite_version,
            "solr_version": self.solr_version,
            "extension_packs": [item.to_mapping() for item in self.extension_packs],
            "extension_config_location": self.extension_config_location,
            "property_sources": [item.to_mapping() for item in self.property_sources],
            "storefront_addons": [item.to_mapping() for item in self.storefront_addons],
            "aspects": [item.to_mapping() for item in self.aspects],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceProjectManifestEvidence:
        """Hydrate project-manifest evidence from a mapping."""

        return cls(
            commerce_suite_version=str(value.get("commerce_suite_version", "")),
            solr_version=_optional_string(value.get("solr_version")),
            extension_packs=_mapped_items(
                value.get("extension_packs"),
                SapCommerceExtensionPackEvidence.from_mapping,
            ),
            extension_config_location=_optional_string(value.get("extension_config_location")),
            property_sources=_mapped_items(
                value.get("property_sources"),
                SapCommercePropertySourceEvidence.from_mapping,
            ),
            storefront_addons=_mapped_items(
                value.get("storefront_addons"),
                SapCommerceStorefrontAddonEvidence.from_mapping,
            ),
            aspects=_mapped_items(value.get("aspects"), SapCommerceAspectEvidence.from_mapping),
        )


@dataclass(frozen=True)
class SapCommerceExtensionPathEvidence:
    """One path declaration from an extension-set configuration."""

    directory: str
    autoload: bool | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize extension path evidence."""

        return {"directory": self.directory, "autoload": self.autoload}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceExtensionPathEvidence:
        """Hydrate extension path evidence from a mapping."""

        return cls(
            directory=str(value.get("directory", "")),
            autoload=_optional_bool(value.get("autoload")),
        )


@dataclass(frozen=True)
class SapCommerceExtensionSetEvidence:
    """One local/cloud extension-set declaration, kept separate by profile."""

    profile: str
    paths: tuple[SapCommerceExtensionPathEvidence, ...] = ()
    extensions: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize extension-set evidence."""

        return {
            "profile": self.profile,
            "paths": [item.to_mapping() for item in self.paths],
            "extensions": list(self.extensions),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceExtensionSetEvidence:
        """Hydrate extension-set evidence from a mapping."""

        return cls(
            profile=str(value.get("profile", "unknown")),
            paths=_mapped_items(value.get("paths"), SapCommerceExtensionPathEvidence.from_mapping),
            extensions=_strings(value.get("extensions")),
        )


@dataclass(frozen=True)
class SapCommerceModuleEvidence:
    """One core/web module explicitly declared inside `extensioninfo.xml`."""

    kind: str
    generated: bool | None = None
    manager: str | None = None
    package_root: str | None = None
    web_root: str | None = None
    jsp_compile: bool | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize extension-module evidence."""

        return {
            "kind": self.kind,
            "generated": self.generated,
            "manager": self.manager,
            "package_root": self.package_root,
            "web_root": self.web_root,
            "jsp_compile": self.jsp_compile,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceModuleEvidence:
        """Hydrate extension-module evidence from a mapping."""

        return cls(
            kind=str(value.get("kind", "unknown")),
            generated=_optional_bool(value.get("generated")),
            manager=_optional_string(value.get("manager")),
            package_root=_optional_string(value.get("package_root")),
            web_root=_optional_string(value.get("web_root")),
            jsp_compile=_optional_bool(value.get("jsp_compile")),
        )


@dataclass(frozen=True)
class SapCommerceMetaEvidence:
    """One literal extension metadata pair."""

    key: str
    value: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize literal extension metadata evidence."""

        return {"key": self.key, "value": self.value}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceMetaEvidence:
        """Hydrate literal extension metadata evidence from a mapping."""

        return cls(key=str(value.get("key", "")), value=str(value.get("value", "")))


@dataclass(frozen=True)
class SapCommerceExtensionEvidence:
    """Direct extension topology observed from one `extensioninfo.xml`."""

    name: str
    distribution_kind: str
    requires_extensions: tuple[str, ...] = ()
    modules: tuple[SapCommerceModuleEvidence, ...] = ()
    metadata: tuple[SapCommerceMetaEvidence, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize extension topology evidence."""

        return {
            "name": self.name,
            "distribution_kind": self.distribution_kind,
            "requires_extensions": list(self.requires_extensions),
            "modules": [item.to_mapping() for item in self.modules],
            "metadata": [item.to_mapping() for item in self.metadata],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceExtensionEvidence:
        """Hydrate extension topology evidence from a mapping."""

        return cls(
            name=str(value.get("name", "")),
            distribution_kind=str(value.get("distribution_kind", "unknown")),
            requires_extensions=_strings(value.get("requires_extensions")),
            modules=_mapped_items(value.get("modules"), SapCommerceModuleEvidence.from_mapping),
            metadata=_mapped_items(value.get("metadata"), SapCommerceMetaEvidence.from_mapping),
        )


@dataclass(frozen=True)
class SapCommerceEvidence:
    """Backend-neutral SAP Commerce evidence attached to one source document."""

    project_manifest: SapCommerceProjectManifestEvidence | None = None
    extension_set: SapCommerceExtensionSetEvidence | None = None
    extension: SapCommerceExtensionEvidence | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize the supported SAP Commerce topology evidence envelope."""

        return {
            "project_manifest": (
                None if self.project_manifest is None else self.project_manifest.to_mapping()
            ),
            "extension_set": None if self.extension_set is None else self.extension_set.to_mapping(),
            "extension": None if self.extension is None else self.extension.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceEvidence:
        """Hydrate the supported SAP Commerce topology evidence envelope."""

        raw_manifest = value.get("project_manifest")
        raw_extension_set = value.get("extension_set")
        raw_extension = value.get("extension")
        return cls(
            project_manifest=(
                SapCommerceProjectManifestEvidence.from_mapping(raw_manifest)
                if isinstance(raw_manifest, Mapping)
                else None
            ),
            extension_set=(
                SapCommerceExtensionSetEvidence.from_mapping(raw_extension_set)
                if isinstance(raw_extension_set, Mapping)
                else None
            ),
            extension=(
                SapCommerceExtensionEvidence.from_mapping(raw_extension)
                if isinstance(raw_extension, Mapping)
                else None
            ),
        )


def extract_sap_commerce_evidence(path: str, content: str) -> SapCommerceEvidence | None:
    """Extract supported SAP Commerce project evidence, failing closed on invalid input."""

    filename = PurePosixPath(path).name.casefold()
    if filename == "manifest.json":
        manifest = _extract_project_manifest(content)
        return SapCommerceEvidence(project_manifest=manifest) if manifest is not None else None
    if filename in {"localextensions.xml", "cloudextensions.xml"}:
        extension_set = _extract_extension_set(filename, content)
        return SapCommerceEvidence(extension_set=extension_set) if extension_set is not None else None
    if filename == "extensioninfo.xml":
        extension = _extract_extension_info(path, content)
        return SapCommerceEvidence(extension=extension) if extension is not None else None
    return None


def sap_commerce_declared_dependencies(evidence: SapCommerceEvidence | None) -> tuple[str, ...]:
    """Return only source-declared extension dependencies for generic relation evidence."""

    if evidence is None or evidence.extension is None:
        return ()
    return evidence.extension.requires_extensions


def _extract_project_manifest(content: str) -> SapCommerceProjectManifestEvidence | None:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    commerce_suite_version = _mapping_string(decoded, "commerceSuiteVersion")
    if commerce_suite_version is None:
        return None

    extension_packs: list[SapCommerceExtensionPackEvidence] = []
    raw_packs = decoded.get("extensionPacks")
    if isinstance(raw_packs, list):
        for item in raw_packs:
            if not isinstance(item, Mapping):
                continue
            name = _mapping_string(item, "name")
            if name is None:
                continue
            extension_packs.append(
                SapCommerceExtensionPackEvidence(
                    name=name,
                    version=_mapping_string(item, "version"),
                )
            )

    extension_config_location: str | None = None
    property_sources: list[SapCommercePropertySourceEvidence] = []
    raw_use_config = decoded.get("useConfig")
    if isinstance(raw_use_config, Mapping):
        raw_extensions = raw_use_config.get("extensions")
        if isinstance(raw_extensions, Mapping):
            extension_config_location = _mapping_string(raw_extensions, "location")
        raw_properties = raw_use_config.get("properties")
        if isinstance(raw_properties, list):
            for item in raw_properties:
                if not isinstance(item, Mapping):
                    continue
                location = _mapping_string(item, "location")
                if location is None:
                    continue
                property_sources.append(
                    SapCommercePropertySourceEvidence(
                        location=location,
                        persona=_mapping_string(item, "persona"),
                        aspect=_mapping_string(item, "aspect"),
                    )
                )

    storefront_addons: list[SapCommerceStorefrontAddonEvidence] = []
    raw_addons = decoded.get("storefrontAddons")
    if isinstance(raw_addons, list):
        for item in raw_addons:
            if not isinstance(item, Mapping):
                continue
            addon = _mapping_string(item, "addon")
            storefront = _mapping_string(item, "storefront")
            if addon is None or storefront is None:
                continue
            storefront_addons.append(
                SapCommerceStorefrontAddonEvidence(
                    addon=addon,
                    storefront=storefront,
                    template=_mapping_string(item, "template"),
                )
            )

    aspects: list[SapCommerceAspectEvidence] = []
    raw_aspects = decoded.get("aspects")
    if isinstance(raw_aspects, list):
        for item in raw_aspects:
            if not isinstance(item, Mapping):
                continue
            name = _mapping_string(item, "name")
            if name is None:
                continue
            webapps: list[SapCommerceWebappEvidence] = []
            raw_webapps = item.get("webapps")
            if isinstance(raw_webapps, list):
                for raw_webapp in raw_webapps:
                    if not isinstance(raw_webapp, Mapping):
                        continue
                    webapp_name = _mapping_string(raw_webapp, "name")
                    if webapp_name is None:
                        continue
                    webapps.append(
                        SapCommerceWebappEvidence(
                            name=webapp_name,
                            context_path=_mapping_string(raw_webapp, "contextPath"),
                        )
                    )
            aspects.append(SapCommerceAspectEvidence(name=name, webapps=tuple(webapps)))

    return SapCommerceProjectManifestEvidence(
        commerce_suite_version=commerce_suite_version,
        solr_version=_mapping_string(decoded, "solrVersion"),
        extension_packs=tuple(extension_packs),
        extension_config_location=extension_config_location,
        property_sources=tuple(property_sources),
        storefront_addons=tuple(storefront_addons),
        aspects=tuple(aspects),
    )


def _extract_extension_set(
    filename: str,
    content: str,
) -> SapCommerceExtensionSetEvidence | None:
    root = _parse_xml(content)
    if root is None or _local_name(root.tag) != "hybrisconfig":
        return None
    extensions_node = _child(root, "extensions")
    if extensions_node is None:
        return None

    paths: list[SapCommerceExtensionPathEvidence] = []
    extensions: list[str] = []
    for child in extensions_node:
        name = _local_name(child.tag)
        if name == "path":
            directory = child.attrib.get("dir")
            if directory:
                paths.append(
                    SapCommerceExtensionPathEvidence(
                        directory=directory,
                        autoload=_text_bool(child.attrib.get("autoload")),
                    )
                )
        elif name == "extension":
            extension_name = child.attrib.get("name")
            if extension_name:
                extensions.append(extension_name)

    profile = "cloud" if filename == "cloudextensions.xml" else "local"
    return SapCommerceExtensionSetEvidence(
        profile=profile,
        paths=tuple(paths),
        extensions=tuple(extensions),
    )


def _extract_extension_info(path: str, content: str) -> SapCommerceExtensionEvidence | None:
    root = _parse_xml(content)
    if root is None or _local_name(root.tag) != "extensioninfo":
        return None
    extension_node = _child(root, "extension")
    if extension_node is None:
        return None
    name = extension_node.attrib.get("name")
    if not name:
        return None

    requires_extensions = tuple(
        child.attrib["name"]
        for child in extension_node
        if _local_name(child.tag) == "requires-extension" and child.attrib.get("name")
    )

    modules: list[SapCommerceModuleEvidence] = []
    metadata: list[SapCommerceMetaEvidence] = []
    for child in extension_node:
        child_name = _local_name(child.tag)
        if child_name == "coremodule":
            modules.append(
                SapCommerceModuleEvidence(
                    kind="core",
                    generated=_text_bool(child.attrib.get("generated")),
                    manager=child.attrib.get("manager"),
                    package_root=child.attrib.get("packageroot"),
                )
            )
        elif child_name == "webmodule":
            modules.append(
                SapCommerceModuleEvidence(
                    kind="web",
                    web_root=child.attrib.get("webroot"),
                    jsp_compile=_text_bool(child.attrib.get("jspcompile")),
                )
            )
        elif child_name == "meta":
            key = child.attrib.get("key")
            value = child.attrib.get("value")
            if key is not None and value is not None:
                metadata.append(SapCommerceMetaEvidence(key=key, value=value))

    return SapCommerceExtensionEvidence(
        name=name,
        distribution_kind=_extension_distribution_kind(path),
        requires_extensions=requires_extensions,
        modules=tuple(modules),
        metadata=tuple(metadata),
    )


def _extension_distribution_kind(path: str) -> str:
    normalized = "/" + str(PurePosixPath(path)).casefold().lstrip("/")
    if "/hybris/bin/custom/" in normalized:
        return "custom"
    return "unknown"


def _parse_xml(content: str) -> ET.Element | None:
    if "<!DOCTYPE" in content.upper():
        return None
    try:
        return ET.fromstring(content)
    except ET.ParseError:
        return None


def _child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in parent:
        if _local_name(child.tag) == name:
            return child
    return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _mapping_string(value: Mapping[str, object], key: str) -> str | None:
    observed = value.get(key)
    return observed if isinstance(observed, str) and observed else None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _text_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _mapped_items(
    value: object,
    factory: Callable[[Mapping[str, object]], _T],
) -> tuple[_T, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[_T] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(factory(item))
    return tuple(items)

# Persisted knowledge-pack fingerprints retain historical class paths.
SapCommerceExtensionPackEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommercePropertySourceEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceStorefrontAddonEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceWebappEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceAspectEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceProjectManifestEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceExtensionPathEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceExtensionSetEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceModuleEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceMetaEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceExtensionEvidence.__module__ = "fetchcore.repository.sap_commerce"
SapCommerceEvidence.__module__ = "fetchcore.repository.sap_commerce"
