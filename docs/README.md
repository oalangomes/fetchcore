# FetchCore documentation

Use this index to find the current contract. Historical extraction notes and individual experiment results are evidence of earlier work, not the current architecture.

| I want to… | Start here |
| --- | --- |
| Understand the product and its boundaries | [Architecture overview](architecture/overview.md) and [module boundaries](architecture/module-boundaries.md) |
| Understand canonical knowledge and projections | [Repository Knowledge](architecture/repository-knowledge.md) and [projection](architecture/projection.md) |
| Understand retrieval and adapters | [Retrieval](architecture/retrieval.md) and [adapters/composition](architecture/adapters-and-composition.md) |
| Understand serving | [Serving boundary](architecture/serving.md) |
| Look up contracts and capabilities | [Contracts](reference/contracts.md) and [capabilities](reference/capabilities.md) |
| Install and index a repository | [Getting started](guides/getting-started.md) and [self-hosted indexing](guides/self-hosted-indexing.md) |
| Search or run MCP | [Searching](guides/searching.md) and [MCP](guides/mcp.md) |
| Configure or add a backend | [Runtime configuration](guides/runtime-configuration.md), [configuration reference](reference/configuration.md), and [adding a backend](guides/adding-a-backend.md) |
| Reproduce a benchmark | [Experiment convention](benchmarks/protocol.md) and [baseline v1 protocol](benchmarks/baseline-v1.md) |
| Understand a durable decision | [ADR index](adr/README.md) |
| Follow a migration or inspect extraction provenance | [Namespace migration](migrations/fetchcore-namespace.md) and [historical extraction](history/extraction/README.md) |

## Vocabulary

[Evidence and provenance](concepts/evidence-and-provenance.md) explains source observations, query matches, and traceability. [Search intent and scope](concepts/search-intent.md) distinguishes current acquisition contracts from planned changes. [Repository identity](concepts/repository-identity.md) explains the repository boundary.

## Documentation rules

Use lowercase kebab-case filenames (except numbered ADRs), one H1 per page, relative internal links, explicit status labels for historical or proposed behavior, portable text diagrams, and copyable commands. Architecture records boundaries; concepts define vocabulary; guides give tasks; reference states current contracts; benchmarks preserve reproducible evidence; ADRs record durable decisions and alternatives. Keep marketing language out of reference pages and avoid duplicating a canonical explanation.
