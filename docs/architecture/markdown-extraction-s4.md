# Technical Markdown extraction — S4

**Status: current.** S4 extends the existing Markdown chunker for traceable
technical sections. It remains deterministic, dependency-light, and part of
canonical repository indexing; it does not introduce a Markdown runtime or
backend-specific representation.

## Sections and locators

The scanner recognizes ATX and Setext H1–H6 headings outside fenced code blocks.
It records the observed heading ancestry, including empty headings and sections
with no body. Text before the first heading is a preamble; headingless Markdown
continues through the existing line-unit path.

Each heading has a stable `section_id` scoped by its parent section and the
ordinal of same-title siblings. Duplicate names in other branches do not collide.
`fragment_id` is a deterministic, URL-safe FetchCore locator; it is **not** a
claim about GitHub, CommonMark, or another renderer's heading-slug convention.
Existing renderer fragments are preserved as source references and are not
rewritten.

Canonical documents retain their exact one-based source line span, repository,
revision, source path, and checksum. A section split into multiple bounded
documents retains the same section identity and heading path in each document.

## Bounded structural units

Markdown extraction preserves fenced code (including a declared language),
tables, lists, block quotes, and paragraphs as blocks while packing them within
both `max_lines` and `markdown_max_chars`. A code fence or table that fits is
kept intact. An oversized block is deterministically truncated at a source-line
or character boundary; the canonical `chunk_diagnostics` metadata and typed
section evidence include `markdown-block-truncated:<kind>`. No omitted source
tail is represented as if it had been indexed.

Default repository policy:

```yaml
version: 1
chunking:
  max_lines: 160
  overlap_lines: 20
  markdown_max_chars: 48000
```

`markdown_max_chars` is a character count, not a token estimate. It is a
Markdown-only limit; JSON, YAML, XML, source-code, and test chunkers retain their
existing algorithms.

## Links and safety

`SectionEvidence.references` records only conservatively parsed relative,
fragment, and HTTP(S) destinations. Entries carry the source line and
`resolution: observed`; S4 does not test file existence, resolve fragments,
follow links, fetch remote content, execute examples, or interpolate values.
Destinations containing a query string, URL user-info, whitespace, or unsafe
fragment characters are omitted from this typed evidence. A safe recorded
destination is still only a source observation, not proof that it resolves.

This filter is **not** a universal secrets detector. Generic Markdown content
continues to be indexed as source text under the existing repository scan
policy; S4 does not claim to sanitize arbitrary tokens, credentials, code
samples, or front matter. API artifact sanitizers and quarantine behavior from
S0–S3 are unchanged.

## Identity and compatibility

The Markdown unit boundaries and discriminators change under
`INDEXING_CONTRACT_VERSION = 25`. Existing snapshots with the prior indexing
fingerprint are incompatible and require the existing explicit rebuild path.
S4 does not change Projection v3 or its fingerprint, and it does not silently
reuse or rewrite persisted indexes.

The Markdown parser is a lightweight scanner; no third-party parser dependency
was added. Its conservative block recognition is covered by synthetic fixtures
and tests, not a claim of full CommonMark conformance.

## Measurement

The frozen before/after fixture, ground truth, runner, and recorded results are
in the [S4 benchmark protocol](../../benchmarks/markdown-extraction-s4/README.md).
Extraction recovery and locator precision are reported separately from the
benchmark's intentionally simple fixture ranker. The ranker is not a production
retrieval backend; no production Recall@K or ranking improvement is claimed.
