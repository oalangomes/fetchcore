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
FetchCore operational index on the FetchCore-labeled self-hosted runner
    ↓
reuse persistent Solr
    ↓
fetchcore
    ↓
plan → incremental sync | noop
```

Pull requests never write to the persistent operational `fetchcore` resource.
`.github/workflows/self-dogfood.yml` runs the candidate revision against an
owned ephemeral Solr runtime and explicitly rebuilds the disposable
`fetchcore-self-dogfood` resource. The run cleans up only resources it owns.

`.github/workflows/operational-index.yml` accepts only a successful `push` CI
run for `master` from the FetchCore repository, consumes that immutable
`head_sha`, and updates only the persistent `fetchcore` resource. It uses
`reuse_only`, `create_if_missing: false`, and `sync`; missing or incompatible
operational state fails closed rather than creating or rebuilding implicitly.

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
- initial explicit bootstrap/rebuild of the persistent `fetchcore` resource
  when no compatible operational snapshot exists;
- localization secrets/configuration;
- optional digest-pinned CI image overrides;
- branch protection and required-check configuration.
