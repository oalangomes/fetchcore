"""Chunk-local, syntax-observed call evidence for repository knowledge.

Call extraction proves call syntax independently from reference extraction. Imported
bindings are used only when they can resolve a callee without inference. Member
calls whose runtime target is unknown remain explicit syntax-level evidence instead
of being guessed or discarded.
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
from fetchcore.repository.references import _binding_table


_IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
_C_LIKE_CALL = re.compile(
    rf"(?<![A-Za-z0-9_$])(?P<chain>{_IDENTIFIER}"
    rf"(?:\s*(?:\?\.|\.)\s*{_IDENTIFIER})*)\s*\("
)
_NEW_BEFORE = re.compile(r"\bnew\s*$")
_RUNTIME_JS_IMPORT_KINDS = frozenset({"default", "named", "namespace", "require"})
_JS_BINDING_ASSIGNMENT = re.compile(
    rf"(?<![A-Za-z0-9_$?.])(?P<binding>{_IDENTIFIER})\s*"
    r"(?P<operator>\*\*=|&&=|\|\|=|\?\?=|[+\-*/%]=|=(?!=|>))"
)
_JS_ARROW_PARAMETERS = re.compile(
    rf"(?:\((?P<params>[^()]*)\)|(?P<single>{_IDENTIFIER}))\s*=>"
)
_JS_FUNCTION_PARAMETERS = re.compile(
    rf"\bfunction(?:\s+{_IDENTIFIER})?\s*\((?P<params>[^()]*)\)\s*\{{"
)


@dataclass(frozen=True)
class CallScope:
    """A more specific caller span nested inside the enclosing source chunk."""

    source_symbol: str
    line_start: int
    line_end: int
    shadowed_bindings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CallEvidence:
    """One call expression observed inside a canonical source chunk."""

    source_symbol: str | None
    callee: str
    target: str
    source: str | None
    kind: str
    line: int | None
    resolution: str

    def to_mapping(self) -> dict[str, object]:
        """Serialize this observed call without changing its provenance."""

        return {
            "source_symbol": self.source_symbol,
            "callee": self.callee,
            "target": self.target,
            "source": self.source,
            "kind": self.kind,
            "line": self.line,
            "resolution": self.resolution,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> CallEvidence:
        """Hydrate call evidence from its canonical mapping."""

        raw_line = value.get("line")
        line = (
            raw_line
            if isinstance(raw_line, int) and not isinstance(raw_line, bool)
            else None
        )
        callee = str(value.get("callee", value.get("target", "")))
        return cls(
            source_symbol=_optional_string(value.get("source_symbol")),
            callee=callee,
            target=str(value.get("target", callee)),
            source=_optional_string(value.get("source")),
            kind=str(value.get("kind", "unknown")),
            line=line,
            resolution=str(value.get("resolution", "unknown")),
        )

    @classmethod
    def legacy(cls, target: str) -> CallEvidence:
        """Hydrate pre-9.3 string calls without inventing provenance."""

        return cls(
            source_symbol=None,
            callee=target,
            target=target,
            source=None,
            kind="legacy",
            line=None,
            resolution="legacy",
        )


def extract_call_evidence(
    language: str,
    content: str,
    imports: tuple[ImportEvidence, ...],
    *,
    source_symbol: str,
    line_start: int,
    scopes: tuple[CallScope, ...] = (),
    lexical_content: str | None = None,
    root_shadowed_bindings: tuple[str, ...] = (),
    dynamic_binding_invalidations: tuple[tuple[str, int], ...] | None = None,
) -> tuple[CallEvidence, ...]:
    """Extract calls from one chunk and resolve only provable imported roots."""

    if not content:
        return ()
    bindings = _binding_table(language, imports)
    if language == "python":
        observed = _python_calls(
            lexical_content if lexical_content is not None else content,
            line_start=line_start,
        )
    elif language in {"javascript", "typescript", "java"}:
        observed = _c_like_calls(
            content,
            line_start=line_start,
            lexical_content=lexical_content,
        )
    else:
        return ()

    invalidations = (
        dynamic_binding_invalidations
        if dynamic_binding_invalidations is not None
        else extract_dynamic_binding_invalidations(
            language,
            content,
            imports,
            line_start=line_start,
        )
    )
    binding_scopes = scopes
    if language in {"javascript", "typescript"}:
        binding_scopes = (
            *scopes,
            *_inline_dynamic_shadow_scopes(
                content,
                imports,
                source_symbol=source_symbol,
                line_start=line_start,
            ),
        )
    values: list[CallEvidence] = []
    for chain, line, constructor in observed:
        if not chain:
            continue
        root = chain[0]
        imported = bindings.get(root)
        eligible = (
            imported is not None
            and _runtime_binding(language, imported)
            and _binding_still_proven(
                imported,
                root,
                line,
                scopes=binding_scopes,
                root_shadowed_bindings=root_shadowed_bindings,
                dynamic_binding_invalidations=invalidations,
            )
        )
        member_call = len(chain) > 1

        # Direct-call syntax is ambiguous with declarations in lexical languages.
        # Keep it only when an import binding proves the identifier, or when `new`
        # independently proves constructor syntax. Member access itself proves an
        # expression-level callee and can therefore be retained unresolved.
        if not member_call and not eligible and not constructor:
            continue

        callee = ".".join(chain)
        target: str | None = None
        source: str | None = None
        resolution = "syntax"
        if eligible and imported is not None:
            target = _resolved_target(language, imported, chain)
            if target is not None:
                source = imported.source or None
                resolution = (
                    "dynamic_import_binding"
                    if imported.kind == "dynamic"
                    else "import_binding"
                )

        if target is None:
            target = callee

        kind = (
            "constructor_call"
            if constructor
            else "member_call"
            if member_call
            else "direct_call"
        )
        values.append(
            CallEvidence(
                source_symbol=_caller_for_line(source_symbol, line, scopes),
                callee=callee,
                target=target,
                source=source,
                kind=kind,
                line=line,
                resolution=resolution,
            )
        )
    return _ordered(values)


def extract_dynamic_binding_invalidations(
    language: str,
    content: str,
    imports: tuple[ImportEvidence, ...],
    *,
    line_start: int = 1,
) -> tuple[tuple[str, int], ...]:
    """Observe assignments that make a dynamic-import binding unsafe to resolve."""

    if language not in {"javascript", "typescript"} or not content:
        return ()
    dynamic_imports: dict[str, ImportEvidence] = {}
    for item in imports:
        if item.kind != "dynamic" or item.name == item.source:
            continue
        current = dynamic_imports.get(item.name)
        item_line = item.line if item.line is not None else float("inf")
        current_line = (
            current.line
            if current is not None and current.line is not None
            else float("inf")
        )
        if current is None or item_line < current_line:
            dynamic_imports[item.name] = item
    if not dynamic_imports:
        return ()

    masked = mask_c_like_non_code(content)
    newline_offsets = [index for index, char in enumerate(masked) if char == "\n"]
    establishment_consumed: set[str] = set()
    invalidations: set[tuple[str, int]] = set()
    for match in _JS_BINDING_ASSIGNMENT.finditer(masked):
        binding = match.group("binding")
        imported = dynamic_imports.get(binding)
        if imported is None:
            continue
        line = line_start + _line_offset(newline_offsets, match.start("binding"))
        if imported.line is not None and line < imported.line:
            continue
        if (
            binding not in establishment_consumed
            and match.group("operator") == "="
            and line == imported.line
            and _starts_dynamic_import(masked, match.end("operator"))
        ):
            establishment_consumed.add(binding)
            continue
        invalidations.add((binding, line))
    return tuple(sorted(invalidations, key=lambda item: (item[1], item[0])))


def _starts_dynamic_import(content: str, start: int) -> bool:
    return re.match(r"\s*(?:await\s+)?import\s*\(", content[start:]) is not None


def _inline_dynamic_shadow_scopes(
    content: str,
    imports: tuple[ImportEvidence, ...],
    *,
    source_symbol: str,
    line_start: int,
) -> tuple[CallScope, ...]:
    """Bound fail-closed shadowing guards for inline JS/TS functions."""

    dynamic_bindings = tuple(
        dict.fromkeys(
            item.name
            for item in imports
            if item.kind == "dynamic"
            and item.name != item.source
            and re.fullmatch(_IDENTIFIER, item.name)
        )
    )
    if not dynamic_bindings:
        return ()

    masked = mask_c_like_non_code(content)
    newline_offsets = [index for index, char in enumerate(masked) if char == "\n"]
    scopes: list[CallScope] = []

    for match in _JS_ARROW_PARAMETERS.finditer(masked):
        raw_parameters = match.group("params") or match.group("single") or ""
        shadowed = _shadowed_dynamic_parameter_names(
            raw_parameters,
            dynamic_bindings,
        )
        if not shadowed:
            continue
        body_start = match.end()
        while body_start < len(masked) and masked[body_start].isspace():
            body_start += 1
        if body_start < len(masked) and masked[body_start] == "{":
            body_end = _matching_c_like_brace(masked, body_start)
        else:
            body_end = _arrow_expression_end(masked, body_start)
        if body_end is None:
            continue
        scopes.append(
            CallScope(
                source_symbol=source_symbol,
                line_start=line_start + _line_offset(newline_offsets, match.start()),
                line_end=line_start + _line_offset(newline_offsets, body_end),
                shadowed_bindings=shadowed,
            )
        )

    for match in _JS_FUNCTION_PARAMETERS.finditer(masked):
        raw_parameters = match.group("params") or ""
        shadowed = _shadowed_dynamic_parameter_names(
            raw_parameters,
            dynamic_bindings,
        )
        if not shadowed:
            continue
        body_open = match.end() - 1
        body_end = _matching_c_like_brace(masked, body_open)
        if body_end is None:
            continue
        scopes.append(
            CallScope(
                source_symbol=source_symbol,
                line_start=line_start + _line_offset(newline_offsets, match.start()),
                line_end=line_start + _line_offset(newline_offsets, body_end),
                shadowed_bindings=shadowed,
            )
        )

    return tuple(scopes)


def _shadowed_dynamic_parameter_names(
    raw_parameters: str,
    dynamic_bindings: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        binding
        for binding in dynamic_bindings
        if re.search(
            rf"(?<![A-Za-z0-9_$]){re.escape(binding)}(?![A-Za-z0-9_$])",
            raw_parameters,
        )
    )


def _matching_c_like_brace(content: str, open_index: int) -> int | None:
    depth = 0
    for index in range(open_index, len(content)):
        char = content[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _arrow_expression_end(content: str, start: int) -> int | None:
    if start >= len(content):
        return None
    round_depth = 0
    square_depth = 0
    brace_depth = 0
    for index in range(start, len(content)):
        char = content[index]
        if char == "(":
            round_depth += 1
        elif char == ")":
            if round_depth == 0:
                return max(start, index - 1)
            round_depth -= 1
        elif char == "[":
            square_depth += 1
        elif char == "]":
            if square_depth == 0:
                return max(start, index - 1)
            square_depth -= 1
        elif char == "{":
            brace_depth += 1
        elif char == "}":
            if brace_depth == 0:
                return max(start, index - 1)
            brace_depth -= 1
        elif char in {",", ";"} and not any(
            (round_depth, square_depth, brace_depth)
        ):
            return max(start, index - 1)
    return len(content) - 1


def _binding_still_proven(
    imported: ImportEvidence,
    binding: str,
    line: int,
    *,
    scopes: tuple[CallScope, ...],
    root_shadowed_bindings: tuple[str, ...],
    dynamic_binding_invalidations: tuple[tuple[str, int], ...],
) -> bool:
    if imported.kind != "dynamic":
        return True
    if binding in root_shadowed_bindings:
        return False
    if any(
        scope.line_start <= line <= scope.line_end
        and binding in scope.shadowed_bindings
        for scope in scopes
    ):
        return False
    return not any(
        invalidated_binding == binding and invalidated_line <= line
        for invalidated_binding, invalidated_line in dynamic_binding_invalidations
    )


def _runtime_binding(language: str, imported: ImportEvidence) -> bool:
    if imported.kind == "legacy":
        return False
    if language == "python":
        return imported.kind in {"module", "named"} and imported.name != "*"
    if language in {"javascript", "typescript"}:
        return imported.kind in _RUNTIME_JS_IMPORT_KINDS or (
            imported.kind == "dynamic"
            and imported.name != imported.source
            and bool(re.fullmatch(_IDENTIFIER, imported.name))
        )
    if language == "java":
        return imported.kind in {"type", "static"} and imported.name != "*"
    return False


def _resolved_target(
    language: str,
    imported: ImportEvidence,
    chain: tuple[str, ...],
) -> str | None:
    suffix = chain[1:]
    if language == "python":
        if imported.kind == "module":
            if imported.alias:
                return ".".join((imported.source, *suffix))
            source_parts = tuple(part for part in imported.source.split(".") if part)
            if not source_parts:
                return None
            if len(chain) >= len(source_parts) and chain[: len(source_parts)] == source_parts:
                return ".".join(chain)
            if len(source_parts) == 1 and chain[0] == source_parts[0]:
                return ".".join(chain)
            return None
        return ".".join((imported.name, *suffix))

    if language in {"javascript", "typescript"}:
        if imported.kind == "dynamic":
            return ".".join((imported.source, *suffix))
        base = imported.source if imported.kind == "namespace" else imported.name
        return ".".join((base, *suffix))

    if language == "java":
        if imported.kind == "static":
            return ".".join((imported.source, *suffix))
        return ".".join((imported.name, *suffix))

    return None


def _python_calls(
    content: str,
    *,
    line_start: int,
) -> tuple[tuple[tuple[str, ...], int, bool], ...]:
    try:
        stream = tokenize.generate_tokens(io.StringIO(content).readline)
        tokens = list(stream)
    except (IndentationError, tokenize.TokenError):
        try:
            tokens = []
            stream = tokenize.generate_tokens(io.StringIO(content).readline)
            for observed in stream:
                tokens.append(observed)
        except (IndentationError, tokenize.TokenError):
            pass

    significant = tuple(
        item
        for item in tokens
        if item.type
        not in {
            token.INDENT,
            token.DEDENT,
            token.NEWLINE,
            tokenize.NL,
            tokenize.COMMENT,
            token.ENDMARKER,
        }
    )
    values: list[tuple[tuple[str, ...], int, bool]] = []
    for index, observed in enumerate(significant):
        if observed.type != token.NAME:
            continue
        previous = significant[index - 1] if index > 0 else None
        if previous is not None and previous.string in {"def", "class", "@", "."}:
            continue

        chain = [observed.string]
        cursor = index + 1
        while (
            cursor + 1 < len(significant)
            and significant[cursor].string == "."
            and significant[cursor + 1].type == token.NAME
        ):
            chain.append(significant[cursor + 1].string)
            cursor += 2
        if cursor >= len(significant) or significant[cursor].string != "(":
            continue
        values.append(
            (
                tuple(chain),
                line_start + observed.start[0] - 1,
                False,
            )
        )
    return tuple(values)


def _c_like_calls(
    content: str,
    *,
    line_start: int,
    lexical_content: str | None = None,
) -> tuple[tuple[tuple[str, ...], int, bool], ...]:
    masked = lexical_content if lexical_content is not None else mask_c_like_non_code(content)
    newline_offsets = [index for index, char in enumerate(masked) if char == "\n"]
    values: list[tuple[tuple[str, ...], int, bool]] = []
    for match in _C_LIKE_CALL.finditer(masked):
        start = match.start("chain")
        if _preceded_by_annotation(masked, start):
            continue
        raw_chain = match.group("chain")
        chain = tuple(
            item
            for item in re.split(r"\s*(?:\?\.|\.)\s*", raw_chain)
            if item
        )
        prefix = masked[max(0, start - 12) : start]
        constructor = bool(_NEW_BEFORE.search(prefix))
        line = line_start + _line_offset(newline_offsets, start)
        values.append((chain, line, constructor))
    return tuple(values)


def _preceded_by_annotation(content: str, start: int) -> bool:
    index = start - 1
    while index >= 0 and content[index] in {" ", "\t"}:
        index -= 1
    return index >= 0 and content[index] == "@"


def _line_offset(newline_offsets: list[int], index: int) -> int:
    # Avoid repeated prefix counting so extraction remains O(n log lines) instead
    # of degrading toward O(n²) on large source files.
    low = 0
    high = len(newline_offsets)
    while low < high:
        middle = (low + high) // 2
        if newline_offsets[middle] < index:
            low = middle + 1
        else:
            high = middle
    return low


def _caller_for_line(
    fallback: str,
    line: int,
    scopes: tuple[CallScope, ...],
) -> str:
    matching = [scope for scope in scopes if scope.line_start <= line <= scope.line_end]
    if not matching:
        return fallback
    narrowest = min(
        matching,
        key=lambda item: (item.line_end - item.line_start, item.line_start, item.source_symbol),
    )
    return narrowest.source_symbol


def _ordered(values: Iterable[CallEvidence]) -> tuple[CallEvidence, ...]:
    unique: dict[tuple[object, ...], CallEvidence] = {}
    for item in values:
        key = (
            item.source_symbol,
            item.callee,
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
                item.callee,
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
