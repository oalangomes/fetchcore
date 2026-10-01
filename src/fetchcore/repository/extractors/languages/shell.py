"""Conservative structural extraction for POSIX/Bash-style shell scripts."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import PurePosixPath

from fetchcore.repository.calls import CallEvidence
from fetchcore.knowledge.evidence import _StructureObservation
from fetchcore.repository.imports import ImportEvidence
from fetchcore.repository.indexing import (
    KnowledgeDocument,
    RepositoryIndexer,
    SymbolExtractor,
    SymbolSpan,
    _Chunk,
)
from fetchcore.repository.lexical import LexicalChunkProjection
from fetchcore.repository.safe_scan import SafeSource
from fetchcore.repository.shell_lexical import (
    analyze_shell_non_code,
    extract_shell_commands,
    extract_shell_static_sources,
)
from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan


_FUNCTION_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
_FUNCTION_BRACE = r"[ \t]*(?:\r?\n[ \t]*)?\{"
_POSIX_FUNCTION = re.compile(
    rf"(?m)^[ \t]*(?P<name>{_FUNCTION_NAME})[ \t]*\([ \t]*\){_FUNCTION_BRACE}"
)
_BASH_FUNCTION = re.compile(
    rf"(?m)^[ \t]*function[ \t]+(?P<name>{_FUNCTION_NAME})"
    rf"(?:[ \t]*\([ \t]*\))?{_FUNCTION_BRACE}"
)
_ASSIGNMENT = re.compile(
    rf"(?m)^[ \t]*(?P<prefix>(?:(?:export|readonly)[ \t]+|"
    rf"(?:declare|typeset)[ \t]+-[A-Za-z]*x[A-Za-z]*[ \t]+)?)"
    rf"(?P<name>{_FUNCTION_NAME})[ \t]*="
)
_EXPORT = re.compile(
    rf"(?m)^[ \t]*export(?:[ \t]+-[A-Za-z]+)?[ \t]+(?P<name>{_FUNCTION_NAME})\b"
)
_DECLARE_EXPORT = re.compile(
    rf"(?m)^[ \t]*(?:declare|typeset)[ \t]+-[A-Za-z]*x[A-Za-z]*[ \t]+"
    rf"(?P<name>{_FUNCTION_NAME})\b"
)


class ShellStructuralExtractor:
    """Extract statically provable shell functions, assignments, includes and exports."""

    indexing_contract_version = "1"

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        return tuple(self.extract_structure(content).chunk_symbols)

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        module = _module_from_path(path)
        masked, _ = analyze_shell_non_code(content)
        functions = _functions(content, masked, module=module)
        function_ranges = tuple((item.line_start, item.line_end) for item in functions)
        assignments = _assignments(
            content,
            masked,
            module=module,
            excluded_ranges=function_ranges,
        )
        exports = _exports(masked)
        imports = tuple(source for source, _ in extract_shell_static_sources(content))
        symbols = tuple(sorted((*functions, *assignments), key=_symbol_order))
        chunks = tuple(sorted((*functions, *assignments), key=_symbol_order))
        return SourceStructure(
            language="shell",
            module=module,
            imports=tuple(dict.fromkeys(imports)),
            exports=exports,
            symbols=symbols,
            chunk_symbols=chunks,
        )


class ShellRepositoryIndexer(RepositoryIndexer):
    """Canonical indexer variant that projects shell-specific typed relations."""

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
        if language != "shell" or not isinstance(extractor, ShellStructuralExtractor):
            return document

        typed_imports = tuple(
            ImportEvidence(
                name=source_path,
                source=source_path,
                kind="source",
                line=line,
            )
            for source_path, line in extract_shell_static_sources(source.content)
        )
        symbol = document.evidence.symbol
        owner = (
            (symbol.qualified_name or symbol.name)
            if symbol is not None
            else document.evidence.structure.module or document.path
        )
        calls = tuple(
            CallEvidence(
                source_symbol=owner,
                callee=command,
                target=command,
                source=None,
                kind="shell_command",
                line=line,
                resolution="syntax",
            )
            for command, line in extract_shell_commands(
                chunk.content,
                line_start=chunk.line_start,
            )
        )
        evidence = replace(
            document.evidence,
            structure=replace(document.evidence.structure, imports=typed_imports),
            relations=replace(document.evidence.relations, calls=calls),
        )
        return replace(document, evidence=evidence)


def _functions(
    content: str,
    masked: str,
    *,
    module: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    candidates = sorted(
        (*_POSIX_FUNCTION.finditer(masked), *_BASH_FUNCTION.finditer(masked)),
        key=lambda match: match.start(),
    )
    values: list[StructuralSymbolSpan] = []
    occupied: list[tuple[int, int]] = []
    for match in candidates:
        if any(start <= match.start() < end for start, end in occupied):
            continue
        brace = masked.rfind("{", match.start(), match.end())
        if brace < 0:
            continue
        closing = _matching_brace(masked, brace)
        if closing is None:
            continue
        occupied.append((match.start(), closing + 1))
        name = match.group("name")
        line_start = _line_number(content, match.start())
        line_end = _line_number(content, closing)
        values.append(
            StructuralSymbolSpan(
                name=name,
                symbol_type="function",
                line_start=line_start,
                line_end=line_end,
                qualified_name=_qualified(module, name),
                visibility="script",
                signature=f"{name}()",
            )
        )
    return tuple(values)


def _assignments(
    content: str,
    masked: str,
    *,
    module: str | None,
    excluded_ranges: tuple[tuple[int, int], ...],
) -> tuple[StructuralSymbolSpan, ...]:
    values: list[StructuralSymbolSpan] = []
    for match in _ASSIGNMENT.finditer(masked):
        line = _line_number(content, match.start())
        if any(start <= line <= end for start, end in excluded_ranges):
            continue
        prefix = match.group("prefix").strip()
        modifiers: list[str] = []
        if prefix:
            command = prefix.split()[0]
            modifiers.append(command)
            if command in {"export", "declare", "typeset"}:
                modifiers.append("export")
        name = match.group("name")
        values.append(
            StructuralSymbolSpan(
                name=name,
                symbol_type="variable",
                line_start=line,
                line_end=line,
                qualified_name=_qualified(module, name),
                visibility="exported" if "export" in modifiers else "script",
                signature=(
                    f"export {name}=..." if "export" in modifiers else f"{name}=..."
                ),
                modifiers=tuple(dict.fromkeys(modifiers)),
            )
        )
    return tuple(values)


def _exports(masked: str) -> tuple[str, ...]:
    names = [match.group("name") for match in _EXPORT.finditer(masked)]
    names.extend(match.group("name") for match in _DECLARE_EXPORT.finditer(masked))
    return tuple(dict.fromkeys(names))


def _matching_brace(masked: str, opening: int) -> int | None:
    depth = 0
    for index in range(opening, len(masked)):
        char = masked[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _module_from_path(path: str | None) -> str | None:
    if not path:
        return None
    pure = PurePosixPath(path)
    without_suffix = pure.with_suffix("")
    parts = tuple(part for part in without_suffix.parts if part not in {".", "src"})
    return ".".join(parts) or None


def _qualified(parent: str | None, name: str) -> str:
    return f"{parent}.{name}" if parent else name


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, index) + 1


def _symbol_order(item: StructuralSymbolSpan) -> tuple[int, int, str]:
    return item.line_start, item.line_end, item.name


# Persisted indexing fingerprints include these historical class paths.
ShellStructuralExtractor.__module__ = "fetchcore.repository.shell"
ShellRepositoryIndexer.__module__ = "fetchcore.repository.shell"
