# FetchCore documentation

[English](README.md) | [Português (Brasil)](pt-BR/README.md)

Use this index to find the current contract. Historical extraction notes and individual experiment results are evidence of earlier work, not the current architecture.

| I want to… | Start here |
| --- | --- |
| Understand the product and its boundaries | [Architecture overview](architecture/overview.md) and [module boundaries](architecture/module-boundaries.md) |
| Understand canonical knowledge and projections | [Repository Knowledge](architecture/repository-knowledge.md) and [projection](architecture/projection.md) |
| Review typed API-contract evidence and format preflight | [API evidence v1](architecture/api-contract-evidence-v1.md) |
| Implement safe OpenAPI/Swagger acquisition | [OpenAPI extraction S2](architecture/openapi-extraction.md) |
| Implement safe Postman Collection 2.1 acquisition | [Postman extraction S3](architecture/postman-collection-extraction.md) |
| Retrieve traceable Markdown sections | [Technical Markdown extraction S4](architecture/markdown-extraction-s4.md) |
| Resolve exact local references | [Bounded local references S5](architecture/local-reference-resolution-s5.md) |
| Understand retrieval and adapters | [Retrieval](architecture/retrieval.md) and [adapters/composition](architecture/adapters-and-composition.md) |
| Understand serving | [Serving boundary](architecture/serving.md) |
| Look up contracts and capabilities | [Contracts](reference/contracts.md) and [capabilities](reference/capabilities.md) |
| Install and index a repository | [Getting started](guides/getting-started.md) and [self-hosted indexing](guides/self-hosted-indexing.md) |
| Share local services safely | [Shared development infrastructure](guides/shared-development-infrastructure.md) |
| Understand CI triggers, runners and container reliability | [CI workflows](guides/ci-workflows.md) |
| Register or inspect a repository source | [Repository registry](guides/repository-registry.md) |
| Plan, sync or rebuild a registered repository | [Repository indexing lifecycle](guides/repository-indexing.md) |
| Search or run MCP | [Searching](guides/searching.md) and [MCP](guides/mcp.md) |
| Configure or add a backend | [Runtime configuration](guides/runtime-configuration.md), [configuration reference](reference/configuration.md), and [adding a backend](guides/adding-a-backend.md) |
| Maintain PT-BR documentation | [Documentation localization](guides/documentation-localization.md) |
| Understand release gates, labels and the initial version | [Release policy](release-policy.md) and [v0.0.1 technical overview](releases/initial-release.md) |
| Reproduce a benchmark | [Experiment convention](benchmarks/protocol.md), [baseline v1 protocol](benchmarks/baseline-v1.md), [Markdown extraction S4](../benchmarks/markdown-extraction-s4/README.md), [Local references S5](../benchmarks/local-reference-s5/README.md), [SearchIntent v2 experiment](benchmarks/search-intent-v2.md), and [Repository Learning Benchmark](benchmarks/repository-learning.md) |
| Understand a durable decision | [ADR index](adr/README.md) |
| Follow a migration or inspect extraction provenance | [Namespace migration](migrations/fetchcore-namespace.md) and [historical extraction](history/extraction/README.md) |

## Vocabulary

[Evidence and provenance](concepts/evidence-and-provenance.md) explains source observations, query matches, and traceability. [Search intent and scope](concepts/search-intent.md) distinguishes current acquisition contracts from planned changes. [Repository identity](concepts/repository-identity.md) explains the repository boundary.

## Documentation rules

Use lowercase kebab-case filenames (except numbered ADRs), one H1 per page, relative internal links, explicit status labels for historical or proposed behavior, portable text diagrams, and copyable commands. Architecture records boundaries; concepts define vocabulary; guides give tasks; reference states current contracts; benchmarks preserve reproducible evidence; ADRs record durable decisions and alternatives. Keep marketing language out of reference pages and avoid duplicating a canonical explanation.

- [Targeted exact retrieval](concepts/targeted-retrieval.md)
