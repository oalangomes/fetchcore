"""Deterministic, backend-neutral structural extraction for Groovy source.

The baseline intentionally targets syntax that is directly observable in the
repository corpus: package/import declarations, top-level script methods,
classes/traits/interfaces/enums, direct class methods, and simple top-level
variables. Closures remain nested behavior and are not promoted to symbols.
"""

from __future__ import annotations

import re

from fetchcore.repository.indexing import SymbolSpan
from fetchcore.repository.lexical import analyze_lexical_source
from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan


_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_QUALIFIED = rf"{_IDENTIFIER}(?:\.{_IDENTIFIER})*"
_RESERVED_RETURN_WORDS = frozenset(
    {"return", "throw", "new", "if", "for", "while", "switch", "catch", "try", "else"}
)
_GROOVY_DECLARATION_START = re.compile(
    rf"(?:(?:public|protected|private|abstract|static|final|synchronized)\s+)*"
    rf"(?:class|interface|enum|trait|def|package|import)\b|@|"
    rf"(?:{_IDENTIFIER}(?:[.$<>\[\],? ]*{_IDENTIFIER})?)\s+{_IDENTIFIER}\s*\("
)


class GroovyStructuralExtractor:
    """Extract conservative Groovy script/type structure without type inference."""

    indexing_contract_version = "1"

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        return tuple(self.extract_structure(content).chunk_symbols)

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        del path
        lexical = analyze_lexical_source("groovy", content)
        if lexical is None:
            raise ValueError("lexical analysis unavailable for Groovy")
        masked = lexical.masked_content
        depths = _brace_depths(masked)
        package = _package(masked)
        imports = _imports(masked)

        symbols: list[StructuralSymbolSpan] = []
        chunks: list[StructuralSymbolSpan] = []

        for symbol, body_open, body_end in _top_level_types(
            content,
            masked,
            depths,
            package=package,
        ):
            symbols.append(symbol)
            chunks.append(symbol)
            symbols.extend(
                _methods(
                    content,
                    masked,
                    depths,
                    target_depth=depths[body_open] + 1,
                    start=body_open + 1,
                    end=body_end,
                    package=package,
                    container=symbol.name,
                )
            )

        top_level_methods = _methods(
            content,
            masked,
            depths,
            target_depth=0,
            start=0,
            end=len(masked),
            package=package,
            container=None,
        )
        symbols.extend(top_level_methods)
        chunks.extend(top_level_methods)

        top_level_variables = _top_level_variables(
            content,
            masked,
            depths,
            package=package,
        )
        symbols.extend(top_level_variables)
        chunks.extend(top_level_variables)

        return SourceStructure(
            language="groovy",
            package=package,
            imports=imports,
            symbols=_ordered(symbols),
            chunk_symbols=_ordered(chunks),
        )


def _package(masked: str) -> str | None:
    match = re.search(rf"(?m)^\s*package\s+({_QUALIFIED})\s*;?\s*$", masked)
    return match.group(1) if match else None


def _imports(masked: str) -> tuple[str, ...]:
    values: set[str] = set()
    pattern = re.compile(
        rf"(?m)^\s*import\s+(?:static\s+)?(?P<target>{_QUALIFIED}(?:\.\*)?)"
        rf"(?:\s+as\s+{_IDENTIFIER})?\s*;?\s*$"
    )
    for match in pattern.finditer(masked):
        values.add(match.group("target"))
    return tuple(sorted(values))


def _top_level_types(
    content: str,
    masked: str,
    depths: list[int],
    *,
    package: str | None,
) -> tuple[tuple[StructuralSymbolSpan, int, int], ...]:
    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private|abstract|static|final)\s+)*)"
        rf"(?P<kind>class|interface|enum|trait)\s+(?P<name>{_IDENTIFIER})"
    )
    values: list[tuple[StructuralSymbolSpan, int, int]] = []
    for match in pattern.finditer(masked):
        if depths[match.start()] != 0:
            continue
        body_open = _type_body_open(masked, depths, match.end())
        if body_open is None:
            continue
        body_end = _matching_brace(masked, body_open)
        if body_end is None:
            continue
        modifiers = tuple(match.group("prefix").split())
        start_line = _line_number(content, match.start())
        symbol = StructuralSymbolSpan(
            name=match.group("name"),
            symbol_type=match.group("kind"),
            line_start=_decorated_line_start(content, start_line),
            line_end=_line_number(content, body_end),
            qualified_name=_qualified(package, match.group("name")),
            visibility=_visibility(modifiers, default="public"),
            signature=_line_signature(content, match.start(), body_open + 1),
            modifiers=modifiers,
            annotations=_annotations_before(content, start_line),
        )
        values.append((symbol, body_open, body_end))
    return tuple(values)


def _type_body_open(masked: str, depths: list[int], start: int) -> int | None:
    """Find a type body without consuming a later top-level declaration."""

    line_breaks = 0
    index = start
    while index < len(masked):
        char = masked[index]
        if depths[index] != 0:
            return None
        if char == "{":
            return index
        if char == ";":
            return None
        if char == "\n":
            line_breaks += 1
            if line_breaks > 64:
                return None
            next_end = masked.find("\n", index + 1)
            if next_end < 0:
                next_end = len(masked)
            next_line = masked[index + 1 : next_end].lstrip()
            if next_line and _GROOVY_DECLARATION_START.match(next_line):
                return None
        index += 1
    return None


def _methods(
    content: str,
    masked: str,
    depths: list[int],
    *,
    target_depth: int,
    start: int,
    end: int,
    package: str | None,
    container: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private|static|final|abstract|synchronized)\s+)*)"
        rf"(?:(?P<def>def)\s+|(?P<return>{_IDENTIFIER}(?:[.$<>\[\],? ]*{_IDENTIFIER})?)\s+)"
        rf"(?P<name>{_IDENTIFIER})\s*\((?P<params>[^)]*)\)\s*"
        rf"(?:throws\s+[^{{;\n]+)?(?P<end>[{{;])"
    )
    values: list[StructuralSymbolSpan] = []
    if container is not None:
        constructor_pattern = re.compile(
            rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private)\s+)*)"
            rf"{re.escape(container)}\s*\((?P<params>[^)]*)\)\s*"
            rf"(?:throws\s+[^{{;\n]+)?(?P<end>[{{;])"
        )
        for constructor in constructor_pattern.finditer(masked, start, end):
            if depths[constructor.start()] != target_depth:
                continue
            if constructor.group("end") == "{":
                open_index = masked.find("{", constructor.start(), constructor.end())
                matched_end = (
                    _matching_brace(masked, open_index) if open_index >= 0 else None
                )
                if matched_end is None or matched_end > end:
                    continue
                constructor_end = matched_end + 1
            else:
                constructor_end = constructor.end()
            modifiers = tuple(constructor.group("prefix").split())
            start_line = _line_number(content, constructor.start())
            parent = _qualified(package, container)
            values.append(
                StructuralSymbolSpan(
                    name=container,
                    symbol_type="constructor",
                    line_start=_decorated_line_start(content, start_line),
                    line_end=_line_number(
                        content,
                        max(constructor.start(), constructor_end - 1),
                    ),
                    qualified_name=_qualified(parent, container),
                    visibility=_visibility(modifiers, default="public"),
                    container=container,
                    signature=_line_signature(
                        content,
                        constructor.start(),
                        constructor_end,
                    ),
                    parameters=_parameter_names(constructor.group("params")),
                    modifiers=modifiers,
                    annotations=_annotations_before(content, start_line),
                )
            )
    for match in pattern.finditer(masked, start, end):
        if depths[match.start()] != target_depth:
            continue
        observed_return = (match.group("return") or "").strip()
        if observed_return in _RESERVED_RETURN_WORDS:
            continue
        end_token = match.group("end")
        if end_token == "{":
            open_index = masked.find("{", match.start(), match.end())
            matched_end = _matching_brace(masked, open_index) if open_index >= 0 else None
            if matched_end is None or matched_end > end:
                continue
            end_index = matched_end + 1
        else:
            end_index = match.end()
        modifiers = tuple(match.group("prefix").split())
        start_line = _line_number(content, match.start())
        parent = _qualified(package, container) if container else package
        values.append(
            StructuralSymbolSpan(
                name=match.group("name"),
                symbol_type="method" if container else "function",
                line_start=_decorated_line_start(content, start_line),
                line_end=_line_number(content, max(match.start(), end_index - 1)),
                qualified_name=_qualified(parent, match.group("name")),
                visibility=_visibility(modifiers, default="public" if container else "module"),
                container=container,
                signature=_line_signature(content, match.start(), end_index),
                parameters=_parameter_names(match.group("params")),
                modifiers=modifiers,
                annotations=_annotations_before(content, start_line),
            )
        )
    return _ordered(values)


def _top_level_variables(
    content: str,
    masked: str,
    depths: list[int],
    *,
    package: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:public|protected|private|static|final)\s+)*)"
        rf"(?:(?P<def>def)\s+|(?P<type>{_IDENTIFIER}(?:[.$<>\[\],? ]*{_IDENTIFIER})?)\s+)"
        rf"(?P<name>{_IDENTIFIER})\s*=\s*(?P<initializer>[^\n;{{}}]+)\s*;?[ \t]*(?:\n|$)"
    )
    values: list[StructuralSymbolSpan] = []
    for match in pattern.finditer(masked):
        if depths[match.start()] != 0:
            continue
        initializer = match.group("initializer")
        if "->" in initializer:
            continue
        modifiers = tuple(match.group("prefix").split())
        start_line = _line_number(content, match.start())
        values.append(
            StructuralSymbolSpan(
                name=match.group("name"),
                symbol_type="constant" if "final" in modifiers else "variable",
                line_start=_decorated_line_start(content, start_line),
                line_end=start_line,
                qualified_name=_qualified(package, match.group("name")),
                visibility=_visibility(modifiers, default="module"),
                signature=_line_signature(content, match.start(), match.end()),
                modifiers=modifiers,
                annotations=_annotations_before(content, start_line),
            )
        )
    return _ordered(values)


def _parameter_names(raw: str) -> tuple[str, ...]:
    values: list[str] = []
    for part in _split_parameters(raw):
        token = part.split("=", 1)[0].strip()
        token = re.sub(r"^@[A-Za-z0-9_$.]+(?:\([^)]*\))?\s*", "", token)
        token = re.sub(r"\b(?:final|def)\b\s*", "", token).strip()
        if not token:
            continue
        identifiers = re.findall(_IDENTIFIER, token)
        if identifiers:
            values.append(identifiers[-1])
    return tuple(values)


def _split_parameters(raw: str) -> tuple[str, ...]:
    values: list[str] = []
    current: list[str] = []
    depth = 0
    for char in raw:
        if char in "<([{":
            depth += 1
        elif char in ">)]}" and depth:
            depth -= 1
        if char == "," and depth == 0:
            values.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        values.append("".join(current))
    return tuple(values)


def _brace_depths(masked: str) -> list[int]:
    depths = [0] * (len(masked) + 1)
    depth = 0
    for index, char in enumerate(masked):
        depths[index] = depth
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    depths[len(masked)] = depth
    return depths


def _matching_brace(masked: str, open_index: int) -> int | None:
    if open_index < 0 or open_index >= len(masked) or masked[open_index] != "{":
        return None
    depth = 0
    for index in range(open_index, len(masked)):
        if masked[index] == "{":
            depth += 1
        elif masked[index] == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, max(0, index)) + 1


def _annotations_before(content: str, declaration_line: int) -> tuple[str, ...]:
    lines = content.splitlines()
    values: list[str] = []
    index = declaration_line - 2
    while index >= 0:
        stripped = lines[index].strip()
        if not stripped or not stripped.startswith("@"):
            break
        values.append(stripped.removeprefix("@").strip())
        index -= 1
    return tuple(reversed(values))


def _decorated_line_start(content: str, declaration_line: int) -> int:
    return declaration_line - len(_annotations_before(content, declaration_line))


def _line_signature(content: str, start: int, end: int) -> str:
    rendered = " ".join(content[start:end].strip().split())
    if "{" in rendered:
        rendered = rendered.split("{", 1)[0].strip()
    return rendered.rstrip(";")


def _qualified(parent: str | None, name: str | None) -> str | None:
    if not name:
        return parent
    return f"{parent}.{name}" if parent else name


def _visibility(modifiers: tuple[str, ...], *, default: str) -> str:
    for value in ("public", "protected", "private"):
        if value in modifiers:
            return value
    return default


def _ordered(values: list[StructuralSymbolSpan]) -> tuple[StructuralSymbolSpan, ...]:
    return tuple(sorted(values, key=lambda item: (item.line_start, item.line_end)))
