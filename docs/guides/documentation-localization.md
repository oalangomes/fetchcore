# Documentation localization

English Markdown is canonical. PT-BR files are derived, reviewable projections. The tooling lives in `scripts/docs/`, `docs/i18n/`, and `.github/workflows/docs-localization.yml`; it is independent of FetchCore runtime and release publication.

## Inclusion policy

| Canonical area | Class | PT-BR target | Reason |
| --- | --- | --- | --- |
| Root and docs indexes | TRANSLATE | `README.pt-BR.md`, `docs/pt-BR/README.md` | Language navigation |
| Architecture, concepts, guides, reference, ADRs | TRANSLATE | Same relative path under `docs/pt-BR/` | Current explanatory or durable documentation |
| `docs/release-policy.md` | TRANSLATE | `docs/pt-BR/release-policy.md` | Current release policy |
| `docs/benchmarks/protocol.md` | TRANSLATE | `docs/pt-BR/benchmarks/protocol.md` | Current experiment convention |
| Other benchmark documents and results | HISTORICAL | None | Frozen protocol/results or point-in-time experiment evidence |
| Migrations and history/extraction | HISTORICAL | None | Historical records |
| Release notes | RELEASE_SPECIFIC | None | Point-in-time curated release evidence |
| PT-BR output and machine-readable data | GENERATED / EXCLUDE | None | Never translation inputs |

Run `python scripts/docs/localize.py inventory` to audit the current Markdown classification. The class is decided by code, not by a translation provider. New Markdown in included directories is automatically included; a new top-level area requires an explicit policy change.

## Identity and validation

Each generated file starts with an HTML comment containing its canonical path, locale, SHA-256 of the exact source bytes, schema version, glossary SHA-256, provider/model identity, translation fingerprint, and body SHA-256. The fingerprint combines source hash, locale, schema, glossary hash, provider, and model. A matching fingerprint skips translation. Changed inputs and `--force` regenerate. Removed or renamed sources cause their generated targets to be removed. Files are only written after every required provider response validates; the workflow commits only after all document checks pass.

The glossary is [glossary.pt-BR.yaml](../i18n/glossary.pt-BR.yaml). Product identifiers and selected technical terms stay in English. The tooling masks fenced code, inline code, link destinations, URLs, paths, environment names, commands, options, versions, issue numbers, SHAs, and glossary-preserved terms before sending prose to the provider. Missing or duplicated placeholders fail validation. Links are rewritten by code to a translated counterpart when included; other links point to canonical English. Translated headings carry their canonical anchor IDs, so existing fragments remain stable. `scripts/check_docs.py` checks links and anchors, including the PT-BR tree.

Run `python scripts/docs/localize.py check` in ordinary CI. It validates generated file integrity and reports stale, orphan, and missing translations without blocking a normal English documentation PR before localization runs. `python scripts/docs/localize.py check --strict` requires a current complete projection and is used by the localization workflow. `make docs-check` checks canonical Markdown links and index reachability. After generation, the localization workflow uses `python scripts/check_docs.py --include-localizations` to validate every PT-BR link and anchor too. The ordinary CI never calls a translation service.

## Automation and recovery

After a canonical change reaches `master`, the dedicated workflow restores any unmerged PT-BR files from `automation/docs-pt-br`, recomputes against current `master`, and pushes one review branch. It creates or updates one PR titled `docs: sync PT-BR translations`. PT-BR-only paths do not trigger the workflow. A no-op makes no commit. Manual `workflow_dispatch` runs incrementally by default; its `force` input rebuilds all included translations. The workflow needs repository Actions permission to create pull requests; enable **Allow GitHub Actions to create and approve pull requests** in repository settings if GitHub rejects PR creation. It explicitly dispatches ordinary CI for the review branch because GitHub limits workflows triggered by `GITHUB_TOKEN` activity; this is the reason for `actions: write` permission.

Configure repository secret `FETCHCORE_DOCS_TRANSLATION_API_KEY` and variables `FETCHCORE_DOCS_TRANSLATION_API_URL` (HTTPS Chat Completions endpoint) and `FETCHCORE_DOCS_TRANSLATION_MODEL`. The endpoint and model are explicit so provider/model changes invalidate old fingerprints. The request contains only bounded protected document prose, translation instructions, and the glossary; it never includes GitHub credentials or the process environment. External calls have a 60-second timeout and at most three attempts for transient network, 429, or 5xx failures. A provider or validation failure fails the workflow; canonical CI and release remain independent.

Review the PR for semantic fidelity, technical terms, natural PT-BR, links, and formatting. Generated output is evidence for review, not automatically trusted. Correct ambiguous English in the canonical file; correct recurring translation errors through the glossary or translation instructions, then regenerate. Do not maintain PT-BR files independently. A provider outage leaves the canonical docs valid and the localization run visibly failed. For offline pipeline testing, `python scripts/docs/localize.py sync --fake --root <temporary-copy>` uses a deterministic identity translator; it is not suitable for a published PT-BR translation.
