"""Geometry-preserving lexical hygiene for Dart source."""

from __future__ import annotations


def analyze_dart_non_code(content: str) -> tuple[str, frozenset[int]]:
    """Mask Dart comments and string literals without interpreting code.

    The baseline deliberately masks complete string literals, including interpolation.
    That can under-report identifiers referenced only from interpolation, but it prevents
    string prose from entering exact-identifier evidence. Dart has no regex literal syntax,
    so division operators are left untouched.
    """

    result = list(content)
    non_code_newlines: set[int] = set()
    index = 0
    state = "code"
    delimiter = ""
    raw = False
    block_depth = 0

    while index < len(content):
        current = content[index]
        following = content[index + 1] if index + 1 < len(content) else ""

        if state == "code":
            if current == "/" and following == "/":
                _mask(result, index, index + 2)
                index += 2
                state = "line-comment"
                continue
            if current == "/" and following == "*":
                _mask(result, index, index + 2)
                index += 2
                block_depth = 1
                state = "block-comment"
                continue

            raw_start = (
                current in {"r", "R"}
                and index + 1 < len(content)
                and content[index + 1] in {"'", '"'}
                and (index == 0 or not _identifier_char(content[index - 1]))
            )
            if raw_start:
                quote = content[index + 1]
                triple = content.startswith(quote * 3, index + 1)
                delimiter = quote * (3 if triple else 1)
                raw = True
                _mask(result, index, index + 1 + len(delimiter))
                index += 1 + len(delimiter)
                state = "string"
                continue

            if current in {"'", '"'}:
                delimiter = current * (3 if content.startswith(current * 3, index) else 1)
                raw = False
                _mask(result, index, index + len(delimiter))
                index += len(delimiter)
                state = "string"
                continue

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
            if current == "/" and following == "*":
                _mask(result, index, index + 2)
                block_depth += 1
                index += 2
                continue
            if current == "*" and following == "/":
                _mask(result, index, index + 2)
                block_depth -= 1
                index += 2
                if block_depth == 0:
                    state = "code"
                continue
            if current == "\n":
                non_code_newlines.add(index)
            else:
                result[index] = " "
            index += 1
            continue

        if not raw and current == "\\":
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
            _mask(result, index, index + len(delimiter))
            index += len(delimiter)
            state = "code"
            delimiter = ""
            raw = False
            continue
        if current == "\n":
            non_code_newlines.add(index)
            if len(delimiter) == 1:
                state = "code"
                delimiter = ""
                raw = False
        else:
            result[index] = " "
        index += 1

    return "".join(result), frozenset(non_code_newlines)


def _identifier_char(value: str) -> bool:
    return value.isalnum() or value in {"_", "$"}


def _mask(values: list[str], start: int, end: int) -> None:
    for position in range(start, min(end, len(values))):
        if values[position] != "\n":
            values[position] = " "
