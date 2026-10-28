"""Conservative HTML source semantics for repository indexing.

HTML is treated as source, never as XML configuration. The parser promotes only
syntax directly observable in markup: stable DOM containers, static resource
references, navigation/form targets, and simple calls written in inline event
attributes. Embedded script/style payloads remain opaque here and are not
reinterpreted as standalone source files.
"""

from __future__ import annotations

import re
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import PurePosixPath

from fetchcore.repository.calls import CallEvidence
from fetchcore.knowledge.evidence import _StructureObservation
from fetchcore.repository.imports import ImportEvidence
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    SymbolExtractor,
    SymbolSpan,
    _Chunk,
)
from fetchcore.repository.lexical import LexicalChunkProjection, mask_c_like_non_code
from fetchcore.repository.references import ReferenceEvidence
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.extractors.languages.shell import ShellRepositoryIndexer
from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan


_VOID_ELEMENTS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)
_CHUNKABLE_TAGS = frozenset(
    {"article", "aside", "dialog", "div", "form", "main", "nav", "section", "template"}
)
_RESOURCE_LINK_RELS = frozenset({"manifest", "modulepreload", "stylesheet"})
_DYNAMIC_TARGET_MARKERS = ("{{", "}}", "{%", "%}", "${", "<%", "%>", "#{")
_EVENT_CALL = re.compile(
    r"(?<![A-Za-z0-9_$])(?P<callee>[A-Za-z_$][A-Za-z0-9_$]*"
    r"(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*)\s*\("
)
_EVENT_CONTROL_NAMES = frozenset(
    {"catch", "for", "function", "if", "switch", "while", "with"}
)


class HtmlStructuralExtractor:
    """Extract non-overlapping, stable HTML container symbols."""

    indexing_contract_version = "1"
    structural_capabilities = ("declarations", "imports")

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        return tuple(self.extract_structure(content).chunk_symbols)

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        module = _module_from_path(path)
        parser = _HtmlObservationParser(module=module)
        parser.feed(content)
        parser.close()
        symbols = tuple(sorted(parser.symbols, key=_symbol_order))
        return SourceStructure(
            language="html",
            module=module,
            imports=tuple(dict.fromkeys(item.source for item in parser.imports)),
            symbols=symbols,
            chunk_symbols=_non_overlapping_chunk_symbols(symbols),
        )


class HtmlRepositoryIndexer(ShellRepositoryIndexer):
    """Repository indexer carrying both Shell and HTML typed source semantics."""

    def _document(
        self,
        source: SafeSource,
        chunk: _Chunk,
        *,
        repository: str,
        branch: str | None,
        revision: str,
        source_ref: str,
        language: str | None,
        knowledge_type: str,
        indexed_at: str,
        extractor: SymbolExtractor | None,
        structure: _StructureObservation | None,
        lexical: LexicalChunkProjection | None,
    ) -> KnowledgeDocument:
        document = super()._document(
            source,
            chunk,
            repository=repository,
            branch=branch,
            revision=revision,
            source_ref=source_ref,
            language=language,
            knowledge_type=knowledge_type,
            indexed_at=indexed_at,
            extractor=extractor,
            structure=structure,
            lexical=lexical,
        )
        if language != "html" or not isinstance(extractor, HtmlStructuralExtractor):
            return document

        module = document.evidence.structure.module or _module_from_path(document.path)
        full = _HtmlObservationParser(module=module)
        full.feed(source.content)
        full.close()

        imports = tuple(full.imports)
        dependencies = tuple(dict.fromkeys(item.source for item in imports))
        references = tuple(
            item
            for item in full.references
            if item.line is not None and chunk.line_start <= item.line <= chunk.line_end
        )
        calls = tuple(
            item
            for item in full.calls
            if item.line is not None and chunk.line_start <= item.line <= chunk.line_end
        )
        evidence = replace(
            document.evidence,
            structure=replace(document.evidence.structure, imports=imports),
            relations=replace(
                document.evidence.relations,
                dependencies=dependencies,
                references=references,
                calls=calls,
            ),
        )
        return replace(document, evidence=evidence)


class _HtmlObservationParser(HTMLParser):
    def __init__(self, *, module: str | None) -> None:
        super().__init__(convert_charrefs=True)
        self.module = module
        self.symbols: list[StructuralSymbolSpan] = []
        self.imports: list[ImportEvidence] = []
        self.references: list[ReferenceEvidence] = []
        self.calls: list[CallEvidence] = []
        self._open_tags: list[tuple[str, StructuralSymbolSpan | None]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._observe_start(tag, attrs, self_closing=tag.casefold() in _VOID_ELEMENTS)

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self._observe_start(tag, attrs, self_closing=True)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if not self._open_tags or self._open_tags[-1][0] != normalized:
            return
        _, opened = self._open_tags.pop()
        if opened is None:
            return
        line = self.getpos()[0]
        self.symbols.append(replace(opened, line_end=max(opened.line_start, line)))

    def _observe_start(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
        *,
        self_closing: bool,
    ) -> None:
        normalized = tag.casefold()
        values = _attributes(attrs)
        line = self.getpos()[0]
        symbol = _html_symbol(
            normalized,
            values,
            module=self.module,
            line=line,
        )
        element_id = values.get("id")
        owner = (
            _element_owner(self.module, element_id)
            if element_id
            else symbol.qualified_name
            if symbol is not None and symbol.qualified_name is not None
            else self._nearest_container_owner()
        )

        resource = _resource_import(normalized, values, line=line)
        if resource is not None:
            self.imports.append(resource)

        reference = _html_reference(normalized, values, source_symbol=owner, line=line)
        if reference is not None:
            self.references.append(reference)

        for event, handler in values.items():
            if not event.startswith("on") or len(event) <= 2 or not handler:
                continue
            self.calls.extend(
                _event_calls(
                    handler,
                    event=event[2:],
                    source_symbol=owner,
                    line=line,
                )
            )

        if self_closing:
            if symbol is not None:
                self.symbols.append(symbol)
            return
        self._open_tags.append((normalized, symbol))

    def _nearest_container_owner(self) -> str | None:
        for _, symbol in reversed(self._open_tags):
            if symbol is not None and symbol.qualified_name is not None:
                return symbol.qualified_name
        return self.module


def _html_symbol(
    tag: str,
    attrs: dict[str, str],
    *,
    module: str | None,
    line: int,
) -> StructuralSymbolSpan | None:
    element_id = attrs.get("id")
    custom = "-" in tag
    if tag not in _CHUNKABLE_TAGS and not custom:
        return None
    if not element_id and not custom and tag != "form":
        return None
    name = element_id or attrs.get("name") or tag
    kind = "html-form" if tag == "form" else "html-custom-element" if custom else "html-element"
    qualified_name = (
        f"{module}#{name}"
        if module and element_id
        else f"#{name}"
        if element_id
        else f"{module}.{name}"
        if module
        else name
    )
    return StructuralSymbolSpan(
        name=name,
        symbol_type=kind,
        line_start=line,
        line_end=line,
        qualified_name=qualified_name,
        visibility="document",
        signature=_tag_signature(tag, attrs),
    )


def _resource_import(
    tag: str,
    attrs: dict[str, str],
    *,
    line: int,
) -> ImportEvidence | None:
    if tag == "script":
        target = _static_target(attrs.get("src"))
        if target is None:
            return None
        kind = "html-module-script" if attrs.get("type", "").casefold() == "module" else "html-script"
        return ImportEvidence(name=target, source=target, kind=kind, line=line)
    if tag != "link":
        return None
    target = _static_target(attrs.get("href"))
    if target is None:
        return None
    rels = frozenset(_tokens(attrs.get("rel")))
    selected = next((rel for rel in ("stylesheet", "manifest", "modulepreload") if rel in rels), None)
    if selected is None or selected not in _RESOURCE_LINK_RELS:
        return None
    return ImportEvidence(name=target, source=target, kind=f"html-{selected}", line=line)


def _html_reference(
    tag: str,
    attrs: dict[str, str],
    *,
    source_symbol: str | None,
    line: int,
) -> ReferenceEvidence | None:
    if tag == "a":
        target = _static_target(attrs.get("href"))
        if target is None or target.casefold().startswith("javascript:"):
            return None
        return ReferenceEvidence(
            source_symbol=source_symbol,
            name="href",
            target=target,
            source=None,
            kind="html-navigation",
            line=line,
            resolution="syntax",
        )
    if tag == "form":
        target = _static_target(attrs.get("action"))
        if target is None:
            return None
        method = attrs.get("method", "unspecified").casefold() or "unspecified"
        return ReferenceEvidence(
            source_symbol=source_symbol,
            name="action",
            target=target,
            source=None,
            kind=f"html-form-action:{method}",
            line=line,
            resolution="syntax",
        )
    return None


def _event_calls(
    handler: str,
    *,
    event: str,
    source_symbol: str | None,
    line: int,
) -> tuple[CallEvidence, ...]:
    masked = mask_c_like_non_code(handler)
    values: list[CallEvidence] = []
    for match in _EVENT_CALL.finditer(masked):
        callee = match.group("callee")
        if callee in _EVENT_CONTROL_NAMES:
            continue
        values.append(
            CallEvidence(
                source_symbol=source_symbol,
                callee=callee,
                target=callee,
                source=None,
                kind=f"html_event:{event.casefold()}",
                line=line,
                resolution="syntax",
            )
        )
    return tuple(values)


def _non_overlapping_chunk_symbols(
    symbols: tuple[StructuralSymbolSpan, ...],
) -> tuple[StructuralSymbolSpan, ...]:
    selected: list[StructuralSymbolSpan] = []
    cursor = 0
    for symbol in sorted(symbols, key=lambda item: (item.line_start, -item.line_end, item.name)):
        if symbol.line_start <= cursor:
            continue
        selected.append(symbol)
        cursor = symbol.line_end
    return tuple(selected)


def _attributes(values: list[tuple[str, str | None]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name, value in values:
        key = name.casefold()
        if key not in result:
            result[key] = value or ""
    return result


def _static_target(value: str | None) -> str | None:
    if value is None:
        return None
    target = value.strip()
    if not target or any(marker in target for marker in _DYNAMIC_TARGET_MARKERS):
        return None
    return target


def _tokens(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(item.casefold() for item in value.split() if item)


def _element_owner(module: str | None, element_id: str | None) -> str | None:
    if element_id:
        return f"{module}#{element_id}" if module else f"#{element_id}"
    return module


def _tag_signature(tag: str, attrs: dict[str, str]) -> str:
    selected = []
    for name in ("id", "name", "role", "method", "action"):
        value = attrs.get(name)
        if value:
            selected.append(f'{name}="{value}"')
    suffix = (" " + " ".join(selected)) if selected else ""
    return f"<{tag}{suffix}>"


def _module_from_path(path: str | None) -> str | None:
    if not path:
        return None
    pure = PurePosixPath(path)
    without_suffix = pure.with_suffix("")
    parts = tuple(part for part in without_suffix.parts if part not in {".", "src"})
    return ".".join(parts) or None


def _symbol_order(item: StructuralSymbolSpan) -> tuple[int, int, str]:
    return item.line_start, item.line_end, item.name


# Persisted indexing fingerprints include these historical class paths.
HtmlStructuralExtractor.__module__ = "fetchcore.repository.html"
HtmlRepositoryIndexer.__module__ = "fetchcore.repository.html"
