"""Python docstrings projected into canonical DocumentationEvidence."""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from fetchcore.repository.documentation import (
    DocumentationEvidence,
    DocumentationParameterEvidence,
    DocumentationThrowsEvidence,
)


_GOOGLE_SECTIONS = frozenset(
    {
        "args",
        "arguments",
        "parameters",
        "keyword args",
        "keyword arguments",
        "returns",
        "yields",
        "raises",
        "deprecated",
    }
)
_REST_FIELD = re.compile(
    r"^:(?P<tag>parameter|argument|returns|raises|exception|deprecated|param|arg|"
    r"rtype|return|raise|type)(?P<head>[^:]*)\s*:\s*(?P<value>.*)$",
    re.IGNORECASE,
)
_GOOGLE_PARAM = re.compile(
    r"^(?P<name>\*{0,2}[A-Za-z_][A-Za-z0-9_.]*)"
    r"(?:\s*\((?P<type>.+)\))?\s*:\s*(?P<description>.*)$"
)
_GOOGLE_RAISE = re.compile(
    r"^(?P<name>[A-Za-z_][A-Za-z0-9_.]*)\s*:\s*(?P<description>.*)$"
)
_DocstringOwner: TypeAlias = (
    ast.Module | ast.ClassDef | ast.AsyncFunctionDef | ast.FunctionDef
)


class _SymbolObservation(Protocol):
    name: str
    line_start: int
    line_end: int
    qualified_name: str | None


@dataclass(frozen=True)
class _ParsedDocstring:
    summary: str | None = None
    parameters: tuple[DocumentationParameterEvidence, ...] = ()
    returns: str | None = None
    throws: tuple[DocumentationThrowsEvidence, ...] = ()
    deprecated: bool = False


def extract_python_docstring_evidence(
    content: str,
    symbols: Sequence[_SymbolObservation],
    *,
    module: str | None = None,
) -> tuple[DocumentationEvidence, ...]:
    """Extract PEP 257 docstrings for observed module, class, function, and method owners."""

    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return ()

    evidence: list[DocumentationEvidence] = []
    module_doc = _docstring_statement(tree)
    if module_doc is not None:
        evidence.append(
            _documentation_for_node(
                tree,
                module_doc,
                source_symbol=module or _module_from_symbols(symbols) or "<module>",
            )
        )

    for node in tree.body:
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            _append_observed_docstring(evidence, node, symbols, container=None)
            continue
        if not isinstance(node, ast.ClassDef):
            continue
        _append_observed_docstring(evidence, node, symbols, container=None)
        for member in node.body:
            if isinstance(member, (ast.AsyncFunctionDef, ast.FunctionDef)):
                _append_observed_docstring(
                    evidence,
                    member,
                    symbols,
                    container=node.name,
                )

    return tuple(
        sorted(
            evidence,
            key=lambda item: (item.line_start, item.line_end, item.source_symbol),
        )
    )


def _append_observed_docstring(
    evidence: list[DocumentationEvidence],
    node: ast.ClassDef | ast.AsyncFunctionDef | ast.FunctionDef,
    symbols: Sequence[_SymbolObservation],
    *,
    container: str | None,
) -> None:
    symbol = _observed_symbol(symbols, node, container=container)
    doc = _docstring_statement(node)
    if symbol is None or doc is None:
        return
    evidence.append(
        _documentation_for_node(
            node,
            doc,
            source_symbol=symbol.qualified_name or symbol.name,
        )
    )


def _documentation_for_node(
    node: _DocstringOwner,
    statement: ast.Expr,
    *,
    source_symbol: str,
) -> DocumentationEvidence:
    parsed = _parse_docstring(ast.get_docstring(node, clean=True) or "")
    return DocumentationEvidence(
        kind="python-docstring",
        source_symbol=source_symbol,
        line_start=int(statement.lineno),
        line_end=int(statement.end_lineno or statement.lineno),
        summary=parsed.summary,
        parameters=parsed.parameters,
        returns=parsed.returns,
        throws=parsed.throws,
        deprecated=parsed.deprecated,
    )


def _docstring_statement(node: _DocstringOwner) -> ast.Expr | None:
    if not node.body or not isinstance(node.body[0], ast.Expr):
        return None
    statement = node.body[0]
    value = statement.value
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return statement
    return None


def _observed_symbol(
    symbols: Sequence[_SymbolObservation],
    node: ast.ClassDef | ast.AsyncFunctionDef | ast.FunctionDef,
    *,
    container: str | None,
) -> _SymbolObservation | None:
    candidates = [
        symbol
        for symbol in symbols
        if symbol.name == node.name and getattr(symbol, "container", None) == container
    ]
    expected_end = int(node.end_lineno or node.lineno)
    return next(
        (symbol for symbol in candidates if symbol.line_end == expected_end),
        candidates[0] if candidates else None,
    )


def _module_from_symbols(symbols: Sequence[_SymbolObservation]) -> str | None:
    for symbol in symbols:
        if getattr(symbol, "container", None) is not None:
            continue
        qualified = symbol.qualified_name
        if qualified and "." in qualified:
            return qualified.rsplit(".", 1)[0]
    return None


def _parse_docstring(value: str) -> _ParsedDocstring:
    lines = value.splitlines()
    summary = next((line.strip() for line in lines if line.strip()), None)
    if any(_REST_FIELD.match(line.strip()) for line in lines):
        parsed = _parse_rest(lines)
    elif any(_google_heading(line) is not None for line in lines):
        parsed = _parse_google(lines)
    else:
        parsed = _ParsedDocstring()
    deprecated = parsed.deprecated or any(
        line.strip().casefold().startswith(".. deprecated::") for line in lines
    )
    return _ParsedDocstring(
        summary=summary,
        parameters=parsed.parameters,
        returns=parsed.returns,
        throws=parsed.throws,
        deprecated=deprecated,
    )


def _parse_rest(lines: list[str]) -> _ParsedDocstring:
    parameters: list[DocumentationParameterEvidence] = []
    throws: list[DocumentationThrowsEvidence] = []
    returns: str | None = None
    return_type: str | None = None
    deprecated = False

    for line in lines:
        match = _REST_FIELD.match(line.strip())
        if match is None:
            continue
        tag = match.group("tag").casefold()
        head = match.group("head").strip()
        value = match.group("value").strip()
        if tag in {"param", "parameter", "arg", "argument"}:
            parts = head.split()
            name = parts[-1].lstrip("*") if parts else ""
            if name:
                parameters.append(
                    DocumentationParameterEvidence(
                        name=name,
                        description=value or None,
                    )
                )
        elif tag in {"return", "returns"}:
            returns = value or None
        elif tag == "rtype":
            return_type = value or None
        elif tag in {"raise", "raises", "exception"}:
            if head:
                throws.append(
                    DocumentationThrowsEvidence(
                        name=head,
                        description=value or None,
                    )
                )
        elif tag == "deprecated":
            deprecated = True

    if return_type:
        returns = f"{return_type}: {returns}" if returns else return_type
    return _ParsedDocstring(
        parameters=_dedupe_parameters(parameters),
        returns=returns,
        throws=_dedupe_throws(throws),
        deprecated=deprecated,
    )


def _google_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.endswith(":"):
        return None
    heading = stripped[:-1].strip().casefold()
    return heading if heading in _GOOGLE_SECTIONS else None


def _parse_google(lines: list[str]) -> _ParsedDocstring:
    parameters: list[DocumentationParameterEvidence] = []
    throws: list[DocumentationThrowsEvidence] = []
    returns: str | None = None
    deprecated = False

    for heading, block in _google_blocks(lines):
        if heading in {
            "args",
            "arguments",
            "parameters",
            "keyword args",
            "keyword arguments",
        }:
            for line in block:
                match = _GOOGLE_PARAM.match(line.strip())
                if match is None:
                    continue
                parameters.append(
                    DocumentationParameterEvidence(
                        name=match.group("name").lstrip("*"),
                        description=match.group("description").strip() or None,
                    )
                )
        elif heading in {"returns", "yields"} and returns is None:
            returns = " ".join(line.strip() for line in block if line.strip()) or None
        elif heading == "raises":
            for line in block:
                match = _GOOGLE_RAISE.match(line.strip())
                if match is None:
                    continue
                throws.append(
                    DocumentationThrowsEvidence(
                        name=match.group("name"),
                        description=match.group("description").strip() or None,
                    )
                )
        elif heading == "deprecated":
            deprecated = True

    return _ParsedDocstring(
        parameters=_dedupe_parameters(parameters),
        returns=returns,
        throws=_dedupe_throws(throws),
        deprecated=deprecated,
    )


def _google_blocks(lines: list[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    blocks: list[tuple[str, tuple[str, ...]]] = []
    index = 0
    while index < len(lines):
        heading = _google_heading(lines[index])
        if heading is None:
            index += 1
            continue
        index += 1
        block: list[str] = []
        while index < len(lines):
            if _google_heading(lines[index]) is not None:
                break
            line = lines[index]
            if line.strip() and not line[:1].isspace():
                break
            block.append(line)
            index += 1
        blocks.append((heading, tuple(block)))
    return tuple(blocks)


def _dedupe_parameters(
    values: Sequence[DocumentationParameterEvidence],
) -> tuple[DocumentationParameterEvidence, ...]:
    result: list[DocumentationParameterEvidence] = []
    seen: set[str] = set()
    for item in values:
        if item.name in seen:
            continue
        seen.add(item.name)
        result.append(item)
    return tuple(result)


def _dedupe_throws(
    values: Sequence[DocumentationThrowsEvidence],
) -> tuple[DocumentationThrowsEvidence, ...]:
    result: list[DocumentationThrowsEvidence] = []
    seen: set[tuple[str, str | None]] = set()
    for item in values:
        key = (item.name, item.description)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return tuple(result)
