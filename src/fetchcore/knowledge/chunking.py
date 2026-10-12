"""Deterministic structural chunking for non-code repository knowledge.

Code/test source chunking remains driven by language symbol extractors. This
module handles documentation and configuration formats where structural units
come from headings, top-level keys, documents, or XML child elements. Every
strategy degrades to deterministic line windows when structure cannot be
proven safely.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, replace
from itertools import pairwise
from urllib.parse import urlsplit

from fetchcore.knowledge.evidence import MarkdownReferenceEvidence, SectionEvidence


DEFAULT_MARKDOWN_MAX_CHARS = 48_000
_MARKDOWN_ATX_HEADING = re.compile(
    r"^ {0,3}(?P<markers>#{1,6})(?:[ \t]+(?P<title>.*?)|[ \t]*)$"
)
_MARKDOWN_SETEXT_HEADING = re.compile(r"^ {0,3}(?P<markers>=+|-+)[ \t]*$")
_MARKDOWN_FENCE_OPEN = re.compile(r"^ {0,3}(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_MARKDOWN_FENCE_CLOSE = re.compile(r"^ {0,3}(?P<marker>`+|~+)[ \t]*$")
_MARKDOWN_LIST_MARKER = re.compile(r"^(?P<indent> {0,3})(?:[-+*]|\d+[.)])[ \t]+")
_MARKDOWN_LINK = re.compile(r"!?\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))")
_MARKDOWN_LINK_DEFINITION = re.compile(
    r"^ {0,3}\[[^\]]+\]:[ \t]*(?:<([^>]+)>|(\S+))"
)
_MARKDOWN_AUTOLINK = re.compile(r"<(https?://[^>\s]+)>", re.IGNORECASE)
_MARKDOWN_INLINE_CODE = re.compile(r"`+[^`\n]*`+")
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
    section: SectionEvidence | None = None
    diagnostics: tuple[str, ...] = ()


@dataclass(frozen=True)
class _MarkdownHeading:
    start_line: int
    marker_line: int
    level: int
    title: str


@dataclass(frozen=True)
class _MarkdownBlock:
    start_line: int
    end_line: int
    kind: str
    language: str | None = None


def chunks_for_knowledge(
    content: str,
    *,
    language: str | None,
    knowledge_type: str,
    max_lines: int,
    overlap: int,
    max_chars: int = DEFAULT_MARKDOWN_MAX_CHARS,
) -> tuple[KnowledgeChunk, ...]:
    """Choose a structural strategy by canonical knowledge type and format."""

    if max_lines < 1 or max_chars < 1:
        raise ValueError("Markdown chunk limits must be positive")
    if overlap < 0 or overlap >= max_lines:
        raise ValueError("overlap must be non-negative and smaller than max_lines")
    if not content.strip():
        return ()
    if knowledge_type == "documentation" and language == "markdown":
        chunks = _markdown_chunks(
            content,
            max_lines=max_lines,
            overlap=overlap,
            max_chars=max_chars,
        )
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
    max_chars: int,
) -> tuple[KnowledgeChunk, ...]:
    lines = content.splitlines(keepends=True)
    headings = _markdown_headings(lines)
    if not headings:
        references, languages = _markdown_observations(lines, 1, len(lines))
        section = SectionEvidence(
            strategy="lines",
            kind="markdown-document",
            references=references,
            code_languages=languages,
        )
        return _pack_markdown_blocks(
            lines,
            1,
            len(lines),
            max_lines=max_lines,
            overlap=overlap,
            max_chars=max_chars,
            prefix="lines",
            strategy="lines",
            section=section,
        )

    chunks: list[KnowledgeChunk] = []
    if headings[0].start_line > 1:
        references, languages = _markdown_observations(
            lines,
            1,
            headings[0].start_line - 1,
        )
        section = SectionEvidence(
            strategy="markdown-preamble",
            kind="markdown-preamble",
            references=references,
            code_languages=languages,
        )
        chunks.extend(
            _pack_markdown_blocks(
                lines,
                1,
                headings[0].start_line - 1,
                max_lines=max_lines,
                overlap=overlap,
                max_chars=max_chars,
                prefix="markdown:preamble",
                strategy="markdown-preamble",
                section=section,
            )
        )

    stack: list[tuple[int, str, str]] = []
    occurrences: Counter[tuple[str, int, str]] = Counter()
    for index, heading in enumerate(headings):
        while stack and stack[-1][0] >= heading.level:
            stack.pop()
        parent_id = stack[-1][2] if stack else "root"
        title_key = unicodedata.normalize("NFKC", heading.title).casefold()
        occurrence_key = (parent_id, heading.level, title_key)
        occurrence = occurrences[occurrence_key]
        occurrences[occurrence_key] += 1
        section_id, fragment_id = _markdown_section_identity(
            parent_id,
            heading.level,
            title_key,
            occurrence,
            heading.title,
        )
        stack.append((heading.level, heading.title, section_id))
        end = (
            headings[index + 1].start_line - 1
            if index + 1 < len(headings)
            else len(lines)
        )
        references, languages = _markdown_observations(
            lines,
            heading.start_line,
            end,
        )
        section = SectionEvidence(
            strategy="markdown-heading",
            kind="heading",
            name=heading.title,
            heading_level=heading.level,
            heading_path=tuple(item[1] for item in stack),
            heading_levels=tuple(item[0] for item in stack),
            section_id=section_id,
            fragment_id=fragment_id,
            code_languages=languages,
            references=references,
        )
        chunks.extend(
            _pack_markdown_blocks(
                lines,
                heading.start_line,
                end,
                max_lines=max_lines,
                overlap=overlap,
                max_chars=max_chars,
                prefix=f"markdown:section:{section_id}",
                strategy="markdown-heading",
                section=section,
            )
        )
    return tuple(chunks)


def _markdown_headings(lines: list[str]) -> list[_MarkdownHeading]:
    headings: list[_MarkdownHeading] = []
    fence: tuple[str, int] | None = None
    paragraph_start: int | None = None
    paragraph_lines: list[str] = []
    consumed_until = 0

    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        if number <= consumed_until:
            continue
        if fence is not None:
            closing = _MARKDOWN_FENCE_CLOSE.match(line)
            if (
                closing is not None
                and closing.group("marker")[0] == fence[0]
                and len(closing.group("marker")) >= fence[1]
            ):
                fence = None
            continue

        opening = _markdown_fence_open(line)
        if opening is not None:
            fence = (opening[0], opening[1])
            paragraph_start = None
            paragraph_lines.clear()
            continue

        atx = _MARKDOWN_ATX_HEADING.match(line)
        if atx is not None:
            title = (atx.group("title") or "").strip()
            title = re.sub(r"[ \t]+#+[ \t]*$", "", title).strip()
            headings.append(
                _MarkdownHeading(number, number, len(atx.group("markers")), title)
            )
            paragraph_start = None
            paragraph_lines.clear()
            continue

        setext = _MARKDOWN_SETEXT_HEADING.match(line)
        if setext is not None and paragraph_start is not None and paragraph_lines:
            marker = setext.group("markers")
            title = " ".join(part.strip() for part in paragraph_lines).strip()
            headings.append(
                _MarkdownHeading(
                    paragraph_start,
                    number,
                    1 if marker.startswith("=") else 2,
                    title,
                )
            )
            consumed_until = number
            paragraph_start = None
            paragraph_lines.clear()
            continue

        if not line.strip() or _is_markdown_block_start(line):
            paragraph_start = None
            paragraph_lines.clear()
            continue
        if paragraph_start is None:
            paragraph_start = number
        paragraph_lines.append(line)

    return headings


def _markdown_fence_open(line: str) -> tuple[str, int, str | None] | None:
    match = _MARKDOWN_FENCE_OPEN.match(line)
    if match is None:
        return None
    marker = match.group("marker")
    info = match.group("info").strip()
    if marker[0] == "`" and "`" in info:
        return None
    language = info.split(maxsplit=1)[0].strip("{}") if info else None
    return marker[0], len(marker), language or None


def _is_markdown_block_start(line: str) -> bool:
    stripped = line.lstrip(" ")
    return (
        _MARKDOWN_ATX_HEADING.match(line) is not None
        or _MARKDOWN_FENCE_OPEN.match(line) is not None
        or stripped.startswith(">")
        or _MARKDOWN_LIST_MARKER.match(line) is not None
    )


def _markdown_section_identity(
    parent_id: str,
    level: int,
    normalized_title: str,
    occurrence: int,
    title: str,
) -> tuple[str, str]:
    identity = "\0".join((parent_id, str(level), normalized_title, str(occurrence)))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    section_id = f"mds-{digest[:20]}"
    slug = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug).strip("-").lower()[:40] or "section"
    return section_id, f"fc-{slug}-{digest[:12]}"


def _markdown_observations(
    lines: list[str],
    first: int,
    last: int,
) -> tuple[tuple[MarkdownReferenceEvidence, ...], tuple[str, ...]]:
    references: list[MarkdownReferenceEvidence] = []
    seen: set[tuple[str, str, int]] = set()
    languages: list[str] = []
    fence: tuple[str, int] | None = None

    for number in range(first, last + 1):
        line = lines[number - 1].rstrip("\r\n")
        if fence is not None:
            closing = _MARKDOWN_FENCE_CLOSE.match(line)
            if (
                closing is not None
                and closing.group("marker")[0] == fence[0]
                and len(closing.group("marker")) >= fence[1]
            ):
                fence = None
            continue

        opening = _markdown_fence_open(line)
        if opening is not None:
            fence = (opening[0], opening[1])
            if opening[2] is not None and opening[2] not in languages:
                languages.append(opening[2])
            continue

        searchable = _MARKDOWN_INLINE_CODE.sub("", line)
        matches = [
            _MARKDOWN_LINK_DEFINITION.match(searchable),
            *_MARKDOWN_LINK.finditer(searchable),
            *_MARKDOWN_AUTOLINK.finditer(searchable),
        ]
        for match in matches:
            if match is None:
                continue
            target = next(
                (group for group in match.groups() if isinstance(group, str) and group),
                None,
            )
            if target is None:
                continue
            reference = _safe_markdown_reference(target, number)
            if reference is None:
                continue
            key = (reference.target, reference.kind, number)
            if key not in seen:
                seen.add(key)
                references.append(reference)

    return tuple(references), tuple(languages)


def _safe_markdown_reference(
    target: str,
    line: int,
) -> MarkdownReferenceEvidence | None:
    if (
        not target
        or len(target) > 512
        or any(character.isspace() or ord(character) < 32 for character in target)
        or "\\" in target
    ):
        return None
    try:
        parsed = urlsplit(target)
    except ValueError:
        return None
    if parsed.query:
        return None
    if parsed.fragment and (
        len(parsed.fragment) > 128
        or any(
            not (character.isalnum() or character in "-_.~%/")
            for character in parsed.fragment
        )
    ):
        return None
    if parsed.scheme:
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        kind = "external"
    elif parsed.netloc:
        if parsed.username is not None or parsed.password is not None:
            return None
        kind = "external"
    elif not parsed.path and parsed.fragment:
        kind = "fragment"
    elif parsed.path:
        kind = "relative"
    else:
        return None
    return MarkdownReferenceEvidence(target=target, kind=kind, line=line)


def _markdown_block_ranges(
    lines: list[str],
    first: int,
    last: int,
) -> tuple[_MarkdownBlock, ...]:
    blocks: list[_MarkdownBlock] = []
    number = first
    pending_start: int | None = None

    while number <= last:
        raw = lines[number - 1].rstrip("\r\n")
        if not raw.strip():
            if blocks:
                previous = blocks[-1]
                blocks[-1] = _MarkdownBlock(
                    previous.start_line,
                    number,
                    previous.kind,
                    previous.language,
                )
            elif pending_start is None:
                pending_start = number
            number += 1
            continue

        start = pending_start if pending_start is not None else number
        pending_start = None
        opening = _markdown_fence_open(raw)
        if opening is not None:
            kind = "code_fence"
            language = opening[2]
            end = _markdown_fence_end(lines, number, last, opening[0], opening[1])
        elif _is_markdown_table(lines, number, last):
            kind = "table"
            language = None
            end = number + 1
            while end < last and _is_table_row(lines[end]):
                end += 1
        elif _MARKDOWN_LIST_MARKER.match(raw) is not None:
            kind = "list"
            language = None
            end = _markdown_list_end(lines, number, last)
        elif raw.lstrip(" ").startswith(">"):
            kind = "blockquote"
            language = None
            end = number
            while end < last and lines[end].lstrip(" ").startswith(">"):
                end += 1
        else:
            kind = "paragraph"
            language = None
            end = number
            while end < last:
                next_line = lines[end].rstrip("\r\n")
                if (
                    not next_line.strip()
                    or _is_markdown_block_start(next_line)
                    or _is_markdown_table(lines, end + 1, last)
                ):
                    break
                end += 1
        blocks.append(_MarkdownBlock(start, end, kind, language))
        number = end + 1

    return tuple(blocks)


def _markdown_fence_end(
    lines: list[str],
    first: int,
    last: int,
    marker: str,
    marker_length: int,
) -> int:
    for number in range(first + 1, last + 1):
        closing = _MARKDOWN_FENCE_CLOSE.match(lines[number - 1].rstrip("\r\n"))
        if (
            closing is not None
            and closing.group("marker")[0] == marker
            and len(closing.group("marker")) >= marker_length
        ):
            return number
    return last


def _is_markdown_table(lines: list[str], number: int, last: int) -> bool:
    if number >= last or "|" not in lines[number - 1]:
        return False
    delimiter = lines[number].strip().strip("|")
    cells = delimiter.split("|")
    return bool(cells) and all(
        re.fullmatch(r"[ \t]*:?-{3,}:?[ \t]*", cell) is not None for cell in cells
    )


def _is_table_row(line: str) -> bool:
    return bool(line.strip()) and "|" in line


def _markdown_list_end(lines: list[str], first: int, last: int) -> int:
    number = first
    while number < last:
        line = lines[number].rstrip("\r\n")
        if line.strip():
            number += 1
            continue
        next_number = number + 1
        while next_number < last and not lines[next_number].strip():
            next_number += 1
        if next_number < last and (
            _MARKDOWN_LIST_MARKER.match(lines[next_number]) is not None
            or lines[next_number].startswith((" ", "\t"))
        ):
            number = next_number
            continue
        break
    return number


def _pack_markdown_blocks(
    lines: list[str],
    first: int,
    last: int,
    *,
    max_lines: int,
    overlap: int,
    max_chars: int,
    prefix: str,
    strategy: str,
    section: SectionEvidence | None,
) -> tuple[KnowledgeChunk, ...]:
    if first > last:
        return ()
    blocks = _markdown_block_ranges(lines, first, last)
    if not blocks:
        return ()

    chunks: list[KnowledgeChunk] = []
    current: list[_MarkdownBlock] = []
    segment = 0

    def emit(
        selected: list[_MarkdownBlock],
        *,
        text: str | None = None,
        end_line: int | None = None,
        diagnostics: tuple[str, ...] = (),
    ) -> None:
        nonlocal segment
        if not selected:
            return
        start_line = selected[0].start_line
        last_line = selected[-1].end_line if end_line is None else end_line
        rendered = (
            "".join(lines[start_line - 1 : last_line])
            if text is None
            else text
        )
        if not rendered.strip():
            return
        chunk_section = section
        if chunk_section is not None and diagnostics:
            chunk_section = replace(
                chunk_section,
                diagnostics=tuple(
                    dict.fromkeys((*chunk_section.diagnostics, *diagnostics))
                ),
            )
        chunks.append(
            KnowledgeChunk(
                discriminator=f"{prefix}:segment:{segment}",
                content=rendered,
                line_start=start_line,
                line_end=max(start_line, last_line),
                strategy=strategy,
                section=chunk_section,
                diagnostics=diagnostics,
            )
        )
        segment += 1

    for block in blocks:
        block_lines = block.end_line - block.start_line + 1
        block_text = "".join(lines[block.start_line - 1 : block.end_line])
        if block_lines > max_lines or len(block_text) > max_chars:
            emit(current)
            current = []
            truncated, truncated_end = _truncate_markdown_block(
                lines,
                block,
                max_lines=max_lines,
                max_chars=max_chars,
            )
            diagnostics = (f"markdown-block-truncated:{block.kind}",)
            emit(
                [block],
                text=truncated,
                end_line=truncated_end,
                diagnostics=diagnostics,
            )
            continue

        candidate = [*current, block]
        if current and not _markdown_blocks_fit(
            candidate,
            lines=lines,
            max_lines=max_lines,
            max_chars=max_chars,
        ):
            previous = current
            emit(previous)
            current = _markdown_overlap_suffix(
                previous,
                lines=lines,
                next_block=block,
                overlap=overlap,
                max_lines=max_lines,
                max_chars=max_chars,
            )
        current.append(block)
    emit(current)
    return tuple(chunks)


def _markdown_blocks_fit(
    blocks: list[_MarkdownBlock],
    *,
    lines: list[str],
    max_lines: int,
    max_chars: int,
) -> bool:
    if not blocks:
        return True
    start = blocks[0].start_line
    end = blocks[-1].end_line
    return (
        end - start + 1 <= max_lines
        and len("".join(lines[start - 1 : end])) <= max_chars
    )


def _markdown_overlap_suffix(
    blocks: list[_MarkdownBlock],
    *,
    lines: list[str],
    next_block: _MarkdownBlock,
    overlap: int,
    max_lines: int,
    max_chars: int,
) -> list[_MarkdownBlock]:
    if overlap <= 0:
        return []
    selected: list[_MarkdownBlock] = []
    line_count = 0
    for block in reversed(blocks):
        block_lines = block.end_line - block.start_line + 1
        if line_count + block_lines > overlap:
            break
        selected.insert(0, block)
        line_count += block_lines
    while selected and not _markdown_blocks_fit(
        [*selected, next_block],
        lines=lines,
        max_lines=max_lines,
        max_chars=max_chars,
    ):
        selected.pop(0)
    return selected


def _truncate_markdown_block(
    lines: list[str],
    block: _MarkdownBlock,
    *,
    max_lines: int,
    max_chars: int,
) -> tuple[str, int]:
    kept: list[str] = []
    characters = 0
    end_line = block.start_line
    truncated = False
    for number in range(block.start_line, block.end_line + 1):
        if len(kept) >= max_lines or characters >= max_chars:
            truncated = True
            break
        line = lines[number - 1]
        remaining = max_chars - characters
        if len(line) > remaining:
            kept.append(line[:remaining])
            characters += remaining
            end_line = number
            truncated = True
            break
        kept.append(line)
        characters += len(line)
        end_line = number
    if not truncated and end_line < block.end_line:
        truncated = True
    if not kept and block.start_line <= block.end_line:
        kept.append(lines[block.start_line - 1][:max_chars])
        end_line = block.start_line
    return "".join(kept), end_line


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


# Preserve historical class paths used by serialized Python consumers.
KnowledgeChunk.__module__ = "fetchcore.repository.knowledge_chunking"
