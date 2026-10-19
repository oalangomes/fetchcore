from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fetchcore.knowledge.chunking import (
    MAX_MARKDOWN_EVIDENCE_BYTES,
    _bound_markdown_evidence,
    chunks_for_knowledge,
)
from fetchcore.knowledge.evidence import (
    KnowledgeEvidence,
    MarkdownReferenceEvidence,
    SectionEvidence,
)
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.repository.index_policy import load_repository_index_policy
from fetchcore.projection import (
    ProjectionV3CanonicalDocumentReader,
    ProjectionV3Compiler,
)


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


def test_references_are_scoped_to_each_emitted_chunk() -> None:
    chunks = _markdown(
        "# Guide\n\nIntro.\n\n[api](../api.md)\n",
        max_lines=3,
        overlap=0,
    )

    assert [(chunk.line_start, chunk.line_end) for chunk in chunks] == [(1, 2), (3, 5)]
    assert chunks[0].section is not None
    assert chunks[1].section is not None
    assert chunks[0].section.section_id == chunks[1].section.section_id
    assert chunks[0].section.references == ()
    assert len(chunks[1].section.references) == 1
    reference = chunks[1].section.references[0]
    assert reference.line == 5
    assert chunks[1].line_start <= reference.line <= chunks[1].line_end
    assert reference.target in chunks[1].content


def test_links_in_a_truncated_table_are_not_published() -> None:
    content = """# Links

| Name | Reference |
| --- | --- |
| first | [kept](first.md) |
| second | [discarded](later.md) |
| third | [also-discarded](last.md) |
"""
    chunks = _markdown(content, max_lines=4, overlap=0, max_chars=256)
    table_chunk = next(
        chunk for chunk in chunks if "markdown-block-truncated:table" in chunk.diagnostics
    )

    assert table_chunk.section is not None
    assert [reference.target for reference in table_chunk.section.references] == [
        "first.md",
        "later.md",
    ]
    assert all(
        table_chunk.line_start <= reference.line <= table_chunk.line_end
        and reference.target in table_chunk.content
        for reference in table_chunk.section.references
    )


def test_partial_destination_at_character_boundary_is_not_observed() -> None:
    chunks = _markdown(
        "# Partial link\n\nprefix [partial](later.md)\n",
        max_lines=4,
        overlap=0,
        max_chars=18,
    )
    truncated = next(
        chunk for chunk in chunks if "markdown-block-truncated:paragraph" in chunk.diagnostics
    )

    assert truncated.section is not None
    assert truncated.section.references == ()
    assert len(truncated.content) <= 18
    assert "later.md" not in truncated.content


def test_two_links_in_different_segments_keep_original_lines() -> None:
    content = (
        "# References\n\n"
        "[first](first.md)\n\n"
        + "".join(f"Paragraph {number}.\n\n" for number in range(8))
        + "[second](second.md)\n"
    )
    chunks = _markdown(content, max_lines=4, overlap=0)

    observations = [
        (index, reference)
        for index, chunk in enumerate(chunks)
        if chunk.section is not None
        for reference in chunk.section.references
    ]
    first = [(index, ref) for index, ref in observations if ref.target == "first.md"]
    second = [(index, ref) for index, ref in observations if ref.target == "second.md"]
    assert len(first) == len(second) == 1
    assert first[0][0] != second[0][0]
    for index, reference in observations:
        chunk = chunks[index]
        assert chunk.line_start <= reference.line <= chunk.line_end
        assert reference.target in chunk.content


def test_code_language_is_only_attached_to_the_chunk_containing_its_fence() -> None:
    content = """# Examples

Introductory text.

Additional prose that fills an earlier chunk.

```yaml
enabled: true
```
"""
    chunks = _markdown(content, max_lines=4, overlap=0)
    language_chunks = [
        chunk
        for chunk in chunks
        if chunk.section is not None and chunk.section.code_languages
    ]

    assert len(language_chunks) == 1
    assert language_chunks[0].section is not None
    assert language_chunks[0].section.code_languages == ("yaml",)
    assert "```yaml" in language_chunks[0].content


def test_truncated_fence_does_not_publish_discarded_links_or_long_languages() -> None:
    long_language = "x" * 200
    content = (
        "# Examples\n\n"
        f"```{long_language}\n"
        "retained line\n"
        "[discarded](outside.md)\n"
        "```\n"
    )
    chunks = _markdown(content, max_lines=3, overlap=0, max_chars=512)
    truncated = next(
        chunk for chunk in chunks if "markdown-block-truncated:code_fence" in chunk.diagnostics
    )

    assert truncated.section is not None
    assert truncated.section.references == ()
    assert truncated.section.code_languages == ()
    assert "markdown-code-language-omitted" in truncated.section.diagnostics
    assert all(
        reference.target in truncated.content
        and truncated.line_start <= reference.line <= truncated.line_end
        for reference in truncated.section.references
    )


def test_overlapping_chunks_repeat_only_observations_in_the_shared_lines() -> None:
    chunks = _markdown(
        """# Links

[overlap](shared.md)

First paragraph.

Second paragraph.
""",
        max_lines=4,
        overlap=2,
    )
    occurrences = [
        chunk
        for chunk in chunks
        if chunk.section is not None
        and any(reference.target == "shared.md" for reference in chunk.section.references)
    ]

    assert len(occurrences) == 2
    assert all("shared.md" in chunk.content for chunk in occurrences)
    assert all(
        any(
            chunk.line_start <= reference.line <= chunk.line_end
            for reference in chunk.section.references
            if reference.target == "shared.md"
        )
        for chunk in occurrences
        if chunk.section is not None
    )


def test_many_references_are_capped_and_section_evidence_is_bounded() -> None:
    links = "\n".join(f"[reference {number}](ref-{number}.md)" for number in range(1_000))
    chunk = _markdown(f"# Many links\n\n{links}\n", max_lines=1_200, overlap=0)[0]

    assert chunk.section is not None
    assert len(chunk.section.references) == 16
    assert "markdown-references-budget-exceeded" in chunk.section.diagnostics
    evidence_bytes = len(
        json.dumps(
            chunk.section.to_mapping(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    assert evidence_bytes <= MAX_MARKDOWN_EVIDENCE_BYTES
    assert all(reference.target in chunk.content for reference in chunk.section.references)


def test_aggregate_evidence_overflow_omits_variable_fields_with_diagnostic() -> None:
    oversized = SectionEvidence(
        strategy="markdown-heading",
        kind="heading",
        name="H" * (MAX_MARKDOWN_EVIDENCE_BYTES * 2),
        heading_level=1,
        heading_path=("H" * (MAX_MARKDOWN_EVIDENCE_BYTES * 2),),
        heading_levels=(1,),
        section_id="mds-stable",
        fragment_id="fc-stable",
    )

    bounded, exceeded = _bound_markdown_evidence(oversized)

    assert exceeded is True
    assert bounded.name is None
    assert bounded.heading_path == ()
    assert bounded.heading_levels == ()
    assert bounded.diagnostics == ("markdown-evidence-budget-exceeded",)
    assert len(
        json.dumps(
            bounded.to_mapping(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ) <= MAX_MARKDOWN_EVIDENCE_BYTES


def test_repeated_links_on_one_line_are_not_duplicated() -> None:
    chunk = _markdown("# Repeated\n\n[link](same.md) then [link](same.md)\n")[0]

    assert chunk.section is not None
    assert len(chunk.section.references) == 1
    assert chunk.section.references[0].target == "same.md"
    assert chunk.section.references[0].line == 3


def test_multiple_headings_keep_only_local_links_and_fence_languages() -> None:
    chunks = _markdown(
        """# Guide

## First

[first](first.md)

```python
print(1)
```

## Second

[second](second.md)

```yaml
enabled: true
```
"""
    )
    first = next(chunk for chunk in chunks if chunk.section and chunk.section.name == "First")
    second = next(chunk for chunk in chunks if chunk.section and chunk.section.name == "Second")

    assert [item.target for item in first.section.references] == ["first.md"]
    assert first.section.code_languages == ("python",)
    assert [item.target for item in second.section.references] == ["second.md"]
    assert second.section.code_languages == ("yaml",)


def test_oversized_headings_and_ancestry_are_omitted_from_bounded_evidence(
    tmp_path: Path,
) -> None:
    huge_title = "A" * 100_000
    document_path = tmp_path / "README.md"
    document_path.write_text(f"# {huge_title}\n\nbody\n", encoding="utf-8")
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(
            repository="example/oversized-heading",
            revision="oversized",
            max_markdown_chunk_chars=100,
        )
    )
    document = indexer.index(tmp_path).documents[0]

    assert document.evidence.section is not None
    assert len(document.content) <= 100
    assert document.evidence.section.name is None
    assert document.evidence.section.heading_path == ()
    assert "markdown-heading-omitted" in document.evidence.section.diagnostics
    section_bytes = len(
        json.dumps(
            document.evidence.section.to_mapping(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    projection = ProjectionV3Compiler().compile(document)
    canonical_payload = str(projection.fields["canonical_payload"])
    assert section_bytes <= MAX_MARKDOWN_EVIDENCE_BYTES
    assert len(canonical_payload.encode("utf-8")) < 16_384
    assert document.provenance.source_checksum == (
        "sha256:" + hashlib.sha256(document_path.read_bytes()).hexdigest()
    )

    deep = _markdown(
        "\n".join(f"{'#' * level} {'T' * 200}" for level in range(1, 7)) + "\n"
    )[-1]
    assert deep.section is not None
    assert deep.section.heading_path == ()
    assert deep.section.heading_levels == ()
    assert "markdown-heading-ancestry-omitted" in deep.section.diagnostics


def test_indexing_near_the_default_file_limit_keeps_evidence_bounded(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "large.md"
    source_path.write_text(
        "# " + ("H" * 200_000) + "\n\n" + ("x" * 789_000) + "\n",
        encoding="utf-8",
    )
    assert source_path.stat().st_size < 1_000_000
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(
            repository="example/near-file-limit",
            revision="near-limit",
            max_total_bytes=2_000_000,
            max_markdown_chunk_chars=256,
        )
    )
    result = indexer.index(tmp_path)

    assert result.telemetry.files_read == 1
    assert result.documents
    for document in result.documents:
        assert document.evidence.section is not None
        section_bytes = len(
            json.dumps(
                document.evidence.section.to_mapping(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        assert section_bytes <= MAX_MARKDOWN_EVIDENCE_BYTES
        assert len(document.content) <= 256


def test_links_and_evidence_round_trip_through_projection_v3(tmp_path: Path) -> None:
    source_path = tmp_path / "guide.md"
    source_path.write_text(
        "# Guide\n\nIntro.\n\n[api](../api.md)\n\n```yaml\nenabled: true\n```\n",
        encoding="utf-8",
    )
    indexer = RepositoryIndexer(
        RepositoryIndexConfig(repository="example/projection", revision="rev-evidence")
    )
    document = indexer.index(tmp_path).documents[-1]
    assert document.evidence.section is not None
    assert document.evidence.section.references
    restored_evidence = KnowledgeEvidence.from_mapping(document.evidence.to_mapping())
    assert restored_evidence == document.evidence

    record = ProjectionV3Compiler().compile(document)
    restored = ProjectionV3CanonicalDocumentReader().read(record.to_mapping())

    assert restored.evidence == document.evidence
    assert restored.provenance == document.provenance
    assert restored.checksum == document.checksum
    assert restored.revision == document.revision


def test_markdown_identity_and_provenance_are_deterministic_across_runs(
    tmp_path: Path,
) -> None:
    source = tmp_path / "README.md"
    source.write_text("# Guide\n\n[api](../api.md)\n\n## Details\n\nContent.\n", encoding="utf-8")
    config = RepositoryIndexConfig(repository="example/stable", revision="rev-stable")
    first = RepositoryIndexer(config, clock=lambda: "fixed-time").index(tmp_path)
    second = RepositoryIndexer(config, clock=lambda: "fixed-time").index(tmp_path)

    assert [item.id for item in first.documents] == [item.id for item in second.documents]
    assert [item.chunk_ref for item in first.documents] == [
        item.chunk_ref for item in second.documents
    ]
    assert [item.provenance for item in first.documents] == [
        item.provenance for item in second.documents
    ]
    assert [item.evidence for item in first.documents] == [
        item.evidence for item in second.documents
    ]
    assert first.documents[0].provenance.source_checksum == (
        "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()
    )
