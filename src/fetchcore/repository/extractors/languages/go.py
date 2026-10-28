"""Observed Go declarations and imports for canonical repository knowledge."""

from __future__ import annotations

import json
import re

from fetchcore.repository.lexical import mask_go_non_code
from fetchcore.repository.structure import (
    SourceStructure,
    StructuralSymbolSpan,
    _brace_depths,
    _line_number,
    _line_signature,
    _matching_brace,
    _ordered_symbols,
    _qualified,
)


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_TYPE = re.compile(
    rf"(?m)^[ \t]*type\s+(?P<name>{_IDENTIFIER})"
    rf"(?:\s*\[[^\]\n]*\])?\s+(?P<kind>struct|interface)\b"
)
_TYPE_ALIAS = re.compile(
    rf"(?m)^[ \t]*type\s+(?P<name>{_IDENTIFIER})"
    rf"(?:\s*\[[^\]\n]*\])?\s+(?P<rest>[^\n]+)"
)
_FUNCTION = re.compile(
    rf"(?m)^[ \t]*func\s+(?:\((?P<receiver>[^)\n]+)\)\s*)?"
    rf"(?P<name>{_IDENTIFIER})\s*\("
)
_RECEIVER_TYPE = re.compile(rf"\*?\s*(?P<name>{_IDENTIFIER})")
_SINGLE_IMPORT = re.compile(
    r'(?m)^[ \t]*import[ \t]+(?:[A-Za-z_][A-Za-z0-9_]*[ \t]+)?'
    r'(?P<path>"(?:\\.|[^"])*"|`[^`]*`)'
)
_IMPORT_BLOCK = re.compile(r"(?ms)^[ \t]*import[ \t]*\((?P<body>.*?)\)")
_IMPORT_LINE = re.compile(
    r'^[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*[ \t]+)?'
    r'(?P<path>"(?:\\.|[^"])*"|`[^`]*`)[ \t]*$'
)


class GoStructuralExtractor:
    """Extract declarations and imports visible in Go syntax, without resolution."""

    indexing_contract_version = "2"
    structural_capabilities = ("declarations", "imports")

    def extract(self, content: str) -> tuple[StructuralSymbolSpan, ...]:
        """Return non-overlapping top-level Go declarations for source chunking."""

        return self.extract_structure(content).chunk_symbols

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        """Extract package, imports, types, functions, and methods observed in source."""

        del path
        masked = mask_go_non_code(content)
        depths = _brace_depths(masked)
        package_match = re.search(
            rf"(?m)^[ \t]*package[ \t]+(?P<name>{_IDENTIFIER})\b",
            masked,
        )
        package = package_match.group("name") if package_match else None
        symbols: list[StructuralSymbolSpan] = []
        chunks: list[StructuralSymbolSpan] = []
        type_names: set[str] = set()

        for match in _TYPE.finditer(masked):
            if depths[match.start()] != 0:
                continue
            name = match.group("name")
            opening = masked.find("{", match.end())
            closing = _matching_brace(masked, opening)
            if opening < 0 or closing is None:
                continue
            symbol = StructuralSymbolSpan(
                name=name,
                symbol_type=match.group("kind"),
                line_start=_line_number(content, match.start()),
                line_end=_line_number(content, closing),
                qualified_name=_qualified(package, name),
                signature=_line_signature(content, match.start(), opening + 1),
            )
            symbols.append(symbol)
            chunks.append(symbol)
            type_names.add(name)

        for match in _TYPE_ALIAS.finditer(masked):
            if depths[match.start()] != 0:
                continue
            name = match.group("name")
            first = match.group("rest").lstrip().split(maxsplit=1)[0].split("=", 1)[-1]
            if name in type_names or first in {"struct", "interface"}:
                continue
            line_end = content.find("\n", match.end())
            end_index = len(content) if line_end < 0 else line_end
            symbols.append(
                StructuralSymbolSpan(
                    name=name,
                    symbol_type="type",
                    line_start=_line_number(content, match.start()),
                    line_end=_line_number(content, max(match.start(), end_index - 1)),
                    qualified_name=_qualified(package, name),
                    signature=_line_signature(content, match.start(), match.end()),
                )
            )
            chunks.append(symbols[-1])

        for match in _FUNCTION.finditer(masked):
            if depths[match.start()] != 0:
                continue
            opening = masked.rfind("(", match.start(), match.end())
            closing = _matching_parenthesis(masked, opening)
            if closing is None:
                continue
            body_open = _function_body_open(masked, closing + 1)
            if body_open is None:
                continue
            body_close = _matching_brace(masked, body_open)
            if body_close is None:
                continue

            name = match.group("name")
            receiver = match.group("receiver")
            container = _receiver_type(receiver) if receiver else None
            symbol = StructuralSymbolSpan(
                name=name,
                symbol_type="method" if container else "function",
                line_start=_line_number(content, match.start()),
                line_end=_line_number(content, body_close),
                qualified_name=_qualified(
                    _qualified(package, container) if container else package,
                    name,
                ),
                container=container,
                signature=_line_signature(content, match.start(), body_open + 1),
            )
            symbols.append(symbol)
            chunks.append(symbol)

        return SourceStructure(
            language="go",
            package=package,
            imports=_go_imports(content),
            symbols=_ordered_symbols(symbols),
            chunk_symbols=_ordered_symbols(chunks),
        )


def _matching_parenthesis(masked: str, opening: int) -> int | None:
    if opening < 0 or opening >= len(masked) or masked[opening] != "(":
        return None
    depth = 0
    for index in range(opening, len(masked)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def _function_body_open(masked: str, start: int) -> int | None:
    round_depth = 0
    square_depth = 0
    angle_depth = 0
    for index in range(start, len(masked)):
        char = masked[index]
        if char == "(":
            round_depth += 1
        elif char == ")" and round_depth:
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]" and square_depth:
            square_depth -= 1
        elif char == "<":
            angle_depth += 1
        elif char == ">" and angle_depth:
            angle_depth -= 1
        elif char == "{" and not any((round_depth, square_depth, angle_depth)):
            return index
        elif char == ";" and not any((round_depth, square_depth, angle_depth)):
            return None
    return None


def _receiver_type(receiver: str | None) -> str | None:
    if not receiver:
        return None
    parts = receiver.strip().split()
    type_name = parts[-1] if len(parts) > 1 else parts[0]
    receiver_match = _RECEIVER_TYPE.match(type_name)
    return receiver_match.group("name") if receiver_match else None


def _go_imports(content: str) -> tuple[str, ...]:
    imports: set[str] = set()
    masked = mask_go_non_code(content)
    for single_match in _SINGLE_IMPORT.finditer(content):
        if masked[single_match.start():single_match.end()].lstrip().startswith("import"):
            imports.add(_import_path(single_match.group("path")))
    for block_match in _IMPORT_BLOCK.finditer(content):
        if not masked[block_match.start():block_match.end()].lstrip().startswith("import"):
            continue
        for line in block_match.group("body").splitlines():
            line_match = _IMPORT_LINE.fullmatch(line)
            if line_match:
                imports.add(_import_path(line_match.group("path")))
    return tuple(sorted(imports))


def _import_path(value: str) -> str:
    if value.startswith("`"):
        return value[1:-1]
    return str(json.loads(value))
