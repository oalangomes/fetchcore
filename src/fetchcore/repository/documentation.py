"""Typed JavaDoc/JSDoc evidence associated with observed source symbols.

Documentation evidence is intentionally backend-neutral and stores only normalized,
directly observable facts. The original documentation text remains losslessly present
in the source chunk content and is not duplicated in canonical evidence.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast


@dataclass(frozen=True)
class DocumentationParameterEvidence:
    """One documented source parameter."""

    name: str
    description: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "description": self.description}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> DocumentationParameterEvidence:
        return cls(
            name=str(value.get("name", "")),
            description=_optional_string(value.get("description")),
        )


@dataclass(frozen=True)
class DocumentationThrowsEvidence:
    """One documented thrown exception/error."""

    name: str
    description: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return {"name": self.name, "description": self.description}

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, object],
    ) -> DocumentationThrowsEvidence:
        return cls(
            name=str(value.get("name", "")),
            description=_optional_string(value.get("description")),
        )


@dataclass(frozen=True)
class DocumentationEvidence:
    """Documentation directly attached to one observed source symbol."""

    kind: str
    source_symbol: str
    line_start: int
    line_end: int
    summary: str | None = None
    parameters: tuple[DocumentationParameterEvidence, ...] = ()
    returns: str | None = None
    throws: tuple[DocumentationThrowsEvidence, ...] = ()
    deprecated: bool = False

    def to_mapping(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "source_symbol": self.source_symbol,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "summary": self.summary,
            "parameters": [item.to_mapping() for item in self.parameters],
            "returns": self.returns,
            "throws": [item.to_mapping() for item in self.throws],
            "deprecated": self.deprecated,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> DocumentationEvidence:
        return cls(
            kind=str(value.get("kind", "unknown")),
            source_symbol=str(value.get("source_symbol", "")),
            line_start=_integer(value.get("line_start"), default=1),
            line_end=_integer(value.get("line_end"), default=1),
            summary=_optional_string(value.get("summary")),
            parameters=_parameter_evidence(value.get("parameters")),
            returns=_optional_string(value.get("returns")),
            throws=_throws_evidence(value.get("throws")),
            deprecated=bool(value.get("deprecated", False)),
        )


class _SymbolObservation(Protocol):
    name: str
    line_start: int
    line_end: int
    qualified_name: str | None


def extract_documentation_evidence(
    language: str,
    content: str,
    symbols: Sequence[_SymbolObservation],
) -> tuple[DocumentationEvidence, ...]:
    """Associate immediately preceding JavaDoc/JSDoc blocks with source symbols.

    Association is fail-closed: only ``/** ... */`` blocks ending on the line
    immediately before a symbol (including its already-observed decorators or
    annotations) are accepted. Co-located symbols sharing the same declaration span
    have one deterministic owner: the first symbol in source order.
    """

    normalized_language = language.casefold()
    if normalized_language == "java":
        kind = "javadoc"
    elif normalized_language in {
        "javascript",
        "typescript",
        "js",
        "jsx",
        "ts",
        "tsx",
    }:
        kind = "jsdoc"
    else:
        return ()

    lines = content.splitlines()
    evidence: list[DocumentationEvidence] = []
    seen_spans: set[tuple[int, int]] = set()

    for symbol in sorted(
        symbols,
        key=lambda item: (item.line_start, item.line_end),
    ):
        span = (symbol.line_start, symbol.line_end)
        if span in seen_spans:
            continue
        seen_spans.add(span)

        bounds = _documentation_block_before(lines, symbol.line_start)
        if bounds is None:
            continue
        line_start, line_end = bounds
        parsed = _parse_documentation_block(lines[line_start - 1 : line_end])
        source_symbol = symbol.qualified_name or symbol.name
        evidence.append(
            DocumentationEvidence(
                kind=kind,
                source_symbol=source_symbol,
                line_start=line_start,
                line_end=line_end,
                summary=parsed.summary,
                parameters=parsed.parameters,
                returns=parsed.returns,
                throws=parsed.throws,
                deprecated=parsed.deprecated,
            )
        )

    return tuple(evidence)


@dataclass(frozen=True)
class _ParsedDocumentation:
    summary: str | None = None
    parameters: tuple[DocumentationParameterEvidence, ...] = ()
    returns: str | None = None
    throws: tuple[DocumentationThrowsEvidence, ...] = ()
    deprecated: bool = False


def _documentation_block_before(
    lines: list[str],
    declaration_line: int,
) -> tuple[int, int] | None:
    end_index = declaration_line - 2
    if end_index < 0:
        return None

    terminal = lines[end_index].strip()
    if not terminal.endswith("*/"):
        return None

    if "/**" in terminal:
        return (declaration_line - 1, declaration_line - 1)

    cursor = end_index - 1
    while cursor >= 0:
        stripped = lines[cursor].strip()
        if "/**" in stripped:
            if stripped.find("/**") < stripped.find("*/") or "*/" not in stripped:
                return (cursor + 1, end_index + 1)
            return None
        if "/*" in stripped or "*/" in stripped:
            return None
        cursor -= 1
    return None


def _parse_documentation_block(lines: list[str]) -> _ParsedDocumentation:
    cleaned = _clean_documentation_lines(lines)
    summary_parts: list[str] = []
    parameters: list[DocumentationParameterEvidence] = []
    throws: list[DocumentationThrowsEvidence] = []
    returns: str | None = None
    deprecated = False
    tags_started = False

    for line in cleaned:
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("@"):
            if not tags_started:
                summary_parts.append(stripped)
            continue

        tags_started = True
        tag, _, raw_value = stripped.partition(" ")
        value = raw_value.strip()
        normalized_tag = tag.casefold()
        if normalized_tag == "@param":
            parameter = _parse_parameter(value)
            if parameter is not None:
                parameters.append(parameter)
        elif normalized_tag in {"@return", "@returns"}:
            returns = value or None
        elif normalized_tag in {"@throws", "@exception"}:
            thrown = _parse_throws(value)
            if thrown is not None:
                throws.append(thrown)
        elif normalized_tag == "@deprecated":
            deprecated = True

    summary = " ".join(summary_parts).strip() or None
    return _ParsedDocumentation(
        summary=summary,
        parameters=tuple(parameters),
        returns=returns,
        throws=tuple(throws),
        deprecated=deprecated,
    )


def _clean_documentation_lines(lines: list[str]) -> tuple[str, ...]:
    if not lines:
        return ()

    rendered = list(lines)
    rendered[0] = rendered[0].split("/**", 1)[1]
    rendered[-1] = rendered[-1].rsplit("*/", 1)[0]

    cleaned: list[str] = []
    for line in rendered:
        stripped = line.strip()
        if stripped.startswith("*"):
            stripped = stripped[1:].lstrip()
        cleaned.append(stripped)
    return tuple(cleaned)


def _parse_parameter(value: str) -> DocumentationParameterEvidence | None:
    remainder = _drop_jsdoc_type(value.strip())
    if not remainder:
        return None
    name, _, description = remainder.partition(" ")
    normalized_name = name.strip().strip("[]")
    if not normalized_name:
        return None
    return DocumentationParameterEvidence(
        name=normalized_name,
        description=_clean_description(description),
    )


def _parse_throws(value: str) -> DocumentationThrowsEvidence | None:
    remainder = _drop_jsdoc_type(value.strip())
    if not remainder:
        return None
    name, _, description = remainder.partition(" ")
    if not name:
        return None
    return DocumentationThrowsEvidence(
        name=name,
        description=_clean_description(description),
    )


def _drop_jsdoc_type(value: str) -> str:
    if not value.startswith("{"):
        return value
    closing = value.find("}")
    if closing < 0:
        return value
    return value[closing + 1 :].strip()


def _clean_description(value: str) -> str | None:
    rendered = value.strip()
    if rendered.startswith("-"):
        rendered = rendered[1:].lstrip()
    return rendered or None


def _parameter_evidence(value: object) -> tuple[DocumentationParameterEvidence, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[DocumentationParameterEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(DocumentationParameterEvidence.from_mapping(item))
    return tuple(items)


def _throws_evidence(value: object) -> tuple[DocumentationThrowsEvidence, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    items: list[DocumentationThrowsEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(DocumentationThrowsEvidence.from_mapping(item))
    return tuple(items)


def documentation_evidence_from_mapping(
    value: object,
) -> tuple[DocumentationEvidence, ...]:
    """Hydrate typed documentation while remaining compatible with old payloads."""

    if not isinstance(value, (list, tuple)):
        return ()
    items: list[DocumentationEvidence] = []
    for item in value:
        if isinstance(item, Mapping):
            items.append(DocumentationEvidence.from_mapping(item))
    return tuple(items)


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value)
    return rendered if rendered else None


def _integer(value: object, *, default: int) -> int:
    try:
        return int(cast(int | float | str, value))
    except (TypeError, ValueError):
        return default
