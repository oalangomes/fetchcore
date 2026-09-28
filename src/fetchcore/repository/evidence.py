"""Typed observable evidence carried by canonical repository knowledge documents.

The indexer records only facts directly observed from source structure or from the
chosen structural chunk. Framework and manifest semantics are promoted only when
dedicated, fail-closed extractors can prove them from syntax, supporting imports,
or explicit file-system conventions.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Protocol, cast

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
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SymbolEvidence:
        """Hydrate symbol evidence from its canonical mapping."""

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
class SectionEvidence:
    """Observed non-code structural unit selected by the chunker."""

    strategy: str
    kind: str
    name: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize non-code section evidence."""

        return {"strategy": self.strategy, "kind": self.kind, "name": self.name}

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SectionEvidence:
        """Hydrate section evidence from its canonical mapping."""

        return cls(
            strategy=str(value.get("strategy", "unknown")),
            kind=str(value.get("kind", "unknown")),
            name=_optional_string(value.get("name")),
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
        exports=structure.exports,
        symbols=structure.symbols,
        chunk_symbols=structure.chunk_symbols,
        documentation=documentation,
        manifest=manifest,
    )


def build_knowledge_evidence(
    structure: _StructureObservation | None,
    *,
    primary_symbol: object | None,
    discriminator: str,
    strategy: str,
    chunk_content: str = "",
    chunk_line_start: int = 1,
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

    section = _section_evidence(strategy, discriminator)
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