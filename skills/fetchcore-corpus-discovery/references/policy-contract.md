# FetchCore policy contract

Contract baseline: FetchCore `e0a9a72adb2f8c7e056c5e9993fb5fa34e00e8f1`, schema 1.
The installed canonical loader is authoritative for compatibility, not this
reference. On another version, inspect its contract and validate before claiming
support. No YAML profile/preset or new domain schema is introduced by this skill.

Canonical implementation references (paths relative to a FetchCore checkout):
`src/fetchcore/repository/index_policy.py`, `indexing.py`, `safe_scan.py`,
`corpus_policy.py`, `knowledge_pack.py` and
`src/fetchcore/application/index_operations.py`.

## Supported fields

| Field | Semantics/constraints |
| --- | --- |
| `version: 1` | Only supported policy version at baseline |
| `selection.include` | List of globs, additive to default includes |
| `selection.exclude` | Additive exclusions; cannot re-enable permanent exclusions |
| `selection.replace_default_includes` | Boolean; true requires nonempty include list; may lose language coverage |
| `limits.max_files/max_file_bytes/max_total_bytes` | Positive integers; don't change without measured need |
| `chunking.max_lines/overlap_lines/markdown_max_chars` | Positive limits; overlap nonnegative and smaller than max_lines |
| `classification.rules` | Ordered `{pattern, knowledge_type}` records before defaults |
| `classification.replace_defaults` | Boolean; preserve unless explicitly justified |
| `technical_roles.rules/replace_defaults` | Existing `{token, role}` records; preserve |
| `extractors.enabled` | Optional unique language list; validate against installed extractor registry |
| `corpus.default_purpose` | `source-code`, `engineering-docs`, `product-docs`, `mixed`, `unknown` |
| `corpus.default_origin` | `authored`, `generated`, `runtime`, `mirrored`, `unknown` |
| `corpus.purpose` | Ordered `{pattern, value}` list from purpose vocabulary |
| `corpus.origin` | Ordered `{pattern, value}` list from origin vocabulary |

`needs_review`, confidence and recommendations belong in the report, never YAML.
`generated` is origin, not purpose, rank, authority or an exclusion requirement.
Corpus rules use first-match precedence independently of admission. Knowledge type
is a nonempty string in this contract; don't invent a semantic vocabulary merely
because the parser permits one.

The default scanner matches includes against basename OR relative path using
Python fnmatch; its `*` can span `/`. Excludes match path components, relative
globs, or the scanner's nested-directory form. This is not gitignore syntax.
Use the helper's reuse of canonical matching, not shell glob expansion.

Permanent default excludes at baseline include `.git`, `.fetchcore`, `.agentsorch`,
virtual environments, dependencies, build outputs, caches, and a `generated`
component. Report that existing default explicitly, while never recommending a
blanket generated exclusion yourself. Legitimate generated material under a
permanently excluded path requires a separate domain decision; a local include
cannot override it. Filename content guards in this skill are stricter than
generic runtime defaults; do not misrepresent them as an existing runtime guarantee.

Missing policy uses defaults. Legacy `.agentsorch/indexing.yaml` is supported;
both paths together are ambiguous and fail closed. Unknown keys, unsupported
versions, invalid values and YAML errors block proposals. Never rewrite a malformed
policy to defaults. Preserve comments and user order with a minimal textual diff.

The helper keeps validation inputs separate from public output. Invalid or
unvalidated YAML is never rendered: only source path, checksum, validation result
and safe error category/type are exposed. Valid non-sensitive YAML keeps its exact
text/comments for useful diffs. A conservative guard withholds the entire YAML,
resolved configured values and diff when credential labels/token/private-key
markers are recognized, including comments. It does not modify validation or
selection semantics. Impact paths remain visible as metadata observations.
Withheld text is null with an explicit visibility status, never a replacement
configuration; inspect the original privately for a separate human review.
The guard is not a general secret scanner and cannot prove arbitrary unlabeled
values or repository filenames non-sensitive. Do not print raw policy/parser
errors as a fallback or claim the guard detects every possible secret.

## KnowledgePacks

Use installed pack detection/configuration rather than new framework heuristics.
The built-in SAP Commerce pack is detected by explicit `manifest.json`
`commerceSuiteVersion`, `hybris/config/localextensions.xml`, `cloudextensions.xml`
or `hybris/bin/custom`. It never needs `local.properties` content.

With no explicit policy it conditionally adds `*.impex` and raises defaults to
at least 10000 files / 128000000 total bytes. With explicit policy these admission
and limit defaults are not applied. Sensitive key/config and unsupported binary
excludes still apply. A first explicit policy can therefore remove prior pack
coverage even if its only apparent change is one exclusion; list and preserve
needed pack includes/limits deliberately. These are conditional defaults, not a
catalog of named presets. Pack interfaces remain unchanged.

## Validation and operational evidence

`inventory.py discover` reads the current policy directly using the canonical
loader. `inventory.py review` validates the proposal in an external temporary
directory, reuses canonical pack precedence/matching, then
lists before/after metadata admission and bytes. It neither runs RepositoryIndexer
nor contacts a backend. Its eligibility precedes binary/API quarantine, decoding,
max_files and max_total_bytes. Inventory truncation means impact is partial.
`observation` and `impact` expose `complete`, `scope` and
`incompleteness_reasons`. A complete metadata observation is not evidence of
complete content inspection or of the number of documents actually indexed.

The helper needs an installed compatible FetchCore for canonical validation; this
is optional tooling, not a runtime dependency of the skill instructions. On a
different environment the coder may manually invoke a known installed validator.
Do not claim compatibility from YAML parsing alone. Extractor names also need
registry validation; failure is explicit.

Existing CLI surfaces have different scopes:

- `fetchcore repo inspect REPOSITORY --json` observes registry/source state.
- `fetchcore index status REPOSITORY --json` reads persisted compatibility.
- `fetchcore index plan REPOSITORY --json` can materialize/refresh an acquired
  workspace and perform extraction to calculate a plan.
- Legacy `index ... dry-run` is backend-connected and may perform full indexing
  computation, despite not persisting index mutations.

Prefer already-produced, target-verified read-only reports. Do not run these
automatically against the analyzed workspace or an unknown profile. Configuration
changes may change an indexing fingerprint and require an explicit rebuild later;
without a snapshot/fingerprint comparison that remains a risk, not a measured
operational verdict. No indexing, reindexing or full benchmark is part of this skill.
