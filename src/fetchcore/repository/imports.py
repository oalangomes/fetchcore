"""Typed, syntax-observed import evidence for canonical repository knowledge.

This module deliberately resolves only what import syntax itself proves. It does
not attempt module resolution, symbol lookup, type inference, or call-graph
construction. Those are later relational-evidence concerns.
"""

from __future__ import annotations

import ast
import re
from bisect import bisect_right
from collections.abc import Iterator, Mapping
from dataclasses import dataclass


_JS_STATIC_START = re.compile(r"^import(?:\s|[\"'])")
_JS_REQUIRE = re.compile(
    r"(?m)^[ \t]*(?:(?:const|let|var)\s+"
    r"(?P<binding>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*)?"
    r"require\(\s*(?P<quote>[\"'])(?P<source>[^\"']+)(?P=quote)\s*\)"
)
_JS_DYNAMIC = re.compile(
    r"\bimport\(\s*(?P<quote>[\"'])(?P<source>[^\"']+)(?P=quote)\s*\)"
)
_JS_DYNAMIC_ASSIGNMENT = re.compile(
    r"(?m)^[ \t]*(?:const|let|var)\s+"
    r"(?P<binding>[A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*"
    r"(?:await\s+)?(?P<dynamic>import\(\s*(?P<quote>[\"'])"
    r"(?P<source>[^\"']+)(?P=quote)\s*\))"
)


@dataclass(frozen=True)
class ImportEvidence:
    """One import binding directly observed in source syntax."""

    name: str
    source: str
    kind: str
    line: int | None
    alias: str | None = None

    def to_mapping(self) -> dict[str, object]:
        """Serialize this observed import binding."""

        return {
            "name": self.name,
            "source": self.source,
            "alias": self.alias,
            "kind": self.kind,
            "line": self.line,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ImportEvidence:
        """Hydrate import evidence from its canonical mapping."""

        raw_line = value.get("line")
        line = (
            raw_line
            if isinstance(raw_line, int) and not isinstance(raw_line, bool)
            else None
        )
        raw_alias = value.get("alias")
        alias = str(raw_alias) if isinstance(raw_alias, str) and raw_alias else None
        return cls(
            name=str(value.get("name", "")),
            source=str(value.get("source", "")),
            kind=str(value.get("kind", "unknown")),
            line=line,
            alias=alias,
        )

    @classmethod
    def legacy(cls, source: str) -> ImportEvidence:
        """Hydrate pre-9.1 string imports without inventing unavailable facts."""

        return cls(
            name=source,
            source=source,
            kind="legacy",
            line=None,
        )


def extract_import_evidence(language: str, content: str) -> tuple[ImportEvidence, ...]:
    """Extract deterministic import bindings for the supported rich languages."""

    if language == "python":
        return _python_imports(content)
    if language in {"javascript", "typescript"}:
        return _javascript_imports(content)
    if language == "java":
        return _java_imports(content)
    if language == "dart":
        return _dart_imports(content)
    return ()


def _dart_imports(content: str) -> tuple[ImportEvidence, ...]:
    """Extract Dart library imports without resolving packages or symbols."""

    from fetchcore.repository.dart_lexical import analyze_dart_non_code

    masked, _ = analyze_dart_non_code(content)
    values: list[ImportEvidence] = []
    for match in re.finditer(r"(?m)^[ \t]*import\b", masked):
        line_end = content.find(";", match.start())
        if line_end < 0:
            continue
        statement = content[match.start() : line_end + 1]
        observed = re.match(
            r"""^\s*import\s+(?:r|R)?(?P<quote>['"])(?P<source>.+?)(?P=quote)"""
            r"""(?P<tail>.*?)\s*;\s*$""",
            statement,
            re.DOTALL,
        )
        if observed is None:
            continue
        source = observed.group("source")
        tail = observed.group("tail")
        prefix = re.search(r"\bas\s+([A-Za-z_$][A-Za-z0-9_$]*)\b", tail)
        alias = prefix.group(1) if prefix is not None else None
        values.append(
            ImportEvidence(
                name=alias or source,
                source=source,
                alias=alias,
                kind="library-prefix" if alias else "library",
                line=_line_number(content, match.start()),
            )
        )
    return _ordered(values)


def _python_imports(content: str) -> tuple[ImportEvidence, ...]:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return ()

    values: list[ImportEvidence] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            for item in node.names:
                values.append(
                    ImportEvidence(
                        name=item.name,
                        source=item.name,
                        alias=item.asname,
                        kind="module",
                        line=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            source = ("." * node.level) + (node.module or "")
            for item in node.names:
                values.append(
                    ImportEvidence(
                        name=item.name,
                        source=source,
                        alias=item.asname,
                        kind="wildcard" if item.name == "*" else "named",
                        line=node.lineno,
                    )
                )
    return _ordered(values)


def _javascript_imports(content: str) -> tuple[ImportEvidence, ...]:
    values: list[ImportEvidence] = []

    for statement, line in _javascript_static_import_statements(content):
        parsed = _parse_javascript_static_import(statement)
        if parsed is None:
            continue
        clause, source, type_only = parsed
        if clause is None:
            values.append(
                ImportEvidence(
                    name=source,
                    source=source,
                    kind="side-effect",
                    line=line,
                )
            )
            continue
        values.extend(
            _javascript_clause(
                clause,
                source=source,
                line=line,
                type_only=type_only,
            )
        )

    newline_offsets = tuple(index for index, char in enumerate(content) if char == "\n")
    for require in _JS_REQUIRE.finditer(content):
        source = require.group("source")
        binding = require.group("binding")
        values.append(
            ImportEvidence(
                name=binding or source,
                source=source,
                kind="require",
                line=_line_number_from_offsets(newline_offsets, require.start()),
            )
        )
    masked = _javascript_code_mask(content)
    dynamic_bindings = {
        match.start("dynamic"): match.group("binding")
        for match in _JS_DYNAMIC_ASSIGNMENT.finditer(content)
        if _javascript_assignment_is_code(masked, match)
    }
    for dynamic in _JS_DYNAMIC.finditer(content):
        source = dynamic.group("source")
        binding = dynamic_bindings.get(dynamic.start())
        values.append(
            ImportEvidence(
                name=binding or source,
                source=source,
                kind="dynamic",
                line=_line_number_from_offsets(newline_offsets, dynamic.start()),
            )
        )

    return _ordered(values)


def _javascript_code_mask(content: str) -> str:
    from fetchcore.repository.lexical import mask_c_like_non_code

    return mask_c_like_non_code(content)


def _javascript_assignment_is_code(
    masked: str,
    match: re.Match[str],
) -> bool:
    binding = match.group("binding")
    start = match.start("binding")
    end = match.end("binding")
    return (
        masked[start:end] == binding
        and masked[
            match.start("dynamic") : match.start("dynamic") + len("import")
        ]
        == "import"
    )


def _javascript_static_import_statements(content: str) -> Iterator[tuple[str, int]]:
    """Scan static imports once without regex backtracking across the source file."""

    parts: list[str] = []
    start_line = 0
    saw_from = False
    for line_number, raw_line in enumerate(content.splitlines(), start=1):
        stripped = raw_line.strip()
        static_start = bool(_JS_STATIC_START.match(stripped)) and not stripped.startswith(
            "import("
        )

        if not parts:
            if not static_start:
                continue
            parts = [stripped]
            start_line = line_number
            saw_from = _contains_from_token(stripped)
        elif static_start:
            parts = [stripped]
            start_line = line_number
            saw_from = _contains_from_token(stripped)
        else:
            parts.append(stripped)
            saw_from = saw_from or _contains_from_token(stripped)

        if len(parts) == 1 and _parse_javascript_static_import(stripped) is not None:
            yield stripped, start_line
            parts = []
            start_line = 0
            saw_from = False
            continue

        likely_complete = ";" in stripped or (
            saw_from and ("\"" in stripped or "'" in stripped)
        )
        if not likely_complete:
            continue

        statement = " ".join(part for part in parts if part)
        if _parse_javascript_static_import(statement) is not None:
            yield statement, start_line
        parts = []
        start_line = 0
        saw_from = False


def _parse_javascript_static_import(
    statement: str,
) -> tuple[str | None, str, bool] | None:
    text = statement.strip()
    if text.endswith(";"):
        text = text[:-1].rstrip()
    if not text.startswith("import "):
        return None

    body = text.removeprefix("import ").strip()
    side_effect_source = _quoted_module_source(body)
    if side_effect_source is not None:
        return None, side_effect_source, False

    type_only = False
    if body.startswith("type "):
        type_only = True
        body = body.removeprefix("type ").strip()

    separator = body.rfind(" from ")
    if separator < 0:
        return None
    clause = body[:separator].strip()
    source = _quoted_module_source(body[separator + len(" from ") :].strip())
    if not clause or source is None:
        return None
    return clause, source, type_only


def _quoted_module_source(value: str) -> str | None:
    if len(value) < 2 or value[0] not in {"'", '"'} or value[-1] != value[0]:
        return None
    source = value[1:-1]
    return source if source else None


def _contains_from_token(value: str) -> bool:
    return bool(re.search(r"(?:^|\s)from(?:\s|$)", value))


def _javascript_clause(
    clause: str,
    *,
    source: str,
    line: int,
    type_only: bool,
) -> list[ImportEvidence]:
    values: list[ImportEvidence] = []
    remaining = clause.strip()

    if remaining and not remaining.startswith(("{", "*")):
        default, separator, tail = remaining.partition(",")
        name = default.strip()
        if name:
            values.append(
                ImportEvidence(
                    name=name,
                    source=source,
                    kind="type-default" if type_only else "default",
                    line=line,
                )
            )
        remaining = tail.strip() if separator else ""

    if remaining.startswith("*"):
        match = re.match(r"\*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)", remaining)
        if match:
            values.append(
                ImportEvidence(
                    name="*",
                    source=source,
                    alias=match.group(1),
                    kind="type-namespace" if type_only else "namespace",
                    line=line,
                )
            )
        return values

    if remaining.startswith("{") and "}" in remaining:
        body = remaining[1 : remaining.rfind("}")]
        for raw in body.split(","):
            token = raw.strip()
            if not token:
                continue
            inline_type = False
            if token.startswith("type "):
                inline_type = True
                token = token.removeprefix("type ").strip()
            items = re.split(r"\s+as\s+", token, maxsplit=1)
            name = items[0].strip()
            alias = items[1].strip() if len(items) == 2 else None
            if not name:
                continue
            values.append(
                ImportEvidence(
                    name=name,
                    source=source,
                    alias=alias,
                    kind="type-named" if type_only or inline_type else "named",
                    line=line,
                )
            )
    return values


def _java_imports(content: str) -> tuple[ImportEvidence, ...]:
    pattern = re.compile(
        r"(?m)^\s*import\s+(?P<static>static\s+)?"
        r"(?P<source>[A-Za-z_][A-Za-z0-9_]*"
        r"(?:\.[A-Za-z_][A-Za-z0-9_]*)*(?:\.\*)?)\s*;"
    )
    values: list[ImportEvidence] = []
    for match in pattern.finditer(content):
        source = match.group("source")
        static = bool(match.group("static"))
        wildcard = source.endswith(".*")
        name = "*" if wildcard else source.rsplit(".", 1)[-1]
        if static and wildcard:
            kind = "static-wildcard"
        elif static:
            kind = "static"
        elif wildcard:
            kind = "wildcard"
        else:
            kind = "type"
        values.append(
            ImportEvidence(
                name=name,
                source=source,
                kind=kind,
                line=_line_number(content, match.start()),
            )
        )
    return _ordered(values)


def _line_number_from_offsets(newline_offsets: tuple[int, ...], index: int) -> int:
    return bisect_right(newline_offsets, index) + 1


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, max(0, index)) + 1


def _ordered(values: list[ImportEvidence]) -> tuple[ImportEvidence, ...]:
    unique: dict[tuple[object, ...], ImportEvidence] = {}
    for item in values:
        key = (item.line, item.source, item.name, item.alias, item.kind)
        unique[key] = item
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.line if item.line is not None else 0,
                item.source,
                item.name,
                item.alias or "",
                item.kind,
            ),
        )
    )
