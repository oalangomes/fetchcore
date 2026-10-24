# CI workflows

FetchCore separates pull-request validation, candidate self-dogfood, post-merge
operational indexing, optional localization, and publication.

## Trigger topology

```text
pull request
    ↓
CI on GitHub-hosted runners
    +
FetchCore self dogfood
    ↓
ephemeral Solr
    ↓
fetchcore-self-dogfood
    ↓
rebuild candidate revision → MCP evaluation → evidence → cleanup

master push
    ↓
CI on GitHub-hosted runners
    ↓ successful master run only
independent Solr, OpenSearch and Qdrant OPERATIONAL workflows
    ↓
reuse each backend's persistent OPERATIONAL resource
    ↓
plan → incremental sync | noop → readiness
```

Pull requests never write to persistent OPERATIONAL resources.
`.github/workflows/self-dogfood.yml` runs the candidate revision against an
owned ephemeral Solr runtime and explicitly rebuilds the disposable
`fetchcore-self-dogfood` resource. The run cleans up only resources it owns.

The [Solr](../../.github/workflows/operational-index.yml),
[OpenSearch](../../.github/workflows/operational-opensearch.yml) and
[Qdrant](../../.github/workflows/operational-qdrant.yml) workflows accept only a
successful `push` CI run for `master` from the FetchCore repository, or an
explicit manual dispatch. Each uses the immutable `head_sha` for post-CI runs
and only its own persistent OPERATIONAL resource. Automatic runs require existing
compatible snapshots, perform `sync` and verify readiness; missing or incompatible
schema/fingerprints fail closed without an implicit rebuild. Manual dispatch from
`master` offers `index_lifecycle: plan | sync | rebuild` with `sync` as the default.
Inspect a plan run before explicitly authorizing a separate rebuild run; schema
and ownership checks remain mandatory. See
[manual operational recovery](self-hosted-indexing.md#manual-operational-recovery).
All default to
hybrid retrieval, allow explicit dense selection, and use Ollama
`embeddinggemma` at 768 dimensions. See the
[operational acceptance](self-hosted-indexing.md#operational-acceptance-2026-10-20).

The repository does not choose the self-hosted runner boot policy. The default
operational labels include `fetchcore`; override
`FETCHCORE_INDEX_RUNNER_LABELS_JSON` only with repository-specific affinity.
Pull-request code stays on GitHub-hosted runners.

Localization is independently gated by
`FETCHCORE_DOCS_LOCALIZATION_ENABLED=true`.

## Python and distribution contract

Supported runtimes are ordinary CPython 3.11, 3.12, 3.13, 3.14 and 3.15 on Linux.
`requires-python = ">=3.11,<3.16"` bounds this declared set. Future minors,
alternative interpreters and free-threaded builds require separate validation.
The test and installed-artifact matrices cover the same set. Lint, typecheck,
tests/coverage, build, artifact smoke and runtime-devex have separate jobs.
Coverage uses the single 48% threshold in `pyproject.toml`; an individual matrix
failure remains visible because matrix execution does not fail fast.

The build job produces one wheel and sdist, retained as `python-distributions`.
Each artifact-smoke job downloads those exact files and installs each in a
separate temporary environment, without editable installation, source import
shadowing or pip wheel-cache reuse. The sdist must build successfully there.
Smoke checks include package version/import, bundled Solr configset, CLI help,
runtime capabilities, and a bounded MCP initialize/list-tools exchange.
No backend service is needed for these packaging checks.

`artifact-smoke-<minor>` evidence records artifact SHA-256, full source SHA,
installed version, Python version, checks and a dependency name/version inventory.
It excludes private paths, pip `direct_url`, environment variables and credentials.
The inventory is an equivalent dependency inventory, not a signed SBOM or a
vulnerability assessment. Dependency resolution is recorded per candidate;
byte-for-byte rebuild reproducibility across future dependency releases is not
claimed. `SOURCE_DATE_EPOCH` fixes the build's source timestamp.

The self-dogfood workflow also runs on `master` pushes. The release workflow
requires its functional success for the exact candidate SHA. Protocol/runtime
failures block publication; report-only retrieval relevance measurements remain
separate. Repository branch protection should require CI matrix jobs and
self-dogfood explicitly; those settings are outside source control.

## Operational run manifest

Indexing workflows emit `run-manifest.json` alongside existing command JSON and
the legacy Solr `run-evidence.json`. The new `fetchcore.operational-run` schema
version 1 composes existing validated `IndexRunArtifact` observations. Solr,
OpenSearch and Qdrant use the same composition contract. The original command
and legacy evidence formats remain unchanged.

The manifest records run/repository/revision/backend/destination identity, a hash
of effective runtime configuration, requested lifecycle, workflow outcome and
ordered command observations. The observation timestamp is the collection time,
not a fabricated command start time. Observations retain actual lifecycle,
currentness, rebuild-required reason, projection/indexing/embedding fingerprints,
mutation counts and measured telemetry/stage durations. `outcome` records the job
state at collection time; later artifact upload or cleanup can still fail the job.
A full discovery scan
does not imply a persistent rebuild. Plan counts stay separate from executed
mutations. Unmeasured embedding/backend work and unexecuted stages stay absent.
Failure category and allowlisted failure diagnostics are retained; raw command
source paths, endpoint configuration and error messages are excluded.

Composition is offline and opt-in through
`scripts/ci/build-run-manifest.py`; it performs no indexing or retrieval. Input
JSON is bounded to 2 MB per command and at most 16 observations per run. Failed
runs retain available observations, including a failed command; a failure before
any command yields an empty observation list when configuration was captured.
A failure before configuration capture has only the existing workflow evidence.
Malformed observations fail validation rather than producing invented metrics.
Collection runs before artifact upload even after an indexing failure.
The effective configuration file is hashed outside the uploaded evidence folder.

## Container inputs

Critical Python and Solr image references live in `infra/images.lock` and are
pinned by manifest-list digest. Dockerfiles and Compose provide the same pinned
defaults. CI may override them with repository variables
`FETCHCORE_CI_PYTHON_IMAGE` and `FETCHCORE_CI_SOLR_IMAGE`, but the override
must also be digest-pinned.

`scripts/ci/docker-pull.sh` retries only recognizable transient registry and
network failures with bounded attempts. Permanent errors fail immediately.

## Repository settings outside source control

Operator-owned settings remain:

- self-hosted runner registration, availability, and boot policy;
- `FETCHCORE_INDEX_RUNNER_LABELS_JSON`;
- explicit rebuild of an OPERATIONAL resource if a future contract change makes
  its snapshot incompatible;
- localization secrets/configuration;
- optional digest-pinned CI image overrides;
- branch protection and required-check configuration.
