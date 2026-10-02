# ADR-0003 — Operator-owned runtime configuration and explicit registries

## Status

Accepted.

## Context

Issue #65 centralized deployment values. Repository-owned `.fetchcore` files describe corpus/indexing policy, while endpoints, credentials, and implementation selection belong to the operator running FetchCore.

## Decision

Resolve versioned operator YAML and supported environment inputs into typed runtime configuration, then compose explicit backend/provider registries and adapters. Precedence is safe defaults < operator config < environment < explicit caller values. Built-in catalogs remain code-owned and injectable at composition. Unknown IDs and malformed higher-precedence values fail explicitly.

## Consequences

Deployments are reproducible and repository content cannot redirect runtime endpoints. Operators must maintain a config file or environment; compatibility names remain until migrated. Composition carries concrete factory knowledge while adapters receive resolved settings.

## Alternatives considered

ENV-only configuration obscures structured settings. Repository-controlled runtime config crosses the trust boundary. Deep adapter ENV lookups make precedence inconsistent. Dynamic plugin discovery and a general DI/config framework add machinery beyond the present fixed catalog.

## Related issues/PRs

[#65](https://github.com/oalangomes/fetchcore/issues/65), [runtime audit](../migrations/issue-65-runtime-audit.md), [configuration reference](../reference/configuration.md), `src/fetchcore/application/runtime_config.py`.
