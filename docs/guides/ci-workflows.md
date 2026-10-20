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
and only its own persistent OPERATIONAL resource. They require existing compatible
snapshots, perform `sync` and verify readiness; missing or incompatible
schema/fingerprints fail closed without an implicit rebuild. All default to
hybrid retrieval, allow explicit dense selection, and use Ollama
`embeddinggemma` at 768 dimensions. See the
[operational acceptance](self-hosted-indexing.md#operational-acceptance-2026-10-20).

The repository does not choose the self-hosted runner boot policy. The default
operational labels include `fetchcore`; override
`FETCHCORE_INDEX_RUNNER_LABELS_JSON` only with repository-specific affinity.
Pull-request code stays on GitHub-hosted runners.

Localization is independently gated by
`FETCHCORE_DOCS_LOCALIZATION_ENABLED=true`.

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
