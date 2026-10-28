"""Provider-neutral declaration identity and verified extraction capabilities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Literal, cast, get_args


StructuralCapability = Literal[
    "source_only",
    "declarations",
    "imports",
    "calls_observed",
    "references_observed",
    "cross_file_resolution",
    "inheritance",
    "tests_observed",
]
ResolutionStatus = Literal["observed", "resolved", "unresolved", "ambiguous", "unsupported"]


@dataclass(frozen=True)
class DeclarationIdentity:
    """Logical declaration key, independent of revision, checksum and source span."""

    repository: str
    path: str
    language: str | None
    kind: str
    qualified_name: str
    discriminator: str | None = None

    def __post_init__(self) -> None:
        for value in (self.repository, self.path, self.kind, self.qualified_name):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("declaration identity requires non-empty key fields")
        if self.language is not None and not self.language.strip():
            raise ValueError("declaration identity language must not be empty")
        if self.discriminator is not None and not self.discriminator.strip():
            raise ValueError("declaration discriminator must not be empty")

    @property
    def key(self) -> str:
        payload = json.dumps(self.to_mapping(), sort_keys=True, separators=(",", ":"))
        return "symbol:v1:sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_mapping(self) -> dict[str, object]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> DeclarationIdentity:
        return cls(
            repository=required_text(value, "repository"),
            path=required_text(value, "path"),
            language=optional_text(value, "language"),
            kind=required_text(value, "kind"),
            qualified_name=required_text(value, "qualified_name"),
            discriminator=optional_text(value, "discriminator"),
        )


@dataclass(frozen=True)
class ExtractionCapabilities:
    """Producer/version and the kinds of facts it can actually observe."""

    language: str | None
    producer: str
    version: str
    capabilities: tuple[StructuralCapability, ...] = ("source_only",)

    def __post_init__(self) -> None:
        if not self.producer.strip() or not self.version.strip():
            raise ValueError("extraction producer and version must not be empty")
        if not self.capabilities or any(
            item not in get_args(StructuralCapability) for item in self.capabilities
        ):
            raise ValueError("invalid extraction capabilities")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("duplicate extraction capabilities")
        if "source_only" in self.capabilities and len(self.capabilities) != 1:
            raise ValueError("source_only cannot be combined with structural capabilities")

    def to_mapping(self) -> dict[str, object]:
        return {
            "language": self.language,
            "producer": self.producer,
            "version": self.version,
            "capabilities": list(self.capabilities),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ExtractionCapabilities:
        items = value.get("capabilities")
        if not isinstance(items, list) or any(
            not isinstance(item, str) or item not in get_args(StructuralCapability)
            for item in items
        ):
            raise ValueError("extraction capabilities must be a list of known capabilities")
        return cls(
            language=optional_text(value, "language"),
            producer=required_text(value, "producer"),
            version=required_text(value, "version"),
            capabilities=tuple(cast(StructuralCapability, item) for item in items),
        )


@dataclass(frozen=True)
class StructuralCoverage:
    """Explicit source coverage; a textual corpus is not a structural negative."""

    capability: StructuralCapability
    sources: int
    supported_sources: int
    unknown_sources: int
    producers: tuple[ExtractionCapabilities, ...]

    @property
    def status(self) -> Literal["supported", "partial", "unsupported", "unknown"]:
        if self.sources == 0 or self.unknown_sources == self.sources:
            return "unknown"
        if self.supported_sources == self.sources:
            return "supported"
        if self.supported_sources or self.unknown_sources:
            return "partial"
        return "unsupported"

    def to_mapping(self) -> dict[str, object]:
        return {
            "capability": self.capability, "status": self.status,
            "sources": self.sources, "supported_sources": self.supported_sources,
            "unknown_sources": self.unknown_sources,
            "producers": [item.to_mapping() for item in self.producers],
        }


def extraction_capabilities(
    extractor: object | None, language: str | None
) -> ExtractionCapabilities:
    """Read an explicit producer declaration; undeclared providers stay source-only."""

    if extractor is None:
        return ExtractionCapabilities(language, "fetchcore.source", "1")
    declared = getattr(extractor, "structural_capabilities", ("source_only",))
    if not isinstance(declared, tuple):
        raise ValueError("extractor structural_capabilities must be a tuple")
    return ExtractionCapabilities(
        language=language,
        producer=f"{type(extractor).__module__}.{type(extractor).__qualname__}",
        version=str(getattr(extractor, "indexing_contract_version", "1")),
        capabilities=declared,
    )


def required_text(value: Mapping[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return item


def optional_text(value: Mapping[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is None:
        return None
    return required_text(value, key)
