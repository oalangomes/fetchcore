"""Chunk-local, syntax-observed reference evidence for repository knowledge.

Reference extraction intentionally resolves only identifiers that can be tied to an
observed import binding. It does not infer calls, local variable types, runtime
objects, framework semantics, or cross-file symbol existence.
"""

from __future__ import annotations

import io
import re
import token
import tokenize
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from fetchcore.repository.imports import ImportEvidence
from fetchcore.repository.lexical import mask_c_like_non_code


_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


@dataclass(frozen=True)
class ReferenceEvidence:
    """One imported binding referenced inside a canonical source chunk."""

    source_symbol: str | None
    name: str
    target: str
    source: str | None
    kind: str
    line: int | None
    resolution: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize this observed imported-binding reference."""

        return {
            "source_symbol": self.source_symbol,
            "name": self.name,
            "target": self.target,
            "source": self.source,
            "kind": self.kind,
            "line": self.line,
            "resolution": self.resolution,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> ReferenceEvidence:
        """Hydrate reference evidence from its canonical mapping."""

        raw_line = value.get("line")
        line = (
            raw_line
            if isinstance(raw_line, int) and not isinstance(raw_line, bool)
            else None
        )
        return cls(
            source_symbol=_optional_string(value.get("source_symbol")),
            name=str(value.get("name", "")),
            target=str(value.get("target", value.get("name", ""))),
            source=_optional_string(value.get("source")),
            kind=str(value.get("kind", "unknown")),
            line=line,
            resolution=str(value.get("resolution", "unknown")),
        )

    @classmethod
    def legacy(cls, target: str) -> ReferenceEvidence:
        """Hydrate pre-9.2 string references without inventing provenance."""

        return cls(
            source_symbol=None,
            name=target,
            target=target,
            source=None,
            kind="legacy",
            line=None,
            resolution="legacy",
        )


def extract_reference_evidence(
    language: str,
    content: str,
    imports: tuple[ImportEvidence, ...],
    *,
    source_symbol: str,
    line_start: int,
    lexical_content: str | None = None,
) -> tuple[ReferenceEvidence, ...]:
    """Resolve chunk-local identifier usages against unambiguous import bindings."""

    bindings = _binding_table(language, imports)
    if not bindings or not content:
        return ()

    if language == "python":
        occurrences = _python_identifiers(
            lexical_content if lexical_content is not None else content,
            line_start=line_start,
        )
    elif language in {"javascript", "typescript", "java"}:
        occurrences = _c_like_identifiers(
            content,
            line_start=line_start,
            lexical_content=lexical_content,
        )
    else:
        return ()

    values: list[ReferenceEvidence] = []
    for name, line in occurrences:
        imported = bindings.get(name)
        if imported is None:
            continue
        values.append(
            ReferenceEvidence(
                source_symbol=source_symbol,
                name=name,
                target=_reference_target(language, imported),
                source=imported.source or None,
                kind="identifier",
                line=line,
                resolution="import_binding",
            )
        )
    return _ordered(values)


def _binding_table(
    language: str,
    imports: tuple[ImportEvidence, ...],
) -> dict[str, ImportEvidence]:
    candidates: dict[str, list[ImportEvidence]] = {}
    for imported in imports:
        binding = _binding_name(language, imported)
        if binding is None:
            continue
        candidates.setdefault(binding, []).append(imported)

    result: dict[str, ImportEvidence] = {}
    for binding, observed in candidates.items():
        unique = {
            (item.name, item.source, item.alias, item.kind): item for item in observed
        }
        if len(unique) == 1:
            result[binding] = next(iter(unique.values()))
    return result


def _binding_name(language: str, imported: ImportEvidence) -> str | None:
    if imported.kind == "legacy":
        return None

    if language == "python":
        if imported.kind == "module":
            return imported.alias or imported.name.split(".", 1)[0]
        if imported.kind == "named" and imported.name != "*":
            return imported.alias or imported.name
        return None

    if language in {"javascript", "typescript"}:
        if imported.kind in {
            "default",
            "named",
            "type-default",
            "type-named",
        }:
            return imported.alias or imported.name
        if imported.kind in {"namespace", "type-namespace"}:
            return imported.alias
        if imported.kind in {"require", "dynamic"}:
            if imported.name == imported.source or not _IDENTIFIER.fullmatch(imported.name):
                return None
            return imported.name
        return None

    if language == "java":
        if imported.kind in {"type", "static"} and imported.name != "*":
            return imported.name
        return None

    return None


def _reference_target(language: str, imported: ImportEvidence) -> str:
    if language == "python" and imported.kind == "module":
        return imported.source
    if language in {"javascript", "typescript"} and imported.kind in {
        "namespace",
        "type-namespace",
    }:
        return imported.source
    return imported.name


def _python_identifiers(content: str, *, line_start: int) -> tuple[tuple[str, int], ...]:
    values: list[tuple[str, int]] = []
    stream = tokenize.generate_tokens(io.StringIO(content).readline)
    try:
        for observed in stream:
            if observed.type != token.NAME:
                continue
            values.append((observed.string, line_start + observed.start[0] - 1))
    except (IndentationError, tokenize.TokenError):
        # Chunks may begin/end inside a larger syntactic construct. Tokens already
        # emitted are still trustworthy; incomplete trailing syntax is ignored.
        pass
    return tuple(values)


def _c_like_identifiers(
    content: str,
    *,
    line_start: int,
    lexical_content: str | None = None,
) -> tuple[tuple[str, int], ...]:
    masked = lexical_content if lexical_content is not None else mask_c_like_non_code(content)
    values: list[tuple[str, int]] = []
    for offset, source_line in enumerate(masked.splitlines(), start=0):
        line = line_start + offset
        values.extend((match.group(0), line) for match in _IDENTIFIER.finditer(source_line))
    return tuple(values)
def _ordered(values: Iterable[ReferenceEvidence]) -> tuple[ReferenceEvidence, ...]:
    unique: dict[tuple[object, ...], ReferenceEvidence] = {}
    for item in values:
        key = (
            item.source_symbol,
            item.name,
            item.target,
            item.source,
            item.kind,
            item.line,
            item.resolution,
        )
        unique[key] = item
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                item.line if item.line is not None else 0,
                item.name,
                item.target,
                item.source or "",
            ),
        )
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value)
    return rendered if rendered else None
