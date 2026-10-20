# ADR-0005 — Developer Local installation and operational ownership

## Status

Accepted.

## Context

FetchCore already has a Python distribution, operator runtime YAML v1, a persistent repository registry, explicit index operations, and CLI/MCP serving. Those pieces need one installation contract. A checkout-based contributor setup and the shared DEV/BENCH Compose topology are useful, but neither is a suitable default contract for a fresh installed user.

## Decision

Developer Local uses an isolated installation of the built Python wheel with the `mcp` extra. A checkout is not required to run the installed commands. The first persistent retrieval path is lexical Solr. An operator may run a dedicated Solr instance with Docker or supply a compatible, separately provisioned Solr endpoint without Docker. Docker and Ollama are optional infrastructure, not domain dependencies. Solr resource creation and schema provisioning are explicit operator actions. Until a distributable configset/provisioning path is verified, an installed user must provide an already compatible, authorized Solr resource; P0.1 owns closing that first-use gap.

The operator owns runtime YAML v1 and chooses an absolute workspace, canonical repository identity, backend endpoint and resource. Precedence remains safe defaults < explicit YAML < environment < explicit caller overrides (ADR-0003). Repository-owned `.fetchcore` files may control source admission and indexing policy but may not select endpoints, credentials or providers. Credentials belong in environment or a protected operator secret store, never in repository policy.

The existing repository cache root owns `registry.json`, Git mirrors and temporary worktrees. Its default is `~/.cache/fetchcore/repositories`; `--cache-root` or `FETCHCORE_REPOSITORY_CACHE` selects an operator-owned location. Backend data and index snapshots belong to the selected backend volume/resource and must survive ordinary process/container restarts. Moving the registry or backend data is an explicit migration, not an automatic discovery. Persisted schema, projection, serializer, indexing and embedding identities retain their existing versions and fail closed on incompatibility (ADR-0004).

Local source authorization uses explicit `--allowed-root` boundaries; the CLI default is the current directory. `repo add` records a source and its observed identity without indexing. Git registration may create a mirror; it does not create an index. Index `plan` and `status` inspect; `rebuild` creates an initial projection or intentionally replaces an incompatible one; `sync` updates only a compatible snapshot. No serving operation starts index work. The initially supported update cadence is manual.

CLI repository retrieval uses the registry through `RepositoryRetrievalApplicationService`. The current stdio MCP process uses an explicit single `RepositoryBinding` assembled from the same operator runtime YAML, not a registry lookup. For Developer Local the operator must set the binding to the registered canonical repository, materialized workspace and backend resource. P0.1 must verify that these agree and reject a missing or mismatched binding; neither transport may infer authority from backend scores. MCP startup, search, source access, doctor and readiness never rebuild or register a source.

Readiness is a read-only conjunction of service, schema, snapshot, currentness, indexing fingerprint, embedding when required, and retrieval compatibility. `unknown`, stale, absent and incompatible are not ready. A failed configured backend does not silently select another. A snapshot incompatibility requires an operator decision to keep the old projection, migrate through a documented path, or explicitly rebuild in an owned resource. No automatic deletion or migration occurs during upgrade.

Developer Local supports one operator and explicitly authorized repositories/resources. It does not specify multi-tenant authorization, managed secrets, high availability, tenant isolation, automated scheduling or enterprise policy enforcement; those require separate design and acceptance.

## Consequences

The installed wheel and its console entry points become a testable product surface. A single YAML can configure CLI, SDK and stdio MCP, with an explicit repository binding for each. Solr configset distribution/provisioning and cross-transport binding verification are P0.1 acceptance work, not new canonical knowledge contracts. Operational backend certification remains P0.2. Installation alone never mutates an index.

## Alternatives considered

An editable checkout was rejected as the user installation contract because it couples the runtime to source layout. ENV-only configuration obscures structured retrieval settings. Automatic Compose provisioning and implicit indexing were rejected because they obscure ownership and make read operations mutate data. A new registry/configuration system was rejected because existing typed boundaries cover the needed semantics.

## Related issues/PRs

[#200](https://github.com/oalangomes/fetchcore/issues/200), [#99](https://github.com/oalangomes/fetchcore/issues/99), [#98](https://github.com/oalangomes/fetchcore/issues/98), [#132](https://github.com/oalangomes/fetchcore/issues/132), [ADR-0003](ADR-0003-operator-runtime-configuration.md), [ADR-0004](ADR-0004-versioned-persisted-identities.md).
