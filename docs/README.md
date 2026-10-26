# FetchCore documentation

[English](README.md) | [Português (Brasil)](pt-BR/README.md)

Use this index to find the current contract. Historical extraction notes and individual experiment results are evidence of earlier work, not the current architecture.

| I want to… | Start here |
| --- | --- |
| Understand the product and its boundaries | [Architecture overview](architecture/overview.md) and [module boundaries](architecture/module-boundaries.md) |
| Understand canonical knowledge and projections | [Repository Knowledge](architecture/repository-knowledge.md) and [projection](architecture/projection.md) |
| Review typed API-contract evidence and format preflight | [API evidence v1](architecture/api-contract-evidence-v1.md) |
| Implement safe OpenAPI/Swagger acquisition | [OpenAPI extraction](architecture/openapi-extraction.md) |
| Implement safe Postman Collection 2.1 acquisition | [Postman extraction](architecture/postman-collection-extraction.md) |
| Retrieve traceable Markdown sections | [Technical Markdown evidence](architecture/markdown-evidence.md) |
| Resolve exact local references | [Bounded local relations](architecture/local-reference-resolution.md) |
| Understand retrieval and adapters | [Retrieval](architecture/retrieval.md) and [adapters/composition](architecture/adapters-and-composition.md) |
| Understand serving | [Serving boundary](architecture/serving.md) |
| Look up contracts and capabilities | [Contracts](reference/contracts.md) and [capabilities](reference/capabilities.md) |
| Install and index a repository | [Getting started](guides/getting-started.md) and [self-hosted indexing](guides/self-hosted-indexing.md) |
| Follow the Developer Local installation contract | [Developer Local guide](guides/developer-local.md) and [P0.0 audit](operations/p00-installation-contract-2026-10-19.md) |
| Review the Developer Local acceptance gate | [P0.1 acceptance](operations/p01-developer-local-acceptance-2026-10-19.md) |
| Share local services safely | [Shared development infrastructure](guides/shared-development-infrastructure.md) |
| Review consumer migration starting state | [Shared infrastructure consumer audit](operations/shared-infra-consumer-audit.md) |
| Review consumer migration validation | [Shared infrastructure consumer validation](operations/shared-infra-consumer-validation.md) |
| Review Qdrant OPERATIONAL preflight | [Qdrant preflight 2026-10-17](operations/qdrant-operational-preflight-2026-10-17.md) |
| Review P0.2 operational acceptance | [P0.2 acceptance](operations/p02-operational-acceptance-2026-10-19.md) |
| Review the initial P0.2 read-only inventory | [P0.2 preflight](operations/p02-operational-preflight-2026-10-19.md) |
| Review Qdrant OPERATIONAL acceptance | [Qdrant lifecycle gate 2026-10-18](benchmarks/results/qdrant-operational-acceptance-2026-10-18.md) |
| Understand CI triggers, runners and container reliability | [CI workflows](guides/ci-workflows.md) |
| Register or inspect a repository source | [Repository registry](guides/repository-registry.md) |
| Plan, sync or rebuild a registered repository | [Repository indexing lifecycle](guides/repository-indexing.md) |
| Search or run MCP | [Searching](guides/searching.md) and [MCP](guides/mcp.md) |
| Configure or add a backend | [Runtime configuration](guides/runtime-configuration.md), [configuration reference](reference/configuration.md), and [adding a backend](guides/adding-a-backend.md) |
| Use MongoDB Search and Vector Search | [MongoDB adapter](guides/mongodb-search.md), including Atlas/local requirements and the pending benchmark gate |
| Maintain PT-BR documentation | [Documentation localization](guides/documentation-localization.md) |
| Understand release gates, labels and the initial version | [Release policy](release-policy.md) and [v0.0.1 technical overview](releases/initial-release.md) |
| Review the launch exposure audit and remaining gate | [Public exposure review 2026-10-22](operations/public-exposure-review-2026-10-22.md) |
| Inspect the frozen operational indexing checkpoint | [Operational indexing freeze 2026-10-18](benchmarks/operational-indexing-freeze-2026-10-18.md) |
| Review typed knowledge canonical validation | [Typed knowledge canonical result](benchmarks/results/typed-knowledge-canonical.md) |
| Review typed knowledge retrieval validation | [Typed knowledge retrieval result](benchmarks/results/typed-knowledge-retrieval.md) |
| Explore benchmarks, acquisition tests and studies | [Experiment convention](benchmarks/protocol.md), [baseline v1 protocol](benchmarks/baseline-v1.md), [Markdown acceptance test](../tests/acceptance/acquisition/markdown/README.md), [Local-relations acceptance test](../tests/acceptance/acquisition/local-relations/README.md), [SearchIntent v2 experiment](benchmarks/search-intent-v2.md), and [repository-learning consumer study](../studies/repository-learning/STUDY.md) |
| Understand a durable decision | [ADR index](adr/README.md) |
| Follow a migration or inspect extraction provenance | [Namespace migration](migrations/fetchcore-namespace.md) and [historical extraction](history/extraction/README.md) |

## Vocabulary

[Evidence and provenance](concepts/evidence-and-provenance.md) explains source observations, query matches, and traceability. [Search intent and scope](concepts/search-intent.md) distinguishes current acquisition contracts from planned changes. [Repository identity](concepts/repository-identity.md) explains the repository boundary.

## Documentation rules

Use lowercase kebab-case filenames (except numbered ADRs), one H1 per page, relative internal links, explicit status labels for historical or proposed behavior, portable text diagrams, and copyable commands. Architecture records boundaries; concepts define vocabulary; guides give tasks; reference states current contracts; benchmarks preserve reproducible evidence; ADRs record durable decisions and alternatives. Keep marketing language out of reference pages and avoid duplicating a canonical explanation.

- [Targeted exact retrieval](concepts/targeted-retrieval.md)
