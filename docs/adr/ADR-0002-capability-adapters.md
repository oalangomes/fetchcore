# ADR-0002 — Capability-oriented adapters and explicit composition

## Status

Accepted.

## Context

Retrieval backends implement `KnowledgeSearchBackend`; embedding implementations implement `EmbeddingProvider`. Issue #52 separated these capabilities while keeping historical import paths available.

## Decision

Place concrete retrieval implementations under `adapters/retrieval` and embedding implementations under `adapters/embedding`. Application composition owns explicit factories and may import concrete classes. Core contracts and retrieval adapters depend on ports; provider selection is injected.

## Consequences

Dependencies and optional capabilities are visible and testable. Factories must be maintained in composition, and historical aliases need a compatibility window. A new adapter requires an explicit registration and failure tests.

## Alternatives considered

A universal `providers/` directory or generic `backends/` bucket mixes unrelated capabilities. A service locator or DI framework adds discovery and lifecycle machinery without a demonstrated need. Letting retrieval adapters construct Ollama directly would couple backend and embedding transport.

## Related issues/PRs

[#52](https://github.com/oalangomes/fetchcore/issues/52), [adapter architecture](../architecture/adapters-and-composition.md), `src/fetchcore/application/composition.py`.
