"""Deterministic structural chunking for non-code repository knowledge.

Code/test source chunking remains driven by language symbol extractors. This
module handles documentation and configuration formats where structural units
come from headings, top-level keys, documents, or XML child elements. Every
strategy degrades to deterministic line windows when structure cannot be
proven safely.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise


_MARKDOWN_HEADING = re.compile(r"^(?: {0,3})(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_JSON_KEY = re.compile(r'(?m)^[ \t]*"((?:\\.|[^"\\])*)"[ \t]*:')
_YAML_KEY = re.compile(r"^(?P<key>[^#\s][^:#]*|\"[^\"]+\"|'[^']+')[ \t]*:")
_XML_TOKEN = re.compile(r"<!--[\s\S]*?-->|<!\[CDATA\[[\s\S]*?\]\]>|<[^>]+>")
_XML_NAME = re.compile(r"^</?\s*([A-Za-z_][A-Za-z0-9_.:-]*)")


@dataclass(frozen=True)
class KnowledgeChunk:
    """One non-code chunk with an observable strategy and stable discriminator."""

    discriminator: str
    content: str
    line_start: int
    line_end: int
    strategy: str


def chunks_for_knowledge(
    content: str,
    *,
    language: str | None,
    knowledge_type: str,
    max_lines: int,
    overlap: int,
) -> tuple[KnowledgeChunk, ...]:
    """Choose a structural strategy by canonical knowledge type and format."""

    if not content.strip():
        return ()
    if knowledge_type == "documentation" and language == "markdown":
        chunks = _markdown_chunks(content, max_lines=max_lines, overlap=overlap)
        if chunks:
            return chunks
    if knowledge_type == "configuration":
        if language == "json":
            chunks = _json_chunks(content, max_lines=max_lines, overlap=overlap)
            if chunks:
                return chunks
        elif language == "yaml":
            chunks = _yaml_chunks(content, max_lines=max_lines, overlap=overlap)
            if chunks:
                return chunks
        elif language == "xml":
            chunks = _xml_chunks(content, max_lines=max_lines, overlap=overlap)
            if chunks:
                return chunks
    lines = content.splitlines(keepends=True)
    return _line_chunks(
        lines,
        1,
        len(lines),
        max_lines=max_lines,
        overlap=overlap,
        prefix="lines",
        strategy="lines",
    )


def _markdown_chunks(
    content: str,
    *,
    max_lines: int,
    overlap: int,
) -> tuple[KnowledgeChunk, ...]:
    lines = content.splitlines(keepends=True)
    headings: list[tuple[int, int, str]] = []
    for number, line in enumerate(lines, start=1):
        match = _MARKDOWN_HEADING.match(line.rstrip("\r\n"))
        if match is None:
            continue
        headings.append((number, len(match.group(1)), match.group(2).strip()))
    if not headings:
        return ()

    chunks: list[KnowledgeChunk] = []
    if headings[0][0] > 1:
        chunks.extend(
            _line_chunks(
                lines,
                1,
                headings[0][0] - 1,
                max_lines=max_lines,
                overlap=overlap,
                prefix="markdown:preamble",
                strategy="markdown-preamble",
            )
        )

    occurrences: Counter[tuple[int, str]] = Counter()
    for index, (start, level, title) in enumerate(headings):
        end = headings[index + 1][0] - 1 if index + 1 < len(headings) else len(lines)
        key = (level, title.casefold())
        occurrence = occurrences[key]
        occurrences[key] += 1
        chunks.extend(
            _line_chunks(
                lines,
                start,
                end,
                max_lines=max_lines,
                overlap=overlap,
                prefix=(
                    f"markdown:heading:{level}:{_discriminator_text(title)}:"
                    f"occurrence:{occurrence}"
                ),
                strategy="markdown-heading",
            )
        )
    return tuple(chunks)


def _json_chunks(
    content: str,
    *,
    max_lines: int,
    overlap: int,
) -> tuple[KnowledgeChunk, ...]:
    try:
        decoded = json.loads(content)
    except json.JSONDecodeError:
        return ()
    if not isinstance(decoded, dict) or not decoded:
        return ()

    lines = content.splitlines(keepends=True)
    masked = _mask_strings(content)
    depths = _brace_depths(masked)
    starts: list[tuple[int, str]] = []
    for match in _JSON_KEY.finditer(content):
        if depths[match.start()] != 1:
            continue
        try:
            key = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            return ()
        line = _line_number(content, match.start())
        starts.append((line, str(key)))
    if len(starts) != len(decoded) or len({line for line, _ in starts}) != len(starts):
        return ()

    closing_line = _json_root_closing_line(content, masked)
    if closing_line is None or closing_line <= starts[-1][0]:
        return ()

    chunks: list[KnowledgeChunk] = []
    if starts[0][0] > 1:
        chunks.extend(
            _line_chunks(
                lines,
                1,
                starts[0][0] - 1,
                max_lines=max_lines,
                overlap=overlap,
                prefix="json:object:preamble",
                strategy="json-object",
            )
        )

    occurrences: Counter[str] = Counter()
    for index, (start, key) in enumerate(starts):
        next_start = starts[index + 1][0] if index + 1 < len(starts) else closing_line
        end = next_start - 1
        occurrence = occurrences[key.casefold()]
        occurrences[key.casefold()] += 1
        chunks.extend(
            _line_chunks(
                lines,
                start,
                end,
                max_lines=max_lines,
                overlap=overlap,
                prefix=f"json:key:{_discriminator_text(key)}:occurrence:{occurrence}",
                strategy="json-key",
            )
        )

    chunks.extend(
        _line_chunks(
            lines,
            closing_line,
            len(lines),
            max_lines=max_lines,
            overlap=overlap,
            prefix="json:object:closing",
            strategy="json-object",
        )
    )
    return tuple(chunks)


def _yaml_chunks(
    content: str,
    *,
    max_lines: int,
    overlap: int,
) -> tuple[KnowledgeChunk, ...]:
    lines = content.splitlines(keepends=True)
    documents = _yaml_document_ranges(lines)
    chunks: list[KnowledgeChunk] = []
    found_key = False
    key_occurrences: Counter[str] = Counter()

    for document_index, (first, last) in enumerate(documents):
        keys: list[tuple[int, str]] = []
        for number in range(first, last + 1):
            raw = lines[number - 1].rstrip("\r\n")
            if raw.startswith((" ", "\t")) or raw.lstrip().startswith("#"):
                continue
            if raw.strip() in {"", "---", "..."}:
                continue
            match = _YAML_KEY.match(raw)
            if match is None:
                continue
            key = match.group("key").strip().strip("\"'")
            if key:
                keys.append((number, key))
        if not keys:
            chunks.extend(
                _line_chunks(
                    lines,
                    first,
                    last,
                    max_lines=max_lines,
                    overlap=overlap,
                    prefix=f"yaml:document:{document_index}:fallback",
                    strategy="yaml-document",
                )
            )
            continue
        found_key = True
        if keys[0][0] > first:
            chunks.extend(
                _line_chunks(
                    lines,
                    first,
                    keys[0][0] - 1,
                    max_lines=max_lines,
                    overlap=overlap,
                    prefix=f"yaml:document:{document_index}:preamble",
                    strategy="yaml-document",
                )
            )
        for index, (start, key) in enumerate(keys):
            end = keys[index + 1][0] - 1 if index + 1 < len(keys) else last
            occurrence = key_occurrences[key.casefold()]
            key_occurrences[key.casefold()] += 1
            chunks.extend(
                _line_chunks(
                    lines,
                    start,
                    end,
                    max_lines=max_lines,
                    overlap=overlap,
                    prefix=(
                        f"yaml:key:{_discriminator_text(key)}:"
                        f"occurrence:{occurrence}:document:{document_index}"
                    ),
                    strategy="yaml-key",
                )
            )
    return tuple(chunks) if found_key else ()


def _xml_chunks(
    content: str,
    *,
    max_lines: int,
    overlap: int,
) -> tuple[KnowledgeChunk, ...]:
    if "<!DOCTYPE" in content.upper():
        return ()
    char_ranges = _xml_direct_child_ranges(content)
    if char_ranges is None or not char_ranges:
        return ()
    line_ranges = [
        (_line_number(content, start), _line_number(content, max(start, end - 1)), name)
        for start, end, name in char_ranges
    ]
    if any(start > end for start, end, _ in line_ranges):
        return ()
    for previous, current in pairwise(line_ranges):
        if previous[1] >= current[0]:
            return ()

    lines = content.splitlines(keepends=True)
    chunks: list[KnowledgeChunk] = []
    cursor = 1
    occurrences: Counter[str] = Counter()
    for index, (start, end, name) in enumerate(line_ranges):
        if cursor < start:
            chunks.extend(
                _line_chunks(
                    lines,
                    cursor,
                    start - 1,
                    max_lines=max_lines,
                    overlap=overlap,
                    prefix=f"xml:envelope:region:{index}",
                    strategy="xml-envelope",
                )
            )
        occurrence = occurrences[name.casefold()]
        occurrences[name.casefold()] += 1
        chunks.extend(
            _line_chunks(
                lines,
                start,
                end,
                max_lines=max_lines,
                overlap=overlap,
                prefix=f"xml:element:{_discriminator_text(name)}:occurrence:{occurrence}",
                strategy="xml-element",
            )
        )
        cursor = end + 1
    if cursor <= len(lines):
        chunks.extend(
            _line_chunks(
                lines,
                cursor,
                len(lines),
                max_lines=max_lines,
                overlap=overlap,
                prefix=f"xml:envelope:region:{len(line_ranges)}",
                strategy="xml-envelope",
            )
        )
    return tuple(chunks)


def _line_chunks(
    lines: list[str],
    first: int,
    last: int,
    *,
    max_lines: int,
    overlap: int,
    prefix: str,
    strategy: str,
) -> tuple[KnowledgeChunk, ...]:
    if first > last or first < 1:
        return ()
    chunks: list[KnowledgeChunk] = []
    start = first
    segment = 0
    while start <= last:
        end = min(last, start + max_lines - 1)
        rendered = "".join(lines[start - 1 : end])
        if rendered.strip():
            chunks.append(
                KnowledgeChunk(
                    discriminator=f"{prefix}:segment:{segment}",
                    content=rendered,
                    line_start=start,
                    line_end=end,
                    strategy=strategy,
                )
            )
        if end >= last:
            break
        start = end - overlap + 1
        segment += 1
    return tuple(chunks)


def _yaml_document_ranges(lines: list[str]) -> tuple[tuple[int, int], ...]:
    if not lines:
        return ()
    starts = [
        number
        for number, line in enumerate(lines, start=1)
        if line.strip() == "---"
    ]
    if not starts:
        return ((1, len(lines)),)
    ranges: list[tuple[int, int]] = []
    if starts[0] > 1:
        ranges.append((1, starts[0] - 1))
    for index, start in enumerate(starts):
        end = starts[index + 1] - 1 if index + 1 < len(starts) else len(lines)
        ranges.append((start, end))
    return tuple(ranges)


def _mask_strings(content: str) -> str:
    chars = list(content)
    quote: str | None = None
    escaped = False
    for index, char in enumerate(chars):
        if quote is None:
            if char == '"':
                quote = char
                chars[index] = " "
            continue
        if escaped:
            if char != "\n":
                chars[index] = " "
            escaped = False
            continue
        if char == "\\":
            chars[index] = " "
            escaped = True
            continue
        if char == quote:
            chars[index] = " "
            quote = None
            continue
        if char != "\n":
            chars[index] = " "
    return "".join(chars)


def _brace_depths(masked: str) -> list[int]:
    depths = [0] * (len(masked) + 1)
    depth = 0
    for index, char in enumerate(masked):
        depths[index] = depth
        if char in "{[":
            depth += 1
        elif char in "}]":
            depth = max(0, depth - 1)
    depths[len(masked)] = depth
    return depths


def _json_root_closing_line(content: str, masked: str) -> int | None:
    depth = 0
    for index, char in enumerate(masked):
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return _line_number(content, index)
    return None


def _xml_direct_child_ranges(
    content: str,
) -> tuple[tuple[int, int, str], ...] | None:
    stack: list[str] = []
    active: tuple[str, int] | None = None
    ranges: list[tuple[int, int, str]] = []
    root_closed = False
    root_seen = False
    for match in _XML_TOKEN.finditer(content):
        stripped = match.group(0).lstrip()
        if stripped.startswith(("<!--", "<![CDATA[", "<?")):
            continue
        if stripped.startswith("<!"):
            return None
        name_match = _XML_NAME.match(stripped)
        if name_match is None:
            return None
        name = name_match.group(1)
        closing = stripped.startswith("</")
        self_closing = stripped.rstrip().endswith("/>")
        if closing:
            if not stack or stack[-1] != name:
                return None
            if len(stack) == 2:
                if active is None or active[0] != name:
                    return None
                ranges.append((active[1], match.end(), name))
                active = None
            stack.pop()
            if not stack:
                root_closed = True
            continue
        if root_closed:
            return None
        if not root_seen:
            root_seen = True
            if self_closing:
                root_closed = True
            else:
                stack.append(name)
            continue
        if not stack:
            return None
        if len(stack) == 1:
            if self_closing:
                ranges.append((match.start(), match.end(), name))
            else:
                active = (name, match.start())
        if not self_closing:
            stack.append(name)
    if stack or not root_seen or not root_closed or active is not None:
        return None
    return tuple(ranges)


def _line_number(content: str, index: int) -> int:
    return content.count("\n", 0, max(0, index)) + 1


def _discriminator_text(value: str) -> str:
    rendered = " ".join(value.split()).strip()
    if not rendered:
        return "unnamed"
    return rendered[:120]
