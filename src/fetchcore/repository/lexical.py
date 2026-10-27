"""Shared lexical hygiene for observable repository source syntax.

The helpers in this module are deliberately backend-neutral. They remove quoted
text and comments without changing source geometry, allowing structural,
relational and projection code to agree about which characters are executable
source rather than prose.
"""

from __future__ import annotations

import io
import re
import token
import tokenize
from bisect import bisect_right
from dataclasses import dataclass


_C_LIKE_IDENTIFIER = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_LEXICALLY_SUPPORTED_LANGUAGES = frozenset(
    {"python", "javascript", "typescript", "java", "groovy", "dart", "go"}
)
_JS_CONTROL_PAREN_KEYWORDS = frozenset(
    {"catch", "for", "if", "switch", "while", "with"}
)


@dataclass(frozen=True)
class LexicalChunkProjection:
    """Backend-neutral lexical facts projected onto one canonical chunk."""

    masked_content: str
    identifiers: tuple[str, ...]


@dataclass(frozen=True)
class LexicalSourceAnalysis:
    """One full-source lexical pass reusable by every chunk consumer."""

    masked_content: str
    line_offsets: tuple[int, ...]
    identifiers_by_line: tuple[tuple[str, ...], ...]
    non_code_newlines: frozenset[int] = frozenset()

    def project(
        self,
        *,
        line_start: int,
        line_end: int,
        content: str,
    ) -> LexicalChunkProjection:
        """Project full-source state without restarting a scanner at the chunk."""

        if line_start < 1 or line_end < line_start:
            raise ValueError("lexical projection requires a valid inclusive line range")
        start_index = line_start - 1
        if start_index >= len(self.line_offsets):
            raise ValueError("lexical projection starts beyond the analyzed source")
        start = self.line_offsets[start_index]
        end = (
            self.line_offsets[line_end]
            if line_end < len(self.line_offsets)
            else len(self.masked_content)
        )
        masked = self.masked_content[start:end]
        if len(masked) != len(content) or masked.count("\n") != content.count("\n"):
            raise ValueError("lexical projection does not match canonical chunk geometry")
        identifiers = tuple(
            identifier
            for line in range(line_start, line_end + 1)
            if line - 1 < len(self.identifiers_by_line)
            for identifier in self.identifiers_by_line[line - 1]
        )
        return LexicalChunkProjection(masked_content=masked, identifiers=identifiers)


def analyze_lexical_source(
    language: str | None,
    content: str,
) -> LexicalSourceAnalysis | None:
    """Analyze supported syntax once from the authoritative source beginning."""

    if language not in _LEXICALLY_SUPPORTED_LANGUAGES:
        return None
    line_offsets = _line_offsets(content)
    if language == "python":
        masked, observed = _analyze_python(content, line_offsets)
        non_code_newlines: frozenset[int] = frozenset()
    else:
        if language == "dart":
            from fetchcore.repository.dart_lexical import analyze_dart_non_code

            masked, non_code_newlines = analyze_dart_non_code(content)
        else:
            masked, non_code_newlines = _analyze_c_like(content)
        observed = tuple(
            (match.group(0), bisect_right(line_offsets, match.start()))
            for match in _C_LIKE_IDENTIFIER.finditer(masked)
        )
    return LexicalSourceAnalysis(
        masked_content=masked,
        line_offsets=line_offsets,
        identifiers_by_line=_identifiers_by_line(observed, len(line_offsets)),
        non_code_newlines=non_code_newlines,
    )


def supports_lexical_analysis(language: str | None) -> bool:
    return language in _LEXICALLY_SUPPORTED_LANGUAGES


def extract_lexical_code_identifiers(
    language: str | None,
    content: str,
) -> tuple[str, ...] | None:
    """Return code tokens for supported languages, or ``None`` for fallback.

    Returning ``None`` is distinct from returning an empty tuple: callers retain
    their compatibility behavior for languages where this module cannot reliably
    distinguish source syntax from free text.
    """

    analysis = analyze_lexical_source(language, content)
    if analysis is None:
        return None
    return tuple(
        identifier
        for line in analysis.identifiers_by_line
        for identifier in line
    )


def mask_c_like_non_code(content: str) -> str:
    """Mask comments and quoted literals while preserving line/column geometry.

    Single- and double-quoted literals are masked completely. Java text blocks are
    treated as one literal. For JavaScript templates, textual portions are masked
    while ``${...}`` expressions remain code, including nested templates.
    Unterminated literals/comments remain masked to EOF.
    """

    masked, _ = _analyze_c_like(content)
    return masked


def _analyze_c_like(content: str) -> tuple[str, frozenset[int]]:
    """Return a geometry-preserving mask and non-code newline offsets."""

    result = list(content)
    non_code_newlines: set[int] = set()
    index = 0
    state = "code"
    delimiter = ""
    template_expressions: list[int] = []
    template_openers: list[int] = []
    paren_contexts: list[bool] = []
    after_control_paren = False
    previous_code_index: int | None = None
    while index < len(content):
        current = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""

        if state == "code":
            if current == "$" and following == "/":
                delimiter = "/$"
                _mask_range(result, index, index + 2)
                result[index] = "0"
                previous_code_index = index
                after_control_paren = False
                index += 2
                state = "string"
                continue
            if current == "/" and following == "/":
                _mask_range(result, index, index + 2)
                index += 2
                state = "line-comment"
                continue
            if current == "/" and following == "*":
                _mask_range(result, index, index + 2)
                index += 2
                state = "block-comment"
                continue
            if current == "/" and _starts_regex_literal(
                content,
                index,
                previous_code_index,
                after_control_paren=after_control_paren,
            ):
                result[index] = "0"
                previous_code_index = index
                after_control_paren = False
                index += 1
                state = "regex"
                delimiter = ""
                continue
            triple_delimiter = next(
                (
                    candidate
                    for candidate in ('"""', "'''")
                    if content.startswith(candidate, index)
                ),
                None,
            )
            if triple_delimiter is not None:
                delimiter = triple_delimiter
                _mask_range(result, index, index + 3)
                result[index] = "0"
                previous_code_index = index
                after_control_paren = False
                index += 3
                state = "string"
                continue
            if current in {"'", '"', "`"}:
                if current == "`":
                    result[index] = "0"
                    template_openers.append(index)
                    previous_code_index = index
                    after_control_paren = False
                    index += 1
                    state = "template-text"
                    continue
                delimiter = current
                result[index] = "0"
                previous_code_index = index
                after_control_paren = False
                index += 1
                state = "string"
                continue
            if current == "(":
                paren_contexts.append(
                    _previous_identifier(content, previous_code_index)
                    in _JS_CONTROL_PAREN_KEYWORDS
                )
                after_control_paren = False
                previous_code_index = index
                index += 1
                continue
            if current == ")":
                after_control_paren = paren_contexts.pop() if paren_contexts else False
                previous_code_index = index
                index += 1
                continue
            if template_expressions and current == "{":
                template_expressions[-1] += 1
                after_control_paren = False
                previous_code_index = index
                index += 1
                continue
            if template_expressions and current == "}":
                if template_expressions[-1] == 0:
                    template_expressions.pop()
                    previous_code_index = index
                    after_control_paren = False
                    index += 1
                    state = "template-text"
                    continue
                template_expressions[-1] -= 1
                after_control_paren = False
                previous_code_index = index
                index += 1
                continue
            if not current.isspace():
                after_control_paren = False
                previous_code_index = index
            index += 1
            continue

        if state == "line-comment":
            if current == "\n":
                state = "code"
            else:
                result[index] = " "
            index += 1
            continue

        if state == "block-comment":
            if current == "*" and following == "/":
                _mask_range(result, index, index + 2)
                index += 2
                state = "code"
                continue
            if current == "\n":
                non_code_newlines.add(index)
            else:
                result[index] = " "
            index += 1
            continue

        if state == "template-text":
            if current == "\\":
                result[index] = " "
                if index + 1 < len(content):
                    if content[index + 1] == "\n":
                        non_code_newlines.add(index + 1)
                    else:
                        result[index + 1] = " "
                    index += 2
                else:
                    index += 1
                continue
            if current == "`":
                result[index] = " "
                previous_code_index = template_openers.pop()
                after_control_paren = False
                index += 1
                state = "code"
                continue
            if current == "$" and following == "{":
                template_expressions.append(0)
                result[index] = " "
                previous_code_index = None
                after_control_paren = False
                index += 2
                state = "code"
                continue
            if current == "\n":
                non_code_newlines.add(index)
            else:
                result[index] = " "
            index += 1
            continue

        if state == "regex":
            if current == "\\":
                result[index] = " "
                if index + 1 < len(content):
                    if content[index + 1] != "\n":
                        result[index + 1] = " "
                    index += 2
                else:
                    index += 1
                continue
            if current == "[":
                delimiter = "character-class"
                result[index] = " "
                index += 1
                continue
            if current == "]" and delimiter == "character-class":
                delimiter = ""
                result[index] = " "
                index += 1
                continue
            if current == "/" and not delimiter:
                result[index] = " "
                index += 1
                while index < len(content) and content[index].isalpha():
                    result[index] = " "
                    index += 1
                state = "code"
                continue
            if current == "\n":
                state = "code"
            else:
                result[index] = " "
            index += 1
            continue

        if current == "\\":
            result[index] = " "
            if index + 1 < len(content):
                if content[index + 1] == "\n":
                    non_code_newlines.add(index + 1)
                else:
                    result[index + 1] = " "
                index += 2
            else:
                index += 1
            continue
        if content.startswith(delimiter, index):
            _mask_range(result, index, index + len(delimiter))
            index += len(delimiter)
            state = "code"
            continue
        if current == "\n":
            non_code_newlines.add(index)
        else:
            result[index] = " "
        index += 1

    return "".join(result), frozenset(non_code_newlines)


def _analyze_python(
    content: str,
    line_offsets: tuple[int, ...],
) -> tuple[str, tuple[tuple[str, int], ...]]:
    result = list(content)
    values: list[tuple[str, int, int]] = []
    stream = tokenize.generate_tokens(io.StringIO(content).readline)
    try:
        for observed in stream:
            if observed.type in {token.STRING, tokenize.COMMENT}:
                start = _python_offset(line_offsets, observed.start)
                end = _python_offset(line_offsets, observed.end)
                _mask_range(result, start, end)
            elif observed.type == token.NAME:
                values.append(
                    (
                        observed.string,
                        observed.start[0],
                        _python_offset(line_offsets, observed.start),
                    )
                )
    except (IndentationError, tokenize.TokenError) as exc:
        # Tokenization errors identify the last source position whose lexical
        # meaning is no longer provable. Preserve earlier observations and mask
        # the unsafe frontier through EOF.
        frontier = _python_error_frontier(exc, line_offsets, content)
        _mask_range(result, frontier, len(result))
        values = [item for item in values if item[2] < frontier]
    return "".join(result), tuple((name, line) for name, line, _ in values)


def _python_error_frontier(
    error: IndentationError | tokenize.TokenError,
    line_offsets: tuple[int, ...],
    content: str,
) -> int:
    position: tuple[int, int] | None = None
    if isinstance(error, tokenize.TokenError):
        if len(error.args) > 1:
            raw = error.args[1]
            if (
                isinstance(raw, tuple)
                and len(raw) == 2
                and all(isinstance(item, int) for item in raw)
            ):
                position = raw
    elif error.lineno is not None:
        position = (error.lineno, max(0, (error.offset or 1) - 1))
    if position is None:
        return 0
    frontier = min(len(content), _python_offset(line_offsets, position))
    if isinstance(error, tokenize.TokenError):
        for candidate in range(max(0, frontier - 2), frontier + 1):
            if content.startswith(('"""', "'''"), candidate):
                return candidate
    return frontier


def _line_offsets(content: str) -> tuple[int, ...]:
    return (0, *(index + 1 for index, char in enumerate(content) if char == "\n"))


def _python_offset(
    line_offsets: tuple[int, ...],
    position: tuple[int, int],
) -> int:
    line, column = position
    if line < 1:
        return 0
    if line - 1 >= len(line_offsets):
        return line_offsets[-1]
    return line_offsets[line - 1] + column


def _identifiers_by_line(
    observed: tuple[tuple[str, int], ...],
    line_count: int,
) -> tuple[tuple[str, ...], ...]:
    values: list[list[str]] = [[] for _ in range(max(1, line_count))]
    for identifier, line in observed:
        if 1 <= line <= len(values):
            values[line - 1].append(identifier)
    return tuple(tuple(line) for line in values)


def _mask_range(values: list[str], start: int, end: int) -> None:
    for index in range(start, min(end, len(values))):
        if values[index] != "\n":
            values[index] = " "


def _starts_regex_literal(
    content: str,
    index: int,
    previous_code_index: int | None,
    *,
    after_control_paren: bool,
) -> bool:
    following = content[index + 1] if index + 1 < len(content) else ""
    if following in {"", "=", "/", "*"}:
        return False
    if previous_code_index is None:
        return True
    if after_control_paren:
        return True
    cursor = previous_code_index
    previous = content[cursor]
    if previous in {"+", "-"} and _is_postfix_update(content, cursor, previous):
        return False
    if previous in "=([{!?:;,<>+-*%&|^~":
        return True
    if previous.isalnum() or previous in {"_", "$"}:
        end = cursor + 1
        while cursor >= 0 and (content[cursor].isalnum() or content[cursor] in {"_", "$"}):
            cursor -= 1
        return content[cursor + 1 : end] in {
            "await",
            "case",
            "delete",
            "do",
            "else",
            "in",
            "instanceof",
            "of",
            "return",
            "throw",
            "typeof",
            "void",
            "yield",
        }
    return False


def _is_postfix_update(content: str, index: int, operator: str) -> bool:
    if index < 1 or content[index - 1] != operator:
        return False
    cursor = index - 2
    while cursor >= 0 and content[cursor].isspace():
        cursor -= 1
    return cursor >= 0 and (
        content[cursor].isalnum() or content[cursor] in {"_", "$", ")", "]"}
    )


def _previous_identifier(content: str, index: int | None) -> str | None:
    if index is None or not (content[index].isalnum() or content[index] in {"_", "$"}):
        return None
    end = index + 1
    while index >= 0 and (content[index].isalnum() or content[index] in {"_", "$"}):
        index -= 1
    return content[index + 1 : end]
