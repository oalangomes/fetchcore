"""Conservative lexical helpers for shell repository knowledge.

Shell is intentionally treated as source text, never executed. The scanner masks
comments, quoted strings and heredoc bodies while preserving source geometry so
structural and relational extractors do not promote prose-like shell payloads.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_SHELL_WORD = re.compile(r"[A-Za-z_./][A-Za-z0-9_./:+@%-]*")
_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FUNCTION_HEADER = re.compile(
    r"^(?:function[ \t]+[A-Za-z_][A-Za-z0-9_]*(?:[ \t]*\(\s*\))?"
    r"|[A-Za-z_][A-Za-z0-9_]*[ \t]*\(\s*\))[ \t]*\{?"
)
_CONTROL_PREFIXES = frozenset({"if", "then", "elif", "else", "while", "until", "do", "!"})
_NON_CALL_HEADS = frozenset(
    {
        "case",
        "esac",
        "fi",
        "done",
        "for",
        "function",
        "select",
        "time",
        "coproc",
        "export",
        "readonly",
        "local",
        "declare",
        "typeset",
        "unset",
        "source",
        ".",
        "return",
        "break",
        "continue",
        "shift",
        "trap",
        "alias",
        "unalias",
        "set",
    }
)


@dataclass(frozen=True)
class _Heredoc:
    delimiter: str
    strip_tabs: bool


def analyze_shell_non_code(content: str) -> tuple[str, frozenset[int]]:
    """Mask shell comments, quoted literals and heredocs without changing geometry."""

    result = list(content)
    non_code_newlines: set[int] = set()
    pending: list[_Heredoc] = []
    state = "code"
    escaped = False
    offset = 0

    for raw_line in content.splitlines(keepends=True):
        line_body = raw_line.rstrip("\r\n")
        newline_start = offset + len(line_body)
        newline_length = len(raw_line) - len(line_body)

        if pending:
            current = pending[0]
            candidate = line_body.lstrip("\t") if current.strip_tabs else line_body
            if candidate == current.delimiter:
                pending.pop(0)
            _mask_non_newline(result, offset, offset + len(line_body))
            if newline_length:
                non_code_newlines.add(newline_start)
            offset += len(raw_line)
            continue

        heredocs: list[_Heredoc] = []
        index = 0
        while index < len(line_body):
            absolute = offset + index
            char = line_body[index]
            following = line_body[index + 1] if index + 1 < len(line_body) else ""

            if state == "single":
                result[absolute] = "0"
                if char == "'":
                    state = "code"
                index += 1
                continue

            if state in {"double", "backtick"}:
                result[absolute] = "0"
                delimiter = '"' if state == "double" else "`"
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == delimiter:
                    state = "code"
                index += 1
                continue

            if char == "#" and _starts_comment(line_body, index):
                _mask_non_newline(result, absolute, offset + len(line_body))
                break
            if char == "'":
                result[absolute] = "0"
                state = "single"
                index += 1
                continue
            if char == '"':
                result[absolute] = "0"
                state = "double"
                escaped = False
                index += 1
                continue
            if char == "`":
                result[absolute] = "0"
                state = "backtick"
                escaped = False
                index += 1
                continue
            if char == "\\" and index + 1 < len(line_body):
                result[absolute] = " "
                result[absolute + 1] = "0"
                index += 2
                continue
            if char == "<" and following == "<":
                observed = _heredoc_after(line_body, index)
                if observed is not None:
                    heredocs.append(observed)
                index += 2
                continue
            index += 1

        if heredocs and state == "code":
            pending.extend(heredocs)
        if state != "code" and newline_length:
            non_code_newlines.add(newline_start)
        offset += len(raw_line)

    return "".join(result), frozenset(non_code_newlines)


def extract_shell_static_sources(content: str) -> tuple[tuple[str, int], ...]:
    """Return literal ``source``/``.`` includes, rejecting dynamic expansions."""

    masked, _ = analyze_shell_non_code(content)
    values: list[tuple[str, int]] = []
    raw_lines = content.splitlines()
    masked_lines = masked.splitlines()
    for line_number, (raw, code) in enumerate(zip(raw_lines, masked_lines), start=1):
        match = re.match(r"^[ \t]*(?:source|\.)[ \t]+", code)
        if match is None:
            continue
        literal = _literal_shell_word(raw[match.end() :])
        if literal is not None:
            values.append((literal, line_number))
    return tuple(values)


def extract_shell_commands(content: str, *, line_start: int) -> tuple[tuple[str, int], ...]:
    """Extract statically named simple-command heads from one shell chunk."""

    masked, _ = analyze_shell_non_code(content)
    values: list[tuple[str, int]] = []
    for offset, line in enumerate(masked.splitlines()):
        line_number = line_start + offset
        for segment in re.split(r"(?:&&|\|\||[;|])", line):
            command = _command_head(segment)
            if command is not None:
                values.append((command, line_number))
    return tuple(values)


def _command_head(segment: str) -> str | None:
    value = segment.strip()
    if not value or _FUNCTION_HEADER.match(value):
        return None

    while True:
        token = _first_word(value)
        if token is None:
            return None
        word, end = token
        if word in _CONTROL_PREFIXES:
            value = value[end:].lstrip()
            continue
        break

    if word in _NON_CALL_HEADS or word in {"{", "}"}:
        return None

    while _ASSIGNMENT.match(value):
        space = re.search(r"[ \t]+", value)
        if space is None:
            return None
        value = value[space.end() :].lstrip()
        token = _first_word(value)
        if token is None:
            return None
        word, _ = token

    if word in _NON_CALL_HEADS or word.startswith("$"):
        return None
    return word


def _first_word(value: str) -> tuple[str, int] | None:
    match = _SHELL_WORD.match(value)
    if match is None:
        return None
    return match.group(0), match.end()


def _literal_shell_word(value: str) -> str | None:
    rendered = value.lstrip()
    if not rendered:
        return None
    if rendered[0] in {"'", '"'}:
        quote = rendered[0]
        closing = rendered.find(quote, 1)
        if closing < 0:
            return None
        literal = rendered[1:closing]
        if quote == '"' and any(token in literal for token in ("$", "`", "\\")):
            return None
        return literal or None

    match = re.match(r"([^\s;&|]+)", rendered)
    if match is None:
        return None
    literal = match.group(1)
    if any(token in literal for token in ("$", "`", "*", "?", "[", "]", "{", "}", "(", ")")):
        return None
    return literal or None


def _heredoc_after(line: str, index: int) -> _Heredoc | None:
    tail = line[index:]
    match = re.match(
        r"<<(?P<strip>-?)[ \t]*(?:(?P<quote>['\"])(?P<quoted>[^'\"]+)(?P=quote)"
        r"|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))",
        tail,
    )
    if match is None:
        return None
    delimiter = match.group("quoted") or match.group("plain")
    return _Heredoc(delimiter=delimiter, strip_tabs=bool(match.group("strip")))


def _starts_comment(line: str, index: int) -> bool:
    if index == 0:
        return True
    return line[index - 1].isspace() or line[index - 1] in ";|&(){}"


def _mask_non_newline(result: list[str], start: int, end: int) -> None:
    for index in range(start, min(end, len(result))):
        if result[index] not in {"\n", "\r"}:
            result[index] = " "
