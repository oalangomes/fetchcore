# Architecture decision records

ADRs preserve the reasons and trade-offs for durable decisions. Current behavior is described in [architecture](../architecture/overview.md); an ADR explains why a boundary was chosen. Name files `ADR-0001-short-decision-title.md` and include **Status**, **Context**, **Decision**, **Consequences**, **Alternatives considered**, and **Related issues/PRs**. Allowed statuses are Proposed, Accepted, Superseded, and Deprecated. Mark a replacement explicitly rather than silently editing old rationale.

| Decision | Status |
| --- | --- |
| [ADR-0001 — Backend-neutral canonical Repository Knowledge and rebuildable projections](ADR-0001-backend-neutral-knowledge.md) | Accepted |
| [ADR-0002 — Capability-oriented adapters and explicit composition](ADR-0002-capability-adapters.md) | Accepted |
| [ADR-0003 — Operator-owned runtime configuration and explicit registries](ADR-0003-operator-runtime-configuration.md) | Accepted |
| [ADR-0004 — Versioned persisted identities and fail-closed compatibility](ADR-0004-versioned-persisted-identities.md) | Accepted |
| [ADR-0005 — Developer Local installation and operational ownership](ADR-0005-developer-local-installation.md) | Accepted |

Do not create ADRs for tuning values, single class/file choices, one-off fixes, or benchmark results.
