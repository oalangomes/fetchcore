# FetchCore release and versioning policy

**Status:** active release automation policy; bootstrap is pending until `v0.0.1` is explicitly approved.

## First release: a single baseline

- Prior commits and merged PRs remain traceable in Git. Do **not** create retroactive version tags for each previous merge.
- The initial `v0.0.1` is triggered by a deliberate `workflow_dispatch` of **Publish FetchCore release**, only after the final technical/CI gates are satisfied.
- The workflow updates `pyproject.toml` and README from the current development version (`0.1.0.dev0` before bootstrap) to `0.0.1`, creates a release commit and requests CI for that commit.
- Only a successful CI run for that exact release commit permits the tag and GitHub Release. A stale CI result or mismatched declared version cannot authorize publication.
- Initial notes combine the curated [v0.0.1 technical overview](releases/initial-release.md) with GitHub-generated change history for merged PRs preceding the first tag. The overview is not a claim that all mentioned capabilities work in every backend.
- Neither merging a PR nor a green CI check **before** the first tag automatically publishes `v0.0.1`.

## After the bootstrap

Every successfully validated PR merged to `master` is eligible for an automated release; a direct push without an unambiguous merged PR does not trigger a version bump.

| PR label | Version behavior | Use |
| --- | --- | --- |
| None | Patch (e.g., `v0.0.1` → `v0.0.2`) | Ordinary changes, including fixes and compatible functionality |
| `release:minor` | Minor (e.g., `v0.0.2` → `v0.1.0`) | Deliberately grouped/new product capability |
| `release:major` | Major (e.g., `v0.1.0` → `v1.0.0`) | Explicitly authorized major compatibility milestone |
| `release:skip` | No release | Purely administrative changes that do not change user-visible behavior or contracts |

**Choose labels before merging.** At most one of `release:skip`, `release:minor` or `release:major` should be present; conflicting directives are rejected. Labels change *release selection*, not required CI, review or benchmark gates.

Use `release:skip` for changes such as housekeeping-only documentation formatting, issue templates, labels, or CI metadata that do not affect the tested product. **Do not** skip when the PR changes CLI/MCP/SDK behavior, retrieval semantics, indexing results, persistent schemas, compatibility, security, packaging, installation, or benchmarks that establish product claims. If uncertain, retain the default patch release.

GitHub-generated release notes group PRs using [`.github/release.yml`](../.github/release.yml) and omit `release:skip` PRs from the generated changelog. A skipped merge remains visible in Git history, and its changes are included in subsequent source snapshots.

## Release gates and provenance

- A version is an immutable, annotated tag pointing at a specific validated release commit, not a label for all work that happened since the last PR.
- Automated releases require a single identifiable merged PR, a matching `master` commit and a successful CI result for the corresponding SHA.
- Keep canonical repository/revision/source provenance and backend-neutral contracts intact; any retrieval improvement needs comparative evidence against the baseline.
- Do not force-push or silently publish from an incompatible schema, ambiguous commit, failed CI or tag/version mismatch.
- The release process never owns agent lifecycle, result satisfaction or authority.

The workflow is [`.github/workflows/release.yml`](../.github/workflows/release.yml). Initial publication is a manual product decision; releases after the bootstrap follow the rules above.
