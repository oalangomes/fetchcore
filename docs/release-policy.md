# FetchCore release and versioning policy

**Status:** active versioning policy for the initial `v0.0.1` bootstrap and subsequent releases.

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

## Candidate artifacts and publication

For a release commit, the publication workflow downloads `python-distributions`
and the complete installed-artifact matrix from the successful CI run that
triggered it. It rejects source/version/checksum mismatches, missing runtimes or
failed console/MCP checks before publication. A successful functional self-dogfood
run for the same SHA is also required. The version-preparation step explicitly
dispatches both workflows because pushes made with `GITHUB_TOKEN` do not trigger
new workflows automatically.

The release container uses `Dockerfile.release`: it installs the verified wheel
without rebuilding FetchCore from source, includes the MCP extra, carries OCI
source/revision/version labels and runs as UID 10001. Before a registry push it
must pass the same import/version/CLI/runtime/MCP/configset smoke as the Python
artifacts, plus the non-root check.

All three candidates are published as GitHub Release assets using the job-scoped
`GITHUB_TOKEN`. The container is a Docker image archive
`fetchcore-v<version>-container.tar`, tagged `fetchcore:v<version>`; consumers use
`docker load --input <archive>`. The exported archive is loaded and smoked before
publication. No new package registry or publication secret is required.
The image ID (its configuration digest), archive/Python artifact SHA-256 checksums,
runtime versions, dependency inventories and smoke observations are retained in
`release-evidence.json`, with `SHA256SUMS`. An image ID is not a registry manifest
digest; this pipeline does not claim or fabricate a registry digest. This evidence
establishes traceability, not artifact trust or retrieval quality.

The GitHub Release stays draft until all assets are attached. A failed validation
or upload cannot mark it complete. Retries encountering an existing release fail
explicitly and never overwrite immutable assets.
A partial publication can leave an immutable Git tag or draft;
an operator must inspect and reconcile that state before retrying. Publication
does not deploy or modify OPERATIONAL backend resources.

Source implementation and local smoke evidence do not close the operational
acceptance of #132/#135/#136: the approved first publication still needs an actual
successful workflow run and a GitHub Release containing the validated assets.

The workflow is [`.github/workflows/release.yml`](../.github/workflows/release.yml). Initial publication is a manual product decision; releases after the bootstrap follow the rules above.
