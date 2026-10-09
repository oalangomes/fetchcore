# CI workflows

FetchCore separates untrusted validation, post-merge dogfood, optional
localization, and publication. This keeps the self-hosted runner out of pull
request execution and avoids starting expensive or externally configured work
before the ordinary CI result is known.

## Trigger topology

```text
pull request / master push
          ↓
CI on GitHub-hosted runners
          ↓ successful master run only
self-dogfood on the FetchCore-labeled self-hosted runner

canonical documentation push
          ↓ enable variable is true
PT-BR localization on a GitHub-hosted runner
```

`self-dogfood.yml` accepts only a successful `push` CI run whose head repository
is FetchCore itself, then consumes that run's immutable `head_sha`. Failed or
cancelled CI runs, and pull request runs from forks or branches, do not enqueue
it. A manual dispatch remains available for operator recovery. The default
runner labels include `fetchcore`; an override in
`FETCHCORE_INDEX_RUNNER_LABELS_JSON` must retain repository-specific affinity.

The repository does not choose the host boot policy. An on-demand runner must be
made available before a manual run. If every successful `master` run must be
dogfooded immediately, configure that runner as always-on through runner
operations. Do not route pull request jobs to a persistent self-hosted runner.

Localization is independently gated by
`FETCHCORE_DOCS_LOCALIZATION_ENABLED=true`. A manual dispatch is allowed while
the gate is off, but both paths validate the API key, HTTPS endpoint, and model
before installing or invoking localization dependencies.

## Container inputs

Critical Python and Solr image references live in `infra/images.lock` and are
pinned by manifest-list digest. Dockerfiles and Compose provide the same pinned
defaults. CI may override them with repository variables
`FETCHCORE_CI_PYTHON_IMAGE` and `FETCHCORE_CI_SOLR_IMAGE`, but the override must
also end in `@sha256:<64 lowercase hex characters>`.

`scripts/ci/docker-pull.sh` retries only recognizable transient registry and
network failures. Attempts are bounded (three by default), use exponential delay
with jitter, and fail immediately for permanent errors such as authentication or
an invalid image reference. This reduces sensitivity to short Docker Hub outages
without hiding configuration and supply-chain failures.

When updating an image, resolve the intended upstream manifest-list digest,
change the lock and every matching default together, and run the CI contract
tests. Digest updates are explicit dependency changes and should be reviewed as
such.

## Repository settings outside source control

The following remain explicit operator settings:

- runner registration, availability, and boot policy;
- `FETCHCORE_INDEX_RUNNER_LABELS_JSON` when the default label set is unsuitable;
- localization secret, endpoint, model, and enable variable;
- optional digest-pinned CI image overrides;
- branch protection and required-check configuration.

Branch protection should name stable CI checks only after observing the updated
workflow on `master`. Self-dogfood and localization are operational follow-ups,
not pull request merge gates.
