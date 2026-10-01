"""Typed, data-minimizing SAP Commerce ImpEx header evidence.

The extractor models operations, item types, header columns, safe structural
modifiers, and header-position macros. It intentionally does not copy ImpEx data
rows or arbitrary macro/modifier values into canonical knowledge. Macro values are
resolved only when used as header fragments and reducible to a safe column grammar.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


SAP_COMMERCE_IMPEX_METADATA_KEY = "sap_commerce_impex"
_OPERATION_RE = re.compile(r"^\s*(INSERT_UPDATE|INSERT|UPDATE|REMOVE)\b", re.IGNORECASE)
_MACRO_DEFINITION_RE = re.compile(r"^\s*\$([A-Za-z_][A-Za-z0-9_.-]*)\s*=(.*)$")
_MACRO_FIELD_RE = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_.-]*)$")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_@&][A-Za-z0-9_.@&-]*")
_SAFE_EXPRESSION_RE = re.compile(r"^[A-Za-z0-9_.$@&(),:\-\s]+$")
_SAFE_CLASS_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)+$")
_MAX_MACRO_DEPTH = 8


@dataclass(frozen=True)
class SapCommerceImpexColumnEvidence:
    """One structural ImpEx header column without arbitrary modifier literal values."""

    name: str
    expression: str
    reference_path: tuple[str, ...] = ()
    modifier_keys: tuple[str, ...] = ()
    unique: bool | None = None
    translator: str | None = None
    source_macro: str | None = None
    resolved: bool = True

    def to_mapping(self) -> dict[str, object]:
        """Serialize structural ImpEx column evidence."""

        return {
            "name": self.name,
            "expression": self.expression,
            "reference_path": list(self.reference_path),
            "modifier_keys": list(self.modifier_keys),
            "unique": self.unique,
            "translator": self.translator,
            "source_macro": self.source_macro,
            "resolved": self.resolved,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceImpexColumnEvidence:
        """Hydrate structural ImpEx column evidence from a mapping."""

        return cls(
            name=str(value.get("name", "")),
            expression=str(value.get("expression", "")),
            reference_path=_strings(value.get("reference_path")),
            modifier_keys=_strings(value.get("modifier_keys")),
            unique=_optional_bool(value.get("unique")),
            translator=_optional_string(value.get("translator")),
            source_macro=_optional_string(value.get("source_macro")),
            resolved=bool(value.get("resolved", True)),
        )


@dataclass(frozen=True)
class SapCommerceImpexHeaderEvidence:
    """One ImpEx operation/header schema observed directly from source."""

    operation: str
    item_type: str
    columns: tuple[SapCommerceImpexColumnEvidence, ...] = ()
    referenced_macros: tuple[str, ...] = ()
    modifier_keys: tuple[str, ...] = ()
    batch_mode: bool | None = None
    processor: str | None = None

    @property
    def symbol_name(self) -> str:
        """Return a stable operation/type semantic symbol."""

        return f"{self.operation}:{self.item_type}"

    @property
    def column_names(self) -> tuple[str, ...]:
        """Return observed non-empty header column names in source order."""

        return tuple(item.name for item in self.columns if item.name)

    @property
    def referenced_classes(self) -> tuple[str, ...]:
        """Return safe processor and translator Java class references."""

        values: list[str] = []
        if self.processor:
            values.append(self.processor)
        values.extend(item.translator for item in self.columns if item.translator)
        return tuple(dict.fromkeys(values))

    def to_mapping(self) -> dict[str, object]:
        """Serialize one ImpEx header schema."""

        return {
            "operation": self.operation,
            "item_type": self.item_type,
            "columns": [item.to_mapping() for item in self.columns],
            "referenced_macros": list(self.referenced_macros),
            "modifier_keys": list(self.modifier_keys),
            "batch_mode": self.batch_mode,
            "processor": self.processor,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> SapCommerceImpexHeaderEvidence:
        """Hydrate one ImpEx header schema from a mapping."""

        return cls(
            operation=str(value.get("operation", "")),
            item_type=str(value.get("item_type", "")),
            columns=_mapped_items(
                value.get("columns"),
                SapCommerceImpexColumnEvidence.from_mapping,
            ),
            referenced_macros=_strings(value.get("referenced_macros")),
            modifier_keys=_strings(value.get("modifier_keys")),
            batch_mode=_optional_bool(value.get("batch_mode")),
            processor=_optional_string(value.get("processor")),
        )


@dataclass(frozen=True)
class SapCommerceImpexUnit:
    """One sanitized ImpEx header unit with exact source-line provenance."""

    evidence: SapCommerceImpexHeaderEvidence
    line_start: int
    line_end: int


def extract_sap_commerce_impex_units(content: str) -> tuple[SapCommerceImpexUnit, ...]:
    """Extract one semantic unit per supported ImpEx header, never copying data rows."""

    macros: dict[str, str] = {}
    units: list[SapCommerceImpexUnit] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        macro_match = _MACRO_DEFINITION_RE.match(line)
        if macro_match is not None:
            macros[macro_match.group(1)] = macro_match.group(2).strip()
            continue

        parsed_header = _parse_header(line)
        if parsed_header is None:
            continue
        operation, item_type, header_modifiers, raw_columns = parsed_header
        modifier_keys, batch_mode, processor = _parse_header_modifiers(header_modifiers)
        columns: list[SapCommerceImpexColumnEvidence] = []
        referenced_macros: list[str] = []
        for raw_field in _split_top_level(raw_columns, ";"):
            field = raw_field.strip()
            if not field:
                continue
            macro_field = _MACRO_FIELD_RE.fullmatch(field)
            if macro_field is not None:
                macro_name = macro_field.group(1)
                referenced_macros.append(macro_name)
                expanded = _resolve_header_macro(macro_name, macros, stack=())
                if expanded:
                    columns.extend(expanded)
                else:
                    columns.append(
                        SapCommerceImpexColumnEvidence(
                            name=macro_name,
                            expression=f"${macro_name}",
                            source_macro=macro_name,
                            resolved=False,
                        )
                    )
                continue
            parsed = _parse_column(field, source_macro=None)
            if parsed is not None:
                columns.append(parsed)

        units.append(
            SapCommerceImpexUnit(
                evidence=SapCommerceImpexHeaderEvidence(
                    operation=operation,
                    item_type=item_type,
                    columns=tuple(columns),
                    referenced_macros=tuple(dict.fromkeys(referenced_macros)),
                    modifier_keys=modifier_keys,
                    batch_mode=batch_mode,
                    processor=processor,
                ),
                line_start=line_number,
                line_end=line_number,
            )
        )
    return tuple(units)


def render_sap_commerce_impex_unit(evidence: SapCommerceImpexHeaderEvidence) -> str:
    """Render only structural header facts suitable for search/retrieval."""

    lines = [f"SAP Commerce ImpEx: {evidence.operation} {evidence.item_type}"]
    if evidence.modifier_keys:
        lines.append("header_modifiers: " + ", ".join(evidence.modifier_keys))
    if evidence.batch_mode is not None:
        lines.append("batch_mode: " + str(evidence.batch_mode).lower())
    if evidence.processor:
        lines.append("processor: " + evidence.processor)
    for column in evidence.columns:
        details: list[str] = []
        if column.reference_path:
            details.append("path=" + ",".join(column.reference_path))
        if column.modifier_keys:
            details.append("modifiers=" + ",".join(column.modifier_keys))
        if column.unique is not None:
            details.append("unique=" + str(column.unique).lower())
        if column.translator:
            details.append("translator=" + column.translator)
        if column.source_macro:
            details.append("macro=$" + column.source_macro)
        if not column.resolved:
            details.append("unresolved=true")
        suffix = " [" + "; ".join(details) + "]" if details else ""
        lines.append(f"column: {column.expression}{suffix}")
    if evidence.referenced_macros:
        lines.append("header_macros: " + ", ".join(f"${item}" for item in evidence.referenced_macros))
    return "\n".join(lines) + "\n"


def _parse_header(line: str) -> tuple[str, str, tuple[str, ...], str] | None:
    operation_match = _OPERATION_RE.match(line)
    if operation_match is None:
        return None
    operation = operation_match.group(1).upper()
    remainder = line[operation_match.end() :].strip()
    item_expression, raw_columns = _split_first_top_level(remainder, ";")
    item_type, modifier_groups = _strip_modifiers(item_expression)
    item_type = item_type.strip()
    if not item_type or _IDENTIFIER_RE.fullmatch(item_type) is None:
        return None
    return operation, item_type, modifier_groups, raw_columns


def _parse_header_modifiers(
    groups: tuple[str, ...],
) -> tuple[tuple[str, ...], bool | None, str | None]:
    keys: list[str] = []
    batch_mode: bool | None = None
    processor: str | None = None
    for group in groups:
        for token in _split_top_level(group, ","):
            stripped = token.strip()
            if not stripped:
                continue
            key, separator, raw_value = stripped.partition("=")
            normalized_key = key.strip()
            if not normalized_key:
                continue
            keys.append(normalized_key)
            if not separator:
                continue
            value = raw_value.strip()
            folded = normalized_key.casefold()
            if folded == "batchmode":
                batch_mode = _text_bool(value)
            elif folded == "processor" and _SAFE_CLASS_RE.fullmatch(value):
                processor = value
    return tuple(dict.fromkeys(keys)), batch_mode, processor


def _resolve_header_macro(
    name: str,
    macros: Mapping[str, str],
    *,
    stack: tuple[str, ...],
) -> tuple[SapCommerceImpexColumnEvidence, ...]:
    if name in stack or len(stack) >= _MAX_MACRO_DEPTH:
        return ()
    raw_value = macros.get(name)
    if raw_value is None:
        return ()
    columns: list[SapCommerceImpexColumnEvidence] = []
    for raw_field in _split_top_level(raw_value, ";"):
        field = raw_field.strip()
        if not field:
            continue
        nested = _MACRO_FIELD_RE.fullmatch(field)
        if nested is not None:
            resolved = _resolve_header_macro(
                nested.group(1),
                macros,
                stack=(*stack, name),
            )
            if not resolved:
                return ()
            columns.extend(
                SapCommerceImpexColumnEvidence(
                    name=item.name,
                    expression=item.expression,
                    reference_path=item.reference_path,
                    modifier_keys=item.modifier_keys,
                    unique=item.unique,
                    translator=item.translator,
                    source_macro=name,
                    resolved=item.resolved,
                )
                for item in resolved
            )
            continue
        parsed = _parse_column(field, source_macro=name)
        if parsed is None:
            return ()
        columns.append(parsed)
    return tuple(columns)


def _parse_column(
    raw: str,
    *,
    source_macro: str | None,
) -> SapCommerceImpexColumnEvidence | None:
    expression, modifiers = _strip_modifiers(raw)
    expression = expression.strip()
    if not expression or _SAFE_EXPRESSION_RE.fullmatch(expression) is None:
        return None
    identifiers = tuple(_IDENTIFIER_RE.findall(expression))
    if not identifiers:
        return None
    modifier_keys: list[str] = []
    unique: bool | None = None
    translator: str | None = None
    for modifier_group in modifiers:
        for token in _split_top_level(modifier_group, ","):
            stripped = token.strip()
            if not stripped:
                continue
            key, separator, raw_value = stripped.partition("=")
            normalized_key = key.strip()
            if not normalized_key:
                continue
            modifier_keys.append(normalized_key)
            if not separator:
                continue
            value = raw_value.strip()
            folded = normalized_key.casefold()
            if folded == "unique":
                unique = _text_bool(value)
            elif folded == "translator" and _SAFE_CLASS_RE.fullmatch(value):
                translator = value
    return SapCommerceImpexColumnEvidence(
        name=identifiers[0],
        expression=expression,
        reference_path=tuple(identifiers[1:]),
        modifier_keys=tuple(dict.fromkeys(modifier_keys)),
        unique=unique,
        translator=translator,
        source_macro=source_macro,
        resolved=True,
    )


def _split_first_top_level(value: str, separator: str) -> tuple[str, str]:
    square_depth = 0
    paren_depth = 0
    quote: str | None = None
    for index, character in enumerate(value):
        if quote is not None:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            continue
        if character == "[":
            square_depth += 1
            continue
        if character == "]":
            square_depth = max(0, square_depth - 1)
            continue
        if character == "(":
            paren_depth += 1
            continue
        if character == ")":
            paren_depth = max(0, paren_depth - 1)
            continue
        if character == separator and square_depth == 0 and paren_depth == 0:
            return value[:index], value[index + 1 :]
    return value, ""


def _strip_modifiers(raw: str) -> tuple[str, tuple[str, ...]]:
    expression: list[str] = []
    modifier_groups: list[str] = []
    modifier: list[str] = []
    bracket_depth = 0
    quote: str | None = None
    for character in raw:
        if quote is not None:
            if bracket_depth:
                modifier.append(character)
            else:
                expression.append(character)
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            if bracket_depth:
                modifier.append(character)
            else:
                expression.append(character)
            continue
        if character == "[":
            if bracket_depth:
                modifier.append(character)
            bracket_depth += 1
            continue
        if character == "]" and bracket_depth:
            bracket_depth -= 1
            if bracket_depth:
                modifier.append(character)
            else:
                modifier_groups.append("".join(modifier))
                modifier = []
            continue
        if bracket_depth:
            modifier.append(character)
        else:
            expression.append(character)
    if bracket_depth:
        return "", ()
    return "".join(expression), tuple(modifier_groups)


def _split_top_level(value: str, separator: str) -> tuple[str, ...]:
    fields: list[str] = []
    current: list[str] = []
    square_depth = 0
    paren_depth = 0
    quote: str | None = None
    for character in value:
        if quote is not None:
            current.append(character)
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
            current.append(character)
            continue
        if character == "[":
            square_depth += 1
            current.append(character)
            continue
        if character == "]":
            square_depth = max(0, square_depth - 1)
            current.append(character)
            continue
        if character == "(":
            paren_depth += 1
            current.append(character)
            continue
        if character == ")":
            paren_depth = max(0, paren_depth - 1)
            current.append(character)
            continue
        if character == separator and square_depth == 0 and paren_depth == 0:
            fields.append("".join(current))
            current = []
            continue
        current.append(character)
    fields.append("".join(current))
    return tuple(fields)


def _text_bool(value: str) -> bool | None:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _mapped_items(value: object, factory: Any) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(factory(item) for item in value if isinstance(item, Mapping))

# Persisted knowledge-pack fingerprints retain historical class paths.
SapCommerceImpexColumnEvidence.__module__ = "fetchcore.repository.sap_commerce_impex"
SapCommerceImpexHeaderEvidence.__module__ = "fetchcore.repository.sap_commerce_impex"
SapCommerceImpexUnit.__module__ = "fetchcore.repository.sap_commerce_impex"
