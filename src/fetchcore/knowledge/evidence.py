"""Typed observable evidence carried by canonical repository knowledge documents.

The indexer records only facts directly observed from source structure or from the
chosen structural chunk. Framework and manifest semantics are promoted only when
dedicated, fail-closed extractors can prove them from syntax, supporting imports,
or explicit file-system conventions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Protocol, cast
from urllib.parse import urlsplit

from fetchcore.knowledge.structure import DeclarationIdentity, ExtractionCapabilities

from fetchcore.repository.calls import (
    CallEvidence,
    CallScope,
    extract_call_evidence,
    extract_dynamic_binding_invalidations,
)
from fetchcore.repository.documentation import (
    DocumentationEvidence,
    documentation_evidence_from_mapping,
    extract_documentation_evidence,
)
from fetchcore.repository.framework import (
    BehaviorScope,
    EventEvidence,
    FrameworkEvidence,
    RouteEvidence,
    extract_framework_evidence,
    proven_fastapi_receivers,
)
from fetchcore.repository.imports import ImportEvidence, extract_import_evidence
from fetchcore.repository.manifest import ManifestEvidence, extract_manifest_evidence
from fetchcore.repository.python_documentation import (
    extract_python_docstring_evidence,
)
from fetchcore.repository.references import ReferenceEvidence, extract_reference_evidence
from fetchcore.repository.semantic import (
    SemanticEvidenceEnvelope,
    SemanticRelationEvidence,
    semantic_evidence_from_mapping,
    semantic_relations_from_mapping,
)


@dataclass(frozen=True)
class SymbolEvidence:
    """Observed declaration facts for the primary or nested source symbol."""

    name: str
    kind: str
    line_start: int
    line_end: int
    qualified_name: str | None = None
    visibility: str | None = None
    container: str | None = None
    signature: str | None = None
    parameters: tuple[str, ...] = ()
    modifiers: tuple[str, ...] = ()
    annotations: tuple[str, ...] = ()
    decorators: tuple[str, ...] = ()
    identity: DeclarationIdentity | None = None
    parent_identity: str | None = None

    def __post_init__(self) -> None:
        if self.identity is not None:
            if (
                self.identity.kind != self.kind
                or self.identity.qualified_name != (self.qualified_name or self.name)
                or self.line_start < 1 or self.line_end < self.line_start
            ):
                raise ValueError("declaration identity or span conflicts with symbol evidence")
            if self.parent_identity == self.identity.key:
                raise ValueError("declaration cannot be its own parent")

    def to_mapping(self) -> dict[str, object]:
        """Serialize observed symbol evidence."""

        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "visibility": self.visibility,
            "container": self.container,
            "signature": self.signature,
            "parameters": list(self.parameters),
            "modifiers": list(self.modifiers),
            "annotations": list(self.annotations),
            "decorators": list(self.decorators),
            "line_start": self.line_start,
            "line_end": self.line_end,
            **(
                {"identity": self.identity.to_mapping(), "parent_identity": self.parent_identity}
                if self.identity is not None
                else {}
            ),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SymbolEvidence:
        """Hydrate symbol evidence from its canonical mapping."""

        raw_identity = value.get("identity")
        if raw_identity is not None and not isinstance(raw_identity, Mapping):
            raise ValueError("symbol identity must be an object")
        return cls(
            name=str(value.get("name", "")),
            kind=str(value.get("kind", "unknown")),
            line_start=_integer(value.get("line_start"), default=1),
            line_end=_integer(value.get("line_end"), default=1),
            qualified_name=_optional_string(value.get("qualified_name")),
            visibility=_optional_string(value.get("visibility")),
            container=_optional_string(value.get("container")),
            signature=_optional_string(value.get("signature")),
            parameters=_strings(value.get("parameters")),
            modifiers=_strings(value.get("modifiers")),
            annotations=_strings(value.get("annotations")),
            decorators=_strings(value.get("decorators")),
            identity=(
                DeclarationIdentity.from_mapping(raw_identity)
                if isinstance(raw_identity, Mapping) else None
            ),
            parent_identity=_optional_string(value.get("parent_identity")),
        )


@dataclass(frozen=True)
class StructureEvidence:
    """File-level source facts shared by chunks from the same source."""

    module: str | None = None
    package: str | None = None
    namespace: str | None = None
    imports: tuple[ImportEvidence, ...] = ()
    exports: tuple[str, ...] = ()

    @property
    def import_sources(self) -> tuple[str, ...]:
        """Return distinct import sources while retaining rich binding evidence."""

        return tuple(dict.fromkeys(item.source for item in self.imports if item.source))

    def to_mapping(self) -> dict[str, object]:
        """Serialize file-level structure evidence."""

        return {
            "module": self.module,
            "package": self.package,
            "namespace": self.namespace,
            "imports": [item.to_mapping() for item in self.imports],
            "exports": list(self.exports),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> StructureEvidence:
        """Hydrate structure evidence from its canonical mapping."""

        return cls(
            module=_optional_string(value.get("module")),
            package=_optional_string(value.get("package")),
            namespace=_optional_string(value.get("namespace")),
            imports=_import_evidence(value.get("imports")),
            exports=_strings(value.get("exports")),
        )


@dataclass(frozen=True)
class MarkdownReferenceEvidence:
    """A safe Markdown destination observed in source, not a resolved target."""

    target: str
    kind: str
    resolution: str = "observed"
    line: int | None = None

    def __post_init__(self) -> None:
        if not self.target.strip() or len(self.target) > 512:
            raise ValueError("Markdown reference target must contain 1-512 characters")
        if (
            any(character.isspace() or ord(character) < 32 for character in self.target)
            or "\\" in self.target
        ):
            raise ValueError("Markdown reference target is not URL-safe")
        if self.kind not in {"relative", "fragment", "external"}:
            raise ValueError("Markdown reference kind is invalid")
        if self.resolution not in {"observed", "resolved"}:
            raise ValueError("Markdown reference resolution is invalid")
        if self.line is not None and (
            isinstance(self.line, bool) or self.line < 1
        ):
            raise ValueError("Markdown reference line must be positive")
        try:
            parsed = urlsplit(self.target)
        except ValueError as exc:
            raise ValueError("Markdown reference target is malformed") from exc
        if parsed.query or "?" in self.target:
            raise ValueError("Markdown reference target must not contain a query")
        if parsed.fragment and (
            len(parsed.fragment) > 128
            or any(
                not (character.isalnum() or character in "-_.~%/")
                for character in parsed.fragment
            )
        ):
            raise ValueError("Markdown reference fragment is unsafe")
        if self.kind == "external":
            if (
                parsed.scheme.casefold() not in {"", "http", "https"}
                or not parsed.netloc
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("Markdown external reference is unsafe")
        elif self.kind == "fragment":
            if parsed.path or parsed.netloc or not parsed.fragment:
                raise ValueError("Markdown fragment reference is invalid")
        elif parsed.scheme or parsed.netloc or not parsed.path:
            raise ValueError("Markdown relative reference is invalid")

    def to_mapping(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "target": self.target,
            "kind": self.kind,
            "resolution": self.resolution,
        }
        if self.line is not None:
            payload["line"] = self.line
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> MarkdownReferenceEvidence:
        raw_target = value.get("target")
        raw_kind = value.get("kind")
        raw_resolution = value.get("resolution", "observed")
        if not isinstance(raw_target, str):
            raise ValueError("Markdown reference target must be a string")
        if not isinstance(raw_kind, str):
            raise ValueError("Markdown reference kind must be a string")
        if not isinstance(raw_resolution, str):
            raise ValueError("Markdown reference resolution must be a string")
        raw_line = value.get("line")
        line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None
        if raw_line is not None and line is None:
            raise ValueError("Markdown reference line must be an integer")
        return cls(
            target=raw_target,
            kind=raw_kind,
            resolution=raw_resolution,
            line=line,
        )


@dataclass(frozen=True)
class SectionEvidence:
    """Observed non-code structural unit selected by the chunker."""

    strategy: str
    kind: str
    name: str | None = None
    heading_level: int | None = None
    heading_path: tuple[str, ...] = ()
    heading_levels: tuple[int, ...] = ()
    section_id: str | None = None
    fragment_id: str | None = None
    code_languages: tuple[str, ...] = ()
    references: tuple[MarkdownReferenceEvidence, ...] = ()
    diagnostics: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.heading_level is not None and (
            isinstance(self.heading_level, bool) or self.heading_level not in range(1, 7)
        ):
            raise ValueError("Markdown heading level must be between 1 and 6")
        if any(
            isinstance(level, bool) or level not in range(1, 7)
            for level in self.heading_levels
        ):
            raise ValueError("Markdown heading ancestry levels must be between 1 and 6")
        if self.heading_path and self.heading_levels and (
            len(self.heading_path) != len(self.heading_levels)
        ):
            raise ValueError("Markdown heading path and ancestry levels must align")
        if self.heading_level is not None and self.heading_levels and (
            self.heading_level != self.heading_levels[-1]
        ):
            raise ValueError("Markdown heading level must match the final ancestry level")

    def to_mapping(self) -> dict[str, object]:
        """Serialize non-code section evidence."""

        payload: dict[str, object] = {
            "strategy": self.strategy,
            "kind": self.kind,
            "name": self.name,
        }
        if self.heading_level is not None:
            payload["heading_level"] = self.heading_level
        if self.heading_path:
            payload["heading_path"] = list(self.heading_path)
        if self.heading_levels:
            payload["heading_levels"] = list(self.heading_levels)
        if self.section_id is not None:
            payload["section_id"] = self.section_id
        if self.fragment_id is not None:
            payload["fragment_id"] = self.fragment_id
        if self.code_languages:
            payload["code_languages"] = list(self.code_languages)
        if self.references:
            payload["references"] = [item.to_mapping() for item in self.references]
        if self.diagnostics:
            payload["diagnostics"] = list(self.diagnostics)
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SectionEvidence:
        """Hydrate section evidence from its canonical mapping."""

        raw_level = value.get("heading_level")
        heading_level = (
            raw_level
            if isinstance(raw_level, int) and not isinstance(raw_level, bool)
            else None
        )
        if raw_level is not None and heading_level is None:
            raise ValueError("Markdown heading level must be an integer")
        raw_path = value.get("heading_path")
        if raw_path is not None and (
            not isinstance(raw_path, (list, tuple))
            or any(not isinstance(item, str) for item in raw_path)
        ):
            raise ValueError("Markdown heading path must be a list of strings")
        heading_path = tuple(raw_path) if isinstance(raw_path, (list, tuple)) else ()
        raw_levels = value.get("heading_levels")
        if raw_levels is not None and (
            not isinstance(raw_levels, (list, tuple))
            or any(
                not isinstance(item, int)
                or isinstance(item, bool)
                or item not in range(1, 7)
                for item in raw_levels
            )
        ):
            raise ValueError("Markdown heading levels must be a list of integers 1-6")
        heading_levels = tuple(raw_levels) if isinstance(raw_levels, (list, tuple)) else ()
        raw_references = value.get("references")
        if raw_references is not None and (
            not isinstance(raw_references, (list, tuple))
            or any(not isinstance(item, Mapping) for item in raw_references)
        ):
            raise ValueError("Markdown references must be a list of objects")
        raw_name = value.get("name")
        references = tuple(
            MarkdownReferenceEvidence.from_mapping(item)
            for item in raw_references
        ) if isinstance(raw_references, (list, tuple)) else ()
        return cls(
            strategy=str(value.get("strategy", "unknown")),
            kind=str(value.get("kind", "unknown")),
            name=raw_name if isinstance(raw_name, str) else None,
            heading_level=heading_level,
            heading_path=heading_path,
            heading_levels=heading_levels,
            section_id=_optional_string(value.get("section_id")),
            fragment_id=_optional_string(value.get("fragment_id")),
            code_languages=_strings(value.get("code_languages")),
            references=references,
            diagnostics=_strings(value.get("diagnostics")),
        )


@dataclass(frozen=True)
class BehaviorEvidence:
    """Behavioral facts with declaration or framework-level provenance."""

    methods: tuple[SymbolEvidence, ...] = ()
    routes: tuple[RouteEvidence, ...] = ()
    events_produced: tuple[EventEvidence, ...] = ()
    events_consumed: tuple[EventEvidence, ...] = ()
    config_keys: tuple[str, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize behavioral evidence."""

        return {
            "methods": [item.to_mapping() for item in self.methods],
            "routes": [item.to_mapping() for item in self.routes],
            "events_produced": [item.to_mapping() for item in self.events_produced],
            "events_consumed": [item.to_mapping() for item in self.events_consumed],
            "config_keys": list(self.config_keys),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> BehaviorEvidence:
        """Hydrate behavioral evidence from its canonical mapping."""

        methods: list[SymbolEvidence] = []
        raw_methods = value.get("methods", [])
        if isinstance(raw_methods, (list, tuple)):
            for item in raw_methods:
                if isinstance(item, Mapping):
                    methods.append(SymbolEvidence.from_mapping(item))
        return cls(
            methods=tuple(methods),
            routes=_route_evidence(value.get("routes")),
            events_produced=_event_evidence(
                value.get("events_produced"),
                direction="produced",
            ),
            events_consumed=_event_evidence(
                value.get("events_consumed"),
                direction="consumed",
            ),
            config_keys=_strings(value.get("config_keys")),
        )


@dataclass(frozen=True)
class TestTargetEvidence:
    """One structurally proven test-source exercise of a repository-local target."""

    target_source: str
    target_symbol: str | None
    binding: str
    import_line: int | None
    call_line: int | None
    resolution: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize structurally proven test-target evidence."""

        return {
            "target_source": self.target_source,
            "target_symbol": self.target_symbol,
            "binding": self.binding,
            "import_line": self.import_line,
            "call_line": self.call_line,
            "resolution": self.resolution,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> TestTargetEvidence:
        """Hydrate test-target evidence from its canonical mapping."""

        return cls(
            target_source=str(value.get("target_source", "")),
            target_symbol=_optional_string(value.get("target_symbol")),
            binding=str(value.get("binding", "")),
            import_line=_optional_int(value.get("import_line")),
            call_line=_optional_int(value.get("call_line")),
            resolution=str(value.get("resolution", "unknown")),
        )


@dataclass(frozen=True)
class RelationEvidence:
    """Relations directly observed or deterministically resolved from evidence."""

    dependencies: tuple[str, ...] = ()
    references: tuple[ReferenceEvidence, ...] = ()
    calls: tuple[CallEvidence, ...] = ()
    test_targets: tuple[TestTargetEvidence, ...] = ()
    semantic: tuple[SemanticRelationEvidence, ...] = ()

    def to_mapping(self) -> dict[str, object]:
        """Serialize directly observed source relationships."""

        return {
            "dependencies": list(self.dependencies),
            "references": [item.to_mapping() for item in self.references],
            "calls": [item.to_mapping() for item in self.calls],
            "test_targets": [item.to_mapping() for item in self.test_targets],
            "semantic": [item.to_mapping() for item in self.semantic],
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> RelationEvidence:
        """Hydrate relation evidence from its canonical mapping."""

        return cls(
            dependencies=_strings(value.get("dependencies")),
            references=_reference_evidence(value.get("references")),
            calls=_call_evidence(value.get("calls")),
            test_targets=_test_target_evidence(value.get("test_targets")),
            semantic=semantic_relations_from_mapping(value.get("semantic")),
        )


@dataclass(frozen=True)
class LexicalEvidence:
    """Source-proven identifier facts observed for one canonical chunk."""

    exact_identifiers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Normalize exact identifiers while preserving first-seen order."""

        normalized = tuple(
            dict.fromkeys(identifier for identifier in self.exact_identifiers if identifier)
        )
        if normalized != self.exact_identifiers:
            object.__setattr__(self, "exact_identifiers", normalized)

    def to_mapping(self) -> dict[str, object]:
        """Serialize source-proven lexical identifiers."""

        return {"exact_identifiers": list(self.exact_identifiers)}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> LexicalEvidence:
        """Hydrate lexical evidence from its canonical mapping."""

        data = value or {}
        return cls(exact_identifiers=_strings(data.get("exact_identifiers")))


@dataclass(frozen=True)
class KnowledgeEvidence:
    """Canonical backend-neutral evidence attached to one KnowledgeDocument."""

    symbol: SymbolEvidence | None = None
    structure: StructureEvidence = field(default_factory=StructureEvidence)
    section: SectionEvidence | None = None
    behavior: BehaviorEvidence = field(default_factory=BehaviorEvidence)
    relations: RelationEvidence = field(default_factory=RelationEvidence)
    manifest: ManifestEvidence | None = None
    documentation: tuple[DocumentationEvidence, ...] = ()
    lexical: LexicalEvidence = field(default_factory=LexicalEvidence)
    semantic: tuple[SemanticEvidenceEnvelope, ...] = ()
    declarations: tuple[SymbolEvidence, ...] = ()
    extraction: ExtractionCapabilities | None = None

    def __post_init__(self) -> None:
        if self.declarations and (
            self.extraction is None or any(item.identity is None for item in self.declarations)
        ):
            raise ValueError("canonical declarations require identities and extraction provenance")

    def to_mapping(self) -> dict[str, object]:
        """Serialize canonical repository evidence."""

        return {
            "symbol": None if self.symbol is None else self.symbol.to_mapping(),
            "structure": self.structure.to_mapping(),
            "section": None if self.section is None else self.section.to_mapping(),
            "behavior": self.behavior.to_mapping(),
            "relations": self.relations.to_mapping(),
            "manifest": None if self.manifest is None else self.manifest.to_mapping(),
            "documentation": [item.to_mapping() for item in self.documentation],
            "lexical": self.lexical.to_mapping(),
            "semantic": [item.to_mapping() for item in self.semantic],
            **(
                {
                    "declarations": [item.to_mapping() for item in self.declarations],
                    "extraction": self.extraction.to_mapping(),
                }
                if self.extraction is not None else {}
            ),
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object] | None) -> KnowledgeEvidence:
        """Hydrate canonical repository evidence from a mapping."""

        data = value or {}
        raw_symbol = data.get("symbol")
        raw_structure = data.get("structure")
        raw_section = data.get("section")
        raw_behavior = data.get("behavior")
        raw_relations = data.get("relations")
        raw_manifest = data.get("manifest")
        raw_lexical = data.get("lexical")
        raw_declarations = data.get("declarations", [])
        raw_extraction = data.get("extraction")
        if not isinstance(raw_declarations, list) or any(
            not isinstance(item, Mapping) for item in raw_declarations
        ):
            raise ValueError("declarations must be a list of symbol objects")
        if raw_extraction is not None and not isinstance(raw_extraction, Mapping):
            raise ValueError("extraction must be an object")
        return cls(
            symbol=(
                SymbolEvidence.from_mapping(raw_symbol)
                if isinstance(raw_symbol, Mapping)
                else None
            ),
            structure=(
                StructureEvidence.from_mapping(raw_structure)
                if isinstance(raw_structure, Mapping)
                else StructureEvidence()
            ),
            section=(
                SectionEvidence.from_mapping(raw_section)
                if isinstance(raw_section, Mapping)
                else None
            ),
            behavior=(
                BehaviorEvidence.from_mapping(raw_behavior)
                if isinstance(raw_behavior, Mapping)
                else BehaviorEvidence()
            ),
            relations=(
                RelationEvidence.from_mapping(raw_relations)
                if isinstance(raw_relations, Mapping)
                else RelationEvidence()
            ),
            manifest=(
                ManifestEvidence.from_mapping(raw_manifest)
                if isinstance(raw_manifest, Mapping)
                else None
            ),
            documentation=documentation_evidence_from_mapping(data.get("documentation")),
            lexical=LexicalEvidence.from_mapping(
                cast(Mapping[str, object], raw_lexical)
                if isinstance(raw_lexical, Mapping)
                else None
            ),
            semantic=semantic_evidence_from_mapping(data.get("semantic")),
            declarations=tuple(
                SymbolEvidence.from_mapping(item) for item in raw_declarations
            ),
            extraction=(
                ExtractionCapabilities.from_mapping(raw_extraction)
                if isinstance(raw_extraction, Mapping) else None
            ),
        )


def identify_declarations(
    symbols: tuple[SymbolEvidence, ...],
    *,
    repository: str,
    path: str,
    language: str | None,
) -> tuple[SymbolEvidence, ...]:
    """Assign source-local identities before chunking can duplicate declarations."""

    ordered = sorted(
        set(symbols),
        key=lambda item: (
            item.line_start, item.line_end, item.qualified_name or item.name,
            item.kind, item.signature or "",
        ),
    )
    groups: dict[tuple[str, str], list[SymbolEvidence]] = {}
    for symbol in ordered:
        groups.setdefault((symbol.kind, symbol.qualified_name or symbol.name), []).append(symbol)
    identified: list[SymbolEvidence] = []
    for symbol in ordered:
        group = groups[(symbol.kind, symbol.qualified_name or symbol.name)]
        discriminator = None
        if len(group) > 1:
            matching = [item for item in group if item.signature == symbol.signature]
            discriminator = f"signature:{symbol.signature or ''}"
            if len(matching) > 1:
                discriminator += f":occurrence:{matching.index(symbol) + 1}"
        identified.append(replace(
            symbol,
            identity=DeclarationIdentity(
                repository, path, language, symbol.kind,
                symbol.qualified_name or symbol.name, discriminator,
            ),
        ))
    result: list[SymbolEvidence] = []
    for symbol in identified:
        parents = [
            item for item in identified
            if symbol.container is not None
            and item.name == symbol.container
            and item != symbol
            and (symbol.qualified_name or "").startswith(
                (item.qualified_name or item.name) + "."
            )
        ]
        parent = parents[0] if len(parents) == 1 else None
        result.append(replace(
            symbol,
            parent_identity=(
                parent.identity.key if parent is not None and parent.identity is not None else None
            ),
        ))
    return tuple(result)


def source_declarations(
    structure: _StructureObservation | None,
    *,
    repository: str,
    path: str,
    language: str | None,
    fallback_symbols: tuple[object, ...] = (),
) -> tuple[SymbolEvidence, ...]:
    """Preserve every observed declaration, including members outside chunk roots."""

    return identify_declarations(
        tuple(
            evidence for item in (
                structure.symbols if structure is not None else fallback_symbols
            )
            if (evidence := _symbol_evidence(item)) is not None
        ),
        repository=repository, path=path, language=language,
    )


def bind_declarations(
    evidence: KnowledgeEvidence,
    declarations: tuple[SymbolEvidence, ...],
    extraction: ExtractionCapabilities,
    *,
    line_start: int,
    line_end: int,
) -> KnowledgeEvidence:
    """Attach the canonical source-local declaration view to overlapping chunks."""

    selected = tuple(
        item for item in declarations
        if item.line_start <= line_end and item.line_end >= line_start
    )

    def identified(symbol: SymbolEvidence) -> SymbolEvidence:
        return next((
            item for item in selected
            if item.name == symbol.name and item.kind == symbol.kind
            and item.line_start == symbol.line_start and item.line_end == symbol.line_end
        ), symbol)

    return replace(
        evidence,
        symbol=identified(evidence.symbol) if evidence.symbol is not None else None,
        behavior=replace(
            evidence.behavior,
            methods=tuple(identified(item) for item in evidence.behavior.methods),
        ),
        declarations=selected,
        extraction=extraction,
    )


class _SymbolObservation(Protocol):
    name: str
    symbol_type: str
    line_start: int
    line_end: int
    qualified_name: str | None
    visibility: str | None
    container: str | None
    signature: str | None
    parameters: tuple[str, ...]
    modifiers: tuple[str, ...]
    annotations: tuple[str, ...]
    decorators: tuple[str, ...]


class _RawStructureObservation(Protocol):
    language: str
    module: str | None
    package: str | None
    namespace: str | None
    imports: tuple[str, ...]
    exports: tuple[str, ...]
    symbols: tuple[_SymbolObservation, ...]
    chunk_symbols: tuple[_SymbolObservation, ...]


class _StructureObservation(Protocol):
    @property
    def language(self) -> str: ...

    @property
    def path(self) -> str: ...

    @property
    def module(self) -> str | None: ...

    @property
    def package(self) -> str | None: ...

    @property
    def namespace(self) -> str | None: ...

    @property
    def imports(self) -> tuple[str, ...]: ...

    @property
    def typed_imports(self) -> tuple[ImportEvidence, ...]: ...

    @property
    def dynamic_binding_invalidations(self) -> tuple[tuple[str, int], ...]: ...

    @property
    def proven_fastapi_receivers(self) -> tuple[str, ...]: ...

    @property
    def exports(self) -> tuple[str, ...]: ...

    @property
    def symbols(self) -> tuple[_SymbolObservation, ...]: ...

    @property
    def chunk_symbols(self) -> tuple[_SymbolObservation, ...]: ...

    @property
    def documentation(self) -> tuple[DocumentationEvidence, ...]: ...

    @property
    def manifest(self) -> ManifestEvidence | None: ...


@dataclass(frozen=True)
class _EnrichedStructureObservation:
    language: str
    path: str
    module: str | None
    package: str | None
    namespace: str | None
    imports: tuple[str, ...]
    typed_imports: tuple[ImportEvidence, ...]
    dynamic_binding_invalidations: tuple[tuple[str, int], ...]
    proven_fastapi_receivers: tuple[str, ...]
    exports: tuple[str, ...]
    symbols: tuple[_SymbolObservation, ...]
    chunk_symbols: tuple[_SymbolObservation, ...]
    documentation: tuple[DocumentationEvidence, ...]
    manifest: ManifestEvidence | None


class _ExtractStructure(Protocol):
    def __call__(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> _RawStructureObservation: ...


def extract_source_structure(
    extractor: object | None,
    content: str,
    *,
    path: str,
) -> _StructureObservation | None:
    """Invoke rich source extraction and enrich observed source-level evidence."""

    manifest = extract_manifest_evidence(path, content)
    if extractor is None:
        if manifest is None:
            return None
        return _EnrichedStructureObservation(
            language=_manifest_source_language(path),
            path=path,
            module=None,
            package=None,
            namespace=None,
            imports=(),
            typed_imports=(),
            dynamic_binding_invalidations=(),
            proven_fastapi_receivers=(),
            exports=(),
            symbols=(),
            chunk_symbols=(),
            documentation=(),
            manifest=manifest,
        )
    raw = getattr(extractor, "extract_structure", None)
    if not callable(raw):
        return None
    method = cast(_ExtractStructure, raw)
    structure = method(content, path=path)
    observed = extract_import_evidence(structure.language, content)
    typed_imports = _complete_import_evidence(structure.imports, observed)
    dynamic_binding_invalidations = extract_dynamic_binding_invalidations(
        structure.language,
        content,
        typed_imports,
    )
    if structure.language.casefold() == "python":
        documentation = extract_python_docstring_evidence(
            content,
            structure.symbols,
            module=structure.module,
        )
    else:
        documentation = extract_documentation_evidence(
            structure.language,
            content,
            structure.symbols,
        )
    return _EnrichedStructureObservation(
        language=structure.language,
        path=path,
        module=structure.module,
        package=structure.package,
        namespace=structure.namespace,
        imports=structure.imports,
        typed_imports=typed_imports,
        dynamic_binding_invalidations=dynamic_binding_invalidations,
        proven_fastapi_receivers=(
            proven_fastapi_receivers(content) if structure.language.casefold() == "python" else ()
        ),
        exports=structure.exports,
        symbols=structure.symbols,
        chunk_symbols=_safe_rich_chunk_symbols(structure.chunk_symbols),
        documentation=documentation,
        manifest=manifest,
    )


def _safe_rich_chunk_symbols(
    values: tuple[_SymbolObservation, ...],
) -> tuple[_SymbolObservation, ...]:
    """Fall back to line chunking when a rich extractor emits overlapping spans."""

    ordered = tuple(
        sorted(
            values,
            key=lambda item: (
                int(getattr(item, "line_start", 0)),
                int(getattr(item, "line_end", 0)),
            ),
        )
    )
    cursor = 1
    previous_span: tuple[int, int] | None = None
    for item in ordered:
        line_start = int(getattr(item, "line_start", 0))
        line_end = int(getattr(item, "line_end", 0))
        span = (line_start, line_end)
        if span == previous_span:
            continue
        if line_start >= 1 and line_end >= line_start:
            if line_start < cursor:
                return ()
            cursor = line_end + 1
        previous_span = span
    return ordered


def build_knowledge_evidence(
    structure: _StructureObservation | None,
    *,
    primary_symbol: object | None,
    discriminator: str,
    strategy: str,
    chunk_content: str = "",
    chunk_line_start: int = 1,
    section_evidence: SectionEvidence | None = None,
    lexical_content: str | None = None,
    lexical_identifiers: tuple[str, ...] = (),
    knowledge_type: str = "code",
) -> KnowledgeEvidence:
    """Project observed source/chunk facts into the canonical evidence contract."""

    symbol = _symbol_evidence(primary_symbol)
    file_structure = StructureEvidence()
    dependencies: tuple[str, ...] = ()
    references: tuple[ReferenceEvidence, ...] = ()
    calls: tuple[CallEvidence, ...] = ()
    test_targets: tuple[TestTargetEvidence, ...] = ()
    methods: tuple[SymbolEvidence, ...] = ()
    documentation: tuple[DocumentationEvidence, ...] = ()
    manifest: ManifestEvidence | None = None
    framework = FrameworkEvidence()

    if structure is not None:
        file_structure = StructureEvidence(
            module=structure.module,
            package=structure.package,
            namespace=structure.namespace,
            imports=structure.typed_imports,
            exports=_ordered_unique(structure.exports),
        )
        dependencies = _ordered_unique(structure.imports)
        manifest = structure.manifest
        if manifest is not None:
            dependencies = _ordered_unique(
                (*dependencies, *(item.name for item in manifest.dependencies))
            )
        if symbol is not None:
            methods = tuple(
                observed
                for observed in (
                    _symbol_evidence(item)
                    for item in structure.symbols
                    if item.container == symbol.name
                    and item.symbol_type in {"method", "constructor"}
                )
                if observed is not None
            )
            owned_symbols = {
                symbol.qualified_name or symbol.name,
                *(method.qualified_name or method.name for method in methods),
            }
            documentation = tuple(
                item
                for item in structure.documentation
                if item.source_symbol in owned_symbols
            )
        elif chunk_content and structure.language.casefold() == "python":
            observed_owners = {
                item.qualified_name or item.name for item in structure.symbols
            }
            chunk_line_end = chunk_line_start + len(chunk_content.splitlines()) - 1
            documentation = tuple(
                item
                for item in structure.documentation
                if item.source_symbol not in observed_owners
                and item.line_start <= chunk_line_end
                and item.line_end >= chunk_line_start
            )

        if chunk_content:
            source_symbol = (
                symbol.qualified_name or symbol.name
                if symbol is not None
                else structure.module or structure.package or structure.namespace
            )
            behavior_scopes = tuple(
                BehaviorScope(
                    source_symbol=(
                        method.qualified_name
                        or (
                            f"{source_symbol}.{method.name}"
                            if source_symbol
                            else method.name
                        )
                    ),
                    line_start=method.line_start,
                    line_end=method.line_end,
                )
                for method in methods
            )
            framework = extract_framework_evidence(
                structure.language,
                chunk_content,
                structure.typed_imports,
                source_symbol=source_symbol,
                source_path=structure.path,
                line_start=chunk_line_start,
                scopes=behavior_scopes,
                lexical_content=lexical_content,
                proven_fastapi_receivers=structure.proven_fastapi_receivers,
            )

            relation_symbol = (
                symbol.qualified_name or symbol.name
                if symbol is not None
                else structure.path
                if knowledge_type == "tests"
                else None
            )
            if symbol is not None:
                references = extract_reference_evidence(
                    structure.language,
                    chunk_content,
                    structure.typed_imports,
                    source_symbol=relation_symbol or structure.path,
                    line_start=chunk_line_start,
                    lexical_content=lexical_content,
                )
            if relation_symbol is not None:
                call_scopes = tuple(
                    CallScope(
                        source_symbol=(
                            method.qualified_name
                            or (
                                f"{source_symbol}.{method.name}"
                                if source_symbol
                                else method.name
                            )
                        ),
                        line_start=method.line_start,
                        line_end=method.line_end,
                        shadowed_bindings=_shadowed_dynamic_bindings(
                            structure.language,
                            method.parameters,
                            structure.typed_imports,
                        ),
                    )
                    for method in methods
                )
                calls = extract_call_evidence(
                    structure.language,
                    chunk_content,
                    structure.typed_imports,
                    source_symbol=relation_symbol,
                    line_start=chunk_line_start,
                    scopes=call_scopes,
                    lexical_content=lexical_content,
                    root_shadowed_bindings=(
                        _shadowed_dynamic_bindings(
                            structure.language,
                            symbol.parameters,
                            structure.typed_imports,
                        )
                        if symbol is not None
                        else ()
                    ),
                    dynamic_binding_invalidations=(
                        structure.dynamic_binding_invalidations
                    ),
                )
                if knowledge_type == "tests":
                    test_targets = _derive_test_targets(
                        calls,
                        structure.typed_imports,
                    )

    section = (
        section_evidence
        if section_evidence is not None
        else _section_evidence(strategy, discriminator)
    )
    config_keys = (
        (section.name,)
        if section is not None and section.kind == "config-key" and section.name
        else ()
    )
    return KnowledgeEvidence(
        symbol=symbol,
        structure=file_structure,
        section=section,
        behavior=BehaviorEvidence(
            methods=methods,
            routes=framework.routes,
            events_produced=framework.events_produced,
            events_consumed=framework.events_consumed,
            config_keys=config_keys,
        ),
        relations=RelationEvidence(
            dependencies=dependencies,
            references=references,
            calls=calls,
            test_targets=test_targets,
        ),
        manifest=manifest,
        documentation=documentation,
        lexical=LexicalEvidence(exact_identifiers=lexical_identifiers),
    )


def _symbol_evidence(value: object | None) -> SymbolEvidence | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    kind = getattr(value, "symbol_type", None)
    line_start = getattr(value, "line_start", None)
    line_end = getattr(value, "line_end", None)
    if not isinstance(name, str) or not isinstance(kind, str):
        return None
    if not isinstance(line_start, int) or not isinstance(line_end, int):
        return None
    return SymbolEvidence(
        name=name,
        kind=kind,
        line_start=line_start,
        line_end=line_end,
        qualified_name=_attribute_string(value, "qualified_name"),
        visibility=_attribute_string(value, "visibility"),
        container=_attribute_string(value, "container"),
        signature=_attribute_string(value, "signature"),
        parameters=_attribute_strings(value, "parameters"),
        modifiers=_attribute_strings(value, "modifiers"),
        annotations=_attribute_strings(value, "annotations"),
        decorators=_attribute_strings(value, "decorators"),
    )


def _section_evidence(strategy: str, discriminator: str) -> SectionEvidence | None:
    patterns: tuple[tuple[str, str, str], ...] = (
        ("markdown-heading", "heading", r"^markdown:heading:\d+:(.*):occurrence:\d+:segment:\d+$"),
        ("json-key", "config-key", r"^json:key:(.*):occurrence:\d+:segment:\d+$"),
        (
            "yaml-key",
            "config-key",
            r"^yaml:key:(.*):occurrence:\d+:document:\d+:segment:\d+$",
        ),
        ("xml-element", "xml-element", r"^xml:element:(.*):occurrence:\d+:segment:\d+$"),
    )
    for expected_strategy, kind, pattern in patterns:
        if strategy != expected_strategy:
            continue
        match = re.match(pattern, discriminator)
        if match is not None:
            return SectionEvidence(strategy=strategy, kind=kind, name=match.group(1))
    if strategy in {
        "markdown-preamble",
        "json-object",
        "yaml-document",
        "xml-envelope",
    }:
        return SectionEvidence(strategy=strategy, kind=strategy)
    return None


def _manifest_source_language(path: str) -> str:
    suffix = PurePosixPath(path).suffix.casefold()
    return {
        ".json": "json",
        ".xml": "xml",
    }.get(suffix, "unknown")


def _complete_import_evidence(
    raw_sources: tuple[str, ...],
    observed: tuple[ImportEvidence, ...],
) -> tuple[ImportEvidence, ...]:
    """Keep raw dependency coverage when a typed parser cannot prove a binding."""

    values = list(observed)
    covered_sources = {item.source for item in observed}
    values.extend(
        ImportEvidence.legacy(source)
        for source in raw_sources
        if source not in covered_sources
    )
    return tuple(values)


def _import_evidence(value: object) -> tuple[ImportEvidence, ...]:
    if isinstance(value, str):
        return (ImportEvidence.legacy(value),)
    if not isinstance(value, (list, tuple)):
        return ()
    imports: list[ImportEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            imports.append(ImportEvidence.from_mapping(item))
        elif isinstance(item, str):
            imports.append(ImportEvidence.legacy(item))
    return tuple(imports)


def _route_evidence(value: object) -> tuple[RouteEvidence, ...]:
    if isinstance(value, str):
        return (RouteEvidence.legacy(value),)
    if not isinstance(value, (list, tuple)):
        return ()
    routes: list[RouteEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            routes.append(RouteEvidence.from_mapping(item))
        elif isinstance(item, str):
            routes.append(RouteEvidence.legacy(item))
    return tuple(routes)


def _event_evidence(
    value: object,
    *,
    direction: str,
) -> tuple[EventEvidence, ...]:
    if isinstance(value, str):
        return (EventEvidence.legacy(value, direction=direction),)
    if not isinstance(value, (list, tuple)):
        return ()
    events: list[EventEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            events.append(EventEvidence.from_mapping(item))
        elif isinstance(item, str):
            events.append(EventEvidence.legacy(item, direction=direction))
    return tuple(events)


def _reference_evidence(value: object) -> tuple[ReferenceEvidence, ...]:
    if isinstance(value, str):
        return (ReferenceEvidence.legacy(value),)
    if not isinstance(value, (list, tuple)):
        return ()
    references: list[ReferenceEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            references.append(ReferenceEvidence.from_mapping(item))
        elif isinstance(item, str):
            references.append(ReferenceEvidence.legacy(item))
    return tuple(references)


def _shadowed_dynamic_bindings(
    language: str,
    parameters: tuple[str, ...],
    imports: tuple[ImportEvidence, ...],
) -> tuple[str, ...]:
    """Fail closed for dynamic bindings when JS/TS parameter binding is complex."""

    if language not in {"javascript", "typescript"} or not parameters:
        return parameters
    identifier = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")
    if all(identifier.fullmatch(parameter) for parameter in parameters):
        return parameters

    dynamic_bindings = tuple(
        dict.fromkeys(
            item.name
            for item in imports
            if item.kind == "dynamic"
            and item.name != item.source
            and identifier.fullmatch(item.name)
        )
    )
    return tuple(dict.fromkeys((*parameters, *dynamic_bindings)))


def _derive_test_targets(
    calls: tuple[CallEvidence, ...],
    imports: tuple[ImportEvidence, ...],
) -> tuple[TestTargetEvidence, ...]:
    """Promote only proven dynamic-import calls in already classified test sources."""

    imports_by_binding = {
        item.name: item
        for item in imports
        if item.kind == "dynamic"
        and item.name != item.source
        and item.source.startswith(("./", "../"))
    }
    values: dict[tuple[object, ...], TestTargetEvidence] = {}
    for call in calls:
        if call.resolution != "dynamic_import_binding" or call.source is None:
            continue
        binding, _, target_symbol = call.callee.partition(".")
        imported = imports_by_binding.get(binding)
        if imported is None or imported.source != call.source or not target_symbol:
            continue
        evidence = TestTargetEvidence(
            target_source=imported.source,
            target_symbol=target_symbol,
            binding=binding,
            import_line=imported.line,
            call_line=call.line,
            resolution=call.resolution,
        )
        key = (
            evidence.target_source,
            evidence.target_symbol,
            evidence.binding,
            evidence.import_line,
            evidence.call_line,
            evidence.resolution,
        )
        values.setdefault(key, evidence)
    return tuple(
        sorted(
            values.values(),
            key=lambda item: (
                item.call_line if item.call_line is not None else 0,
                item.target_source,
                item.target_symbol or "",
                item.binding,
            ),
        )
    )


def _test_target_evidence(value: object) -> tuple[TestTargetEvidence, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        TestTargetEvidence.from_mapping(item)
        for item in value
        if isinstance(item, Mapping)
    )


def _call_evidence(value: object) -> tuple[CallEvidence, ...]:
    if isinstance(value, str):
        return (CallEvidence.legacy(value),)
    if not isinstance(value, (list, tuple)):
        return ()
    calls: list[CallEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            calls.append(CallEvidence.from_mapping(item))
        elif isinstance(item, str):
            calls.append(CallEvidence.legacy(item))
    return tuple(calls)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _attribute_string(value: object, name: str) -> str | None:
    return _optional_string(getattr(value, name, None))


def _attribute_strings(value: object, name: str) -> tuple[str, ...]:
    return _strings(getattr(value, name, ()))


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value)
    return rendered if rendered else None


def _strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(str(item) for item in value)


def _ordered_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _integer(value: object, *, default: int) -> int:
    try:
        return int(cast(int | float | str, value))
    except (TypeError, ValueError):
        return default

# Preserve historical class paths used by serialized Python consumers.
SymbolEvidence.__module__ = "fetchcore.repository.evidence"
StructureEvidence.__module__ = "fetchcore.repository.evidence"
SectionEvidence.__module__ = "fetchcore.repository.evidence"
BehaviorEvidence.__module__ = "fetchcore.repository.evidence"
TestTargetEvidence.__module__ = "fetchcore.repository.evidence"
RelationEvidence.__module__ = "fetchcore.repository.evidence"
LexicalEvidence.__module__ = "fetchcore.repository.evidence"
KnowledgeEvidence.__module__ = "fetchcore.repository.evidence"
_SymbolObservation.__module__ = "fetchcore.repository.evidence"
_RawStructureObservation.__module__ = "fetchcore.repository.evidence"
_StructureObservation.__module__ = "fetchcore.repository.evidence"
_EnrichedStructureObservation.__module__ = "fetchcore.repository.evidence"
_ExtractStructure.__module__ = "fetchcore.repository.evidence"
