from __future__ import annotations

from pathlib import Path

import pytest

from fetchcore.knowledge.chunking import chunks_for_knowledge
from fetchcore.knowledge.evidence import (
    KnowledgeEvidence,
    MarkdownReferenceEvidence,
    SectionEvidence,
)
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.index_policy import load_repository_index_policy


def _markdown(
    content: str,
    *,
    max_lines: int = 160,
    overlap: int = 20,
    max_chars: int = 48_000,
):
    return chunks_for_knowledge(
        content,
        language="markdown",
        knowledge_type="documentation",
        max_lines=max_lines,
        overlap=overlap,
        max_chars=max_chars,
    )


def test_atx_and_setext_headings_capture_hierarchy_and_unicode() -> None:
    chunks = _markdown(
        """Arquitetura técnica
====================

## Seção interna

### Retrieval

#### Ranking

##### Evidence

###### Provenance
"""
    )

    evidence = [chunk.section for chunk in chunks if chunk.section is not None]
    assert [item.heading_level for item in evidence] == [1, 2, 3, 4, 5, 6]
    assert evidence[0].name == "Arquitetura técnica"
    assert evidence[-1].heading_levels == (1, 2, 3, 4, 5, 6)
    assert evidence[-1].heading_path == (
        "Arquitetura técnica",
        "Seção interna",
        "Retrieval",
        "Ranking",
        "Evidence",
        "Provenance",
    )
    assert all(item.section_id and item.fragment_id for item in evidence)
    assert all(item.fragment_id.startswith("fc-") for item in evidence)


def test_duplicate_headings_have_distinct_branch_scoped_stable_ids() -> None:
    content = """# Architecture

## Retrieval

### Details

First branch.

## Operations

### Details

Second branch.
"""
    original = _markdown(content)
    repeated = _markdown(content)
    details = [
        chunk.section
        for chunk in original
        if chunk.section is not None and chunk.section.name == "Details"
    ]
    repeated_details = [
        chunk.section
        for chunk in repeated
        if chunk.section is not None and chunk.section.name == "Details"
    ]

    assert len(details) == 2
    assert details[0].heading_path == ("Architecture", "Retrieval", "Details")
    assert details[1].heading_path == ("Architecture", "Operations", "Details")
    assert details[0].section_id != details[1].section_id
    assert tuple(item.section_id for item in details) == tuple(
        item.section_id for item in repeated_details
    )


def test_repeated_sibling_headings_have_distinct_ordinals() -> None:
    chunks = _markdown(
        """# Guide

## Example

First example.

## Example

Second example.
"""
    )
    examples = [
        chunk.section
        for chunk in chunks
        if chunk.section is not None and chunk.section.name == "Example"
    ]

    assert len(examples) == 2
    assert examples[0].heading_path == examples[1].heading_path == ("Guide", "Example")
    assert examples[0].section_id != examples[1].section_id
    assert examples[0].fragment_id != examples[1].fragment_id


def test_empty_sections_and_headingless_markdown_are_retained() -> None:
    sections = _markdown("#\n\n## Next\n\n")
    headings = [chunk.section for chunk in sections if chunk.section is not None]
    assert len(headings) == 2
    assert headings[0].name == ""
    assert headings[0].heading_path == ("",)
    assert headings[1].heading_path == ("", "Next")
    assert sections[0].content.startswith("#")

    plain = _markdown("A headingless document.\n\nSecond paragraph.\n")
    assert len(plain) == 1
    assert plain[0].strategy == "lines"
    assert plain[0].content == "A headingless document.\n\nSecond paragraph.\n"
    assert plain[0].section is not None
    assert plain[0].section.kind == "markdown-document"


def test_headingless_documents_still_record_code_languages_and_links() -> None:
    chunk = _markdown(
        """See [the specification](../api/openapi.yaml#/paths/~1widgets).

```yaml
enabled: true
```
"""
    )[0]

    assert chunk.section is not None
    assert chunk.section.kind == "markdown-document"
    assert chunk.section.code_languages == ("yaml",)
    assert chunk.section.references[0].target == "../api/openapi.yaml#/paths/~1widgets"
    assert chunk.section.references[0].resolution == "observed"


def test_preamble_has_its_own_exact_source_span() -> None:
    chunks = _markdown("Introductory context.\n\n# First section\n\nBody.\n")

    assert len(chunks) == 2
    assert chunks[0].strategy == "markdown-preamble"
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 2
    assert chunks[0].content == "Introductory context.\n\n"
    assert chunks[1].line_start == 3


@pytest.mark.parametrize(
    ("opening", "closing", "language"),
    [
        ("```python", "```", "python"),
        ("~~~~yaml", "~~~~", "yaml"),
    ],
)
def test_fenced_headings_are_ignored_and_language_is_observed(
    opening: str,
    closing: str,
    language: str,
) -> None:
    chunks = _markdown(
        f"""# Real section

{opening}
# Not a section
{closing}

## Another real section
"""
    )
    sections = [chunk.section for chunk in chunks if chunk.section is not None]

    assert [item.name for item in sections] == ["Real section", "Another real section"]
    assert sections[0].code_languages == (language,)
    assert "Not a section" in chunks[0].content


def test_unclosed_fence_keeps_following_heading_text_inside_the_fence() -> None:
    chunks = _markdown(
        """# Example

```python
print("open fence")
## not a heading
"""
    )

    assert len(chunks) == 1
    assert chunks[0].section is not None
    assert chunks[0].section.name == "Example"
    assert "## not a heading" in chunks[0].content


def test_small_tables_and_fences_are_never_split_within_budget() -> None:
    content = """# Structured examples

| Field | Meaning |
| --- | --- |
| route | API path |

```python
print("complete fence")
```
"""
    chunks = _markdown(content, max_lines=12, overlap=2, max_chars=512)

    assert len(chunks) == 1
    assert "| route | API path |" in chunks[0].content
    assert '```python\nprint("complete fence")\n```' in chunks[0].content
    assert chunks[0].line_start == 1
    assert chunks[0].line_end == 9
    assert chunks[0].section is not None
    assert chunks[0].section.code_languages == ("python",)


def test_nested_lists_remain_a_single_unit_when_within_budget() -> None:
    content = """# Nested list

- parent item
  - child item
    1. grandchild item
"""
    chunks = _markdown(content, max_lines=8, overlap=0)

    assert len(chunks) == 1
    assert "- parent item\n  - child item\n    1. grandchild item" in chunks[0].content


def test_large_atomic_blocks_are_bounded_and_report_truncation() -> None:
    content = """# Large code sample

```python
one = 1
two = 2
three = 3
four = 4
five = 5
six = 6
```
"""
    chunks = _markdown(content, max_lines=5, overlap=0, max_chars=80)

    assert all(len(chunk.content.splitlines()) <= 5 for chunk in chunks)
    assert all(len(chunk.content) <= 80 for chunk in chunks)
    truncated = [chunk for chunk in chunks if chunk.diagnostics]
    assert len(truncated) == 1
    assert truncated[0].diagnostics == ("markdown-block-truncated:code_fence",)
    assert truncated[0].section is not None
    assert truncated[0].section.diagnostics == truncated[0].diagnostics

    table = """# Wide table

| Name | Meaning |
| --- | --- |
| first | row one |
| second | row two |
| third | row three |
| fourth | row four |
| fifth | row five |
"""
    table_chunks = _markdown(table, max_lines=4, overlap=0, max_chars=200)
    table_truncations = [
        chunk for chunk in table_chunks if "markdown-block-truncated:table" in chunk.diagnostics
    ]
    assert len(table_truncations) == 1
    assert len(table_truncations[0].content.splitlines()) <= 4
    assert "| first | row one |" in table_truncations[0].content
    assert "| fifth | row five |" not in table_truncations[0].content

    long_line = _markdown("x" * 100, max_lines=2, overlap=0, max_chars=12)
    assert len(long_line) == 1
    assert len(long_line[0].content) == 12
    assert long_line[0].diagnostics == ("markdown-block-truncated:paragraph",)
    assert long_line[0].line_start == long_line[0].line_end == 1


def test_links_are_observed_only_and_sensitive_destinations_are_not_typed() -> None:
    content = """# References

[relative](../docs/API.md#operation)
[fragment](#local-section)
[external](https://example.test/guide)
[query](https://example.test/search?token=FIXTURE_SENTINEL)
[credentials](https://user:password@example.test/private)
`[code](https://example.test/ignored)`
```markdown
[fenced](https://example.test/also-ignored)
```
"""
    chunk = _markdown(content)[0]
    assert chunk.section is not None
    references = chunk.section.references
    assert [(item.kind, item.target) for item in references] == [
        ("relative", "../docs/API.md#operation"),
        ("fragment", "#local-section"),
        ("external", "https://example.test/guide"),
    ]
    assert all(item.resolution == "observed" for item in references)
    assert [item.line for item in references] == [3, 4, 5]
    serialized = chunk.section.to_mapping()
    assert "FIXTURE_SENTINEL" not in str(serialized)
    assert "password" not in str(serialized)
    assert "FIXTURE_SENTINEL" in chunk.content


def test_reference_definitions_are_observed_without_resolution() -> None:
    chunk = _markdown(
        """# Reference style

See [the API contract][api-doc].

[api-doc]: ../api/openapi.yaml#/paths/~1widgets
"""
    )[0]

    assert chunk.section is not None
    assert len(chunk.section.references) == 1
    reference = chunk.section.references[0]
    assert reference.target == "../api/openapi.yaml#/paths/~1widgets"
    assert reference.kind == "relative"
    assert reference.resolution == "observed"
    assert reference.line == 5


def test_evidence_round_trip_preserves_markdown_section_contract() -> None:
    chunk = _markdown(
        """## Arquitetura

Read [the guide](../guide.md#part).

```yaml
enabled: true
```
"""
    )[0]
    assert chunk.section is not None
    restored = KnowledgeEvidence.from_mapping(
        KnowledgeEvidence(section=chunk.section).to_mapping()
    )

    assert restored.section == chunk.section
    legacy = SectionEvidence(strategy="markdown-heading", kind="heading", name="Legacy")
    assert SectionEvidence.from_mapping(legacy.to_mapping()) == legacy
    assert legacy.to_mapping() == {
        "strategy": "markdown-heading",
        "kind": "heading",
        "name": "Legacy",
    }


def test_markdown_reference_contract_rejects_unbounded_or_unsafe_targets() -> None:
    for target, kind in (
        ("https://example.test/guide?token=FIXTURE", "external"),
        ("https://user:password@example.test/guide", "external"),
        ("#not a safe fragment", "fragment"),
        ("../docs\\guide.md", "relative"),
    ):
        with pytest.raises(ValueError):
            MarkdownReferenceEvidence(target=target, kind=kind)


def test_indexing_preserves_section_ids_checksums_and_source_spans(
    tmp_path: Path,
) -> None:
    path = tmp_path / "README.md"
    path.write_text(
        """# Guide

## Alpha

Unchanged alpha section.

## Beta

Original beta body.

## Gamma

Unchanged gamma section.
""",
        encoding="utf-8",
    )
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(repository="example/markdown", revision="rev-a")
    )
    first = indexer.index(tmp_path)
    first_by_name = {
        document.evidence.section.name: document
        for document in first.documents
        if document.evidence.section is not None
    }

    assert first_by_name["Beta"].line_start == 7
    assert first_by_name["Beta"].line_end == 10
    assert first_by_name["Beta"].provenance.line_start == 7
    assert first_by_name["Beta"].provenance.line_end == 10
    unchanged = indexer.index(tmp_path, previous_documents=first.documents)
    assert unchanged.changes.added == ()
    assert unchanged.changes.updated == ()
    assert unchanged.changes.removed == ()
    assert len(unchanged.changes.unchanged) == len(first.documents)

    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Original beta body.", "Updated beta body."
        ),
        encoding="utf-8",
    )
    changed = indexer.index(tmp_path, previous_documents=first.documents)
    changed_by_name = {
        document.evidence.section.name: document
        for document in changed.documents
        if document.evidence.section is not None
    }
    for name in ("Alpha", "Gamma"):
        assert changed_by_name[name].id == first_by_name[name].id
        assert changed_by_name[name].checksum == first_by_name[name].checksum
    assert changed_by_name["Beta"].id == first_by_name["Beta"].id
    assert changed_by_name["Beta"].checksum != first_by_name["Beta"].checksum


def test_markdown_budget_must_be_positive() -> None:
    with pytest.raises(ValueError, match="Markdown chunk limits"):
        _markdown("# Heading\n", max_chars=0)
    with pytest.raises(ValueError, match="repository indexing limits"):
        RepositoryIndexConfig(max_markdown_chunk_chars=0)


def test_policy_round_trip_exposes_markdown_character_budget(tmp_path: Path) -> None:
    policy_dir = tmp_path / ".fetchcore"
    policy_dir.mkdir()
    (policy_dir / "indexing.yaml").write_text(
        "version: 1\nchunking:\n  markdown_max_chars: 1234\n",
        encoding="utf-8",
    )

    policy = load_repository_index_policy(tmp_path)

    assert policy.config.max_markdown_chunk_chars == 1234
    chunking = policy.to_mapping()["chunking"]
    assert isinstance(chunking, dict)
    assert chunking["markdown_max_chars"] == 1234
