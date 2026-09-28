"""Deterministic, backend-neutral structural extraction for Dart source.

The baseline targets syntax directly observed in Flutter/Dart repositories:
library imports, top-level functions, classes/mixins/enums, constructors, methods,
and getters. It deliberately avoids Flutter-specific route/widget semantics and
type inference.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from fetchcore.repository.indexing import SymbolSpan
from fetchcore.repository.lexical import analyze_lexical_source
from fetchcore.repository.structure import SourceStructure, StructuralSymbolSpan


_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_CONTROL_NAMES = frozenset(
    {"if", "for", "while", "switch", "catch", "return", "throw", "assert", "await"}
)
_TYPE_START = re.compile(
    r"(?:(?:(?:abstract|base|final|interface|sealed)\s+)*)"
    r"(?:class|mixin|enum)\b"
)
_CALLABLE_DECLARATION_START = re.compile(
    rf"(?:(?:external|static|abstract|covariant)\s+)*"
    rf"(?:(?:{_IDENTIFIER}(?:<[^{{}};\n]+>)?[?]?)\s+)?"
    rf"(?P<name>{_IDENTIFIER})\s*\("
)
_GETTER_DECLARATION_START = re.compile(
    rf"(?:(?:external|static|abstract|covariant|final)\s+)*"
    rf"(?:{_IDENTIFIER}(?:<[^{{}};\n]+>)?[?]?)\s+get\s+{_IDENTIFIER}\b"
)


class DartStructuralExtractor:
    """Extract conservative Dart structure without compiler/type inference."""

    indexing_contract_version = "1"

    def extract(self, content: str) -> tuple[SymbolSpan, ...]:
        return tuple(self.extract_structure(content).chunk_symbols)

    def extract_structure(
        self,
        content: str,
        *,
        path: str | None = None,
    ) -> SourceStructure:
        lexical = analyze_lexical_source("dart", content)
        if lexical is None:  # pragma: no cover - registry constrains language
            raise ValueError("lexical analysis unavailable for Dart")
        masked = lexical.masked_content
        depths = _brace_depths(masked)
        module = _module_from_path(path)
        imports = _imports(content, masked, depths)

        symbols: list[StructuralSymbolSpan] = []
        chunks: list[StructuralSymbolSpan] = []

        for type_symbol, body_open, body_end in _types(
            content,
            masked,
            depths,
            module=module,
        ):
            symbols.append(type_symbol)
            chunks.append(type_symbol)
            symbols.extend(
                _constructors(
                    content,
                    masked,
                    depths,
                    class_name=type_symbol.name,
                    body_open=body_open,
                    body_end=body_end,
                    module=module,
                )
            )
            symbols.extend(
                _getters(
                    content,
                    masked,
                    depths,
                    target_depth=depths[body_open] + 1,
                    start=body_open + 1,
                    end=body_end,
                    module=module,
                    container=type_symbol.name,
                )
            )
            symbols.extend(
                _callables(
                    content,
                    masked,
                    depths,
                    target_depth=depths[body_open] + 1,
                    start=body_open + 1,
                    end=body_end,
                    module=module,
                    container=type_symbol.name,
                )
            )

        top_level = _callables(
            content,
            masked,
            depths,
            target_depth=0,
            start=0,
            end=len(masked),
            module=module,
            container=None,
        )
        symbols.extend(top_level)
        chunks.extend(top_level)

        return SourceStructure(
            language="dart",
            module=module,
            imports=imports,
            symbols=_ordered(symbols),
            chunk_symbols=_ordered(chunks),
        )


def _imports(content: str, masked: str, depths: list[int]) -> tuple[str, ...]:
    values: set[str] = set()
    for match in re.finditer(r"(?m)^[ \t]*import\b", masked):
        if depths[match.start()] != 0:
            continue
        end = content.find(";", match.start())
        if end < 0:
            continue
        statement = content[match.start() : end + 1]
        observed = re.match(
            r"""^\s*import\s+(?:r|R)?(?P<quote>['"])(?P<source>.+?)(?P=quote)""",
            statement,
            re.DOTALL,
        )
        if observed is not None:
            values.add(observed.group("source"))
    return tuple(sorted(values))


def _types(
    content: str,
    masked: str,
    depths: list[int],
    *,
    module: str | None,
) -> tuple[tuple[StructuralSymbolSpan, int, int], ...]:
    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:abstract|base|final|interface|sealed)\s+)*)"
        rf"(?P<kind>class|mixin|enum)\s+(?P<name>{_IDENTIFIER})"
    )
    values: list[tuple[StructuralSymbolSpan, int, int]] = []
    for match in pattern.finditer(masked):
        if depths[match.start()] != 0:
            continue
        body_open = _body_open(masked, depths, match.end(), target_depth=0)
        if body_open is None:
            continue
        body_end = _matching_brace(masked, body_open)
        if body_end is None:
            continue
        name = match.group("name")
        modifiers = tuple(match.group("prefix").split())
        declaration_line = _line_number(content, match.start())
        values.append(
            (
                StructuralSymbolSpan(
                    name=name,
                    symbol_type=match.group("kind"),
                    line_start=_annotated_line_start(content, declaration_line),
                    line_end=_line_number(content, body_end),
                    qualified_name=_qualified(module, name),
                    visibility=_visibility(name),
                    signature=_header_signature(content, match.start(), body_open),
                    modifiers=modifiers,
                    annotations=_annotations_before(content, declaration_line),
                ),
                body_open,
                body_end,
            )
        )
    return tuple(values)


def _constructors(
    content: str,
    masked: str,
    depths: list[int],
    *,
    class_name: str,
    body_open: int,
    body_end: int,
    module: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    target_depth = depths[body_open] + 1
    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:external|const|factory)\s+)*)"
        rf"(?P<class>{re.escape(class_name)})(?:\.(?P<named>{_IDENTIFIER}))?\s*\("
    )
    values: list[StructuralSymbolSpan] = []
    for match in pattern.finditer(masked, body_open + 1, body_end):
        if depths[match.start()] != target_depth:
            continue
        paren = masked.find("(", match.start(), match.end())
        close = _matching_delimiter(masked, paren, "(", ")")
        if close is None or close >= body_end:
            continue
        boundary = _callable_end(
            masked,
            depths,
            close + 1,
            target_depth=target_depth,
            limit=body_end,
        )
        if boundary is None:
            continue
        declaration_line = _line_number(content, match.start())
        named = match.group("named")
        name = named or class_name
        qualified_parent = _qualified(module, class_name)
        values.append(
            StructuralSymbolSpan(
                name=name,
                symbol_type="constructor",
                line_start=_annotated_line_start(content, declaration_line),
                line_end=_line_number(content, max(match.start(), boundary - 1)),
                qualified_name=_qualified(qualified_parent, name),
                visibility=_visibility(name),
                container=class_name,
                signature=_header_signature(content, match.start(), boundary),
                parameters=_parameter_names(masked[paren + 1 : close]),
                modifiers=tuple(match.group("prefix").split()),
                annotations=_annotations_before(content, declaration_line),
            )
        )
    return _ordered(values)


def _getters(
    content: str,
    masked: str,
    depths: list[int],
    *,
    target_depth: int,
    start: int,
    end: int,
    module: str | None,
    container: str,
) -> tuple[StructuralSymbolSpan, ...]:
    pattern = re.compile(
        rf"(?m)^[ \t]*(?P<prefix>(?:(?:external|static|final|covariant)\s+)*)"
        rf"(?P<type>{_IDENTIFIER}(?:[<>,?. \[\]]+{_IDENTIFIER})?[?]?)\s+"
        rf"get\s+(?P<name>{_IDENTIFIER})\b"
    )
    values: list[StructuralSymbolSpan] = []
    for match in pattern.finditer(masked, start, end):
        if depths[match.start()] != target_depth:
            continue
        boundary = _callable_end(
            masked,
            depths,
            match.end(),
            target_depth=target_depth,
            limit=end,
        )
        if boundary is None:
            continue
        declaration_line = _line_number(content, match.start())
        parent = _qualified(module, container)
        values.append(
            StructuralSymbolSpan(
                name=match.group("name"),
                symbol_type="method",
                line_start=_annotated_line_start(content, declaration_line),
                line_end=_line_number(content, max(match.start(), boundary - 1)),
                qualified_name=_qualified(parent, match.group("name")),
                visibility=_visibility(match.group("name")),
                container=container,
                signature=_header_signature(content, match.start(), boundary),
                modifiers=tuple(match.group("prefix").split()) + ("getter",),
                annotations=_annotations_before(content, declaration_line),
            )
        )
    return _ordered(values)


def _callables(
    content: str,
    masked: str,
    depths: list[int],
    *,
    target_depth: int,
    start: int,
    end: int,
    module: str | None,
    container: str | None,
) -> tuple[StructuralSymbolSpan, ...]:
    """Find ordinary Dart callable declarations at one proven brace depth."""

    open_paren = re.compile(r"(?m)^[ \t]*(?P<header>[^\n;={}]+?)\(")
    values: list[StructuralSymbolSpan] = []
    for match in open_paren.finditer(masked, start, end):
        if depths[match.start()] != target_depth:
            continue
        header = match.group("header").strip()
        if not header or header.startswith(("class ", "mixin ", "enum ")):
            continue
        identifiers = re.findall(_IDENTIFIER, header)
        if not identifiers:
            continue
        name = identifiers[-1]
        if name in _CONTROL_NAMES:
            continue
        if container is not None and (name == container or f"{container}." in header):
            continue
        if re.search(r"\bget\s+" + re.escape(name) + r"\s*$", header):
            continue
        if header.startswith(("return ", "throw ", "new ")):
            continue

        paren = masked.find("(", match.start(), match.end())
        close = _matching_delimiter(masked, paren, "(", ")")
        if close is None or close >= end:
            continue
        if not _ordinary_callable_suffix(masked, close + 1, limit=end):
            continue
        boundary = _callable_end(
            masked,
            depths,
            close + 1,
            target_depth=target_depth,
            limit=end,
        )
        if boundary is None:
            continue

        declaration_line = _line_number(content, match.start())
        parent = _qualified(module, container) if container else module
        modifiers = tuple(
            token
            for token in re.findall(_IDENTIFIER, header[: header.rfind(name)])
            if token in {"external", "static", "abstract", "covariant"}
        )
        values.append(
            StructuralSymbolSpan(
                name=name,
                symbol_type="method" if container else "function",
                line_start=_annotated_line_start(content, declaration_line),
                line_end=_line_number(content, max(match.start(), boundary - 1)),
                qualified_name=_qualified(parent, name),
                visibility=_visibility(name),
                container=container,
                signature=_header_signature(content, match.start(), boundary),
                parameters=_parameter_names(masked[paren + 1 : close]),
                modifiers=modifiers,
                annotations=_annotations_before(content, declaration_line),
            )
        )
    return _ordered(values)


def _ordinary_callable_suffix(masked: str, start: int, *, limit: int) -> bool:
    """Prove that parameters are followed by Dart declaration syntax, not a call use."""

    index = start
    while index < limit and masked[index].isspace():
        index += 1

    modifier = re.match(r"(?:async|sync)(?:\s*\*)?\b", masked[index:limit])
    if modifier is not None:
        index += modifier.end()
        while index < limit and masked[index].isspace():
            index += 1

    return index < limit and (masked.startswith("=>", index) or masked[index] in "{;")


def _callable_end(
    masked: str,
    depths: list[int],
    start: int,
    *,
    target_depth: int,
    limit: int,
) -> int | None:
    """Prove a callable terminator without consuming a later declaration."""

    index = start
    while index < limit:
        if depths[index] < target_depth:
            return None
        if depths[index] > target_depth:
            index += 1
            continue
        if masked.startswith("=>", index):
            semicolon = _semicolon_at_depth(masked, depths, index + 2, target_depth, limit)
            return None if semicolon is None else semicolon + 1
        char = masked[index]
        if char == "{":
            closing = _matching_brace(masked, index)
            return None if closing is None or closing >= limit else closing + 1
        if char == ";":
            return index + 1
        index += 1
    return None


def _semicolon_at_depth(
    masked: str,
    depths: list[int],
    start: int,
    target_depth: int,
    limit: int,
) -> int | None:
    paren = bracket = 0
    for index in range(start, limit):
        if depths[index] < target_depth:
            return None
        char = masked[index]
        if char == "(":
            paren += 1
        elif char == ")" and paren:
            paren -= 1
        elif char == "[":
            bracket += 1
        elif char == "]" and bracket:
            bracket -= 1
        elif char == ";" and depths[index] == target_depth and not paren and not bracket:
            return index
    return None


def _body_open(
    masked: str,
    depths: list[int],
    start: int,
    *,
    target_depth: int,
) -> int | None:
    line_breaks = 0
    index = start
    while index < len(masked):
        if depths[index] != target_depth:
            return None
        char = masked[index]
        if char == "{":
            return index
        if char == ";":
            return None
        if char == "\n":
            line_breaks += 1
            if line_breaks > 64:
                return None
            line_end = masked.find("\n", index + 1)
            if line_end < 0:
                line_end = len(masked)
            next_line = masked[index + 1 : line_end].lstrip()
            if next_line and _starts_new_declaration(next_line):
                return None
        index += 1
    return None


def _starts_new_declaration(line: str) -> bool:
    """Return whether a fresh top-level declaration starts before a type body opens."""

    if line.startswith("@") or _TYPE_START.match(line):
        return True
    if _GETTER_DECLARATION_START.match(line):
        return True
    callable_match = _CALLABLE_DECLARATION_START.match(line)
    return bool(
        callable_match is not None
        and callable_match.group("name") not in _CONTROL_NAMES
    )


def _matching_brace(masked: str, open_index: int) -> int | None:
    return _matching_delimiter(masked, open_index, "{", "}")


def _matching_delimiter(
    masked: str,
    open_index: int,
    opener: str,
    closer: str,
) -> int | None:
    if open_index < 0 or open_index >= len(masked) or masked[open_index] != opener:
        return None
    depth = 0
    for index in range(open_index, len(masked)):
        if masked[index] == opener:
            depth += 1
        elif masked[index] == closer:
            depth -= 1
            if depth == 0:
                return index
    return None


def _brace_depths(masked: str) -> list[int]:
    values = [0] * (len(masked) + 1)
    depth = 0
    for index, char in enumerate(masked):
        values[index] = depth
        if char == "{":
            depth += 1
        elif char == "}":
            depth = max(0, depth - 1)
    values[len(masked)] = depth
    return values


def _parameter_names(raw: str) -> tuple[str, ...]:
    values: list[str] = []
    for part in _split_parameters(raw):
        token = part.split("=", 1)[0].strip().strip("{}[]").strip()
        if not token:
            continue
        identifiers = [
            item
            for item in re.findall(_IDENTIFIER, token)
            if item not in {"required", "final", "var", "const", "covariant", "this", "super"}
        ]
        if identifiers:
            values.append(identifiers[-1])
    return tuple(values)


def _split_parameters(raw: str) -> tuple[str, ...]:
    values: list[str] = []
    current: list[str] = []
    depths = {"<": 0, "(": 0, "[": 0, "{": 0}
    closing = {">": "<", ")": "(", "]": "[", "}": "{"}
    for char in raw:
        if char in depths:
            depths[char] += 1
        elif char in closing and depths[closing[char]]:
            depths[closing[char]] -= 1
        if char == "," and not any(depths.values()):
            values.append("".join(current))
            current = []
        else:
            current.append(char)
    if current:
        values.append("".join(current))
    return tuple(values)


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


def _annotated_line_start(content: str, declaration_line: int) -> int:
    return declaration_line - len(_annotations_before(content, declaration_line))


def _header_signature(content: str, start: int, end: int) -> str:
    rendered = " ".join(content[start:end].strip().split())
    if "{" in rendered:
        rendered = rendered.split("{", 1)[0].strip()
    if "=>" in rendered:
        rendered = rendered.split("=>", 1)[0].strip()
    return rendered.rstrip(";")


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, max(0, index)) + 1


def _module_from_path(path: str | None) -> str | None:
    if not path:
        return None
    source = PurePosixPath(path)
    without_suffix = source.with_suffix("")
    parts = list(without_suffix.parts)
    if parts and parts[0] == "lib":
        parts = parts[1:]
    return ".".join(parts) if parts else without_suffix.name


def _qualified(parent: str | None, name: str | None) -> str | None:
    if not name:
        return parent
    return f"{parent}.{name}" if parent else name


def _visibility(name: str) -> str:
    return "private" if name.startswith("_") else "public"


def _ordered(
    values: list[StructuralSymbolSpan] | tuple[StructuralSymbolSpan, ...],
) -> tuple[StructuralSymbolSpan, ...]:
    unique: dict[tuple[object, ...], StructuralSymbolSpan] = {}
    for item in values:
        key = (item.qualified_name, item.symbol_type, item.line_start, item.line_end)
        unique[key] = item
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.line_start,
                item.line_end,
                item.qualified_name or item.name,
                item.symbol_type,
            ),
        )
    )
