# FetchCore

FetchCore turns software repositories into searchable, typed technical evidence with source provenance. It acquires and indexes repository knowledge, then returns retrieval candidates through backend-neutral contracts. Consumers decide whether evidence satisfies a task and what authority to assign it.

FetchCore owns repository scanning, extraction, canonical knowledge, projection, retrieval contracts and adapters, incremental indexing, and CLI/MCP serving. It does not own agent planning, task completion, approval, or execution authority.

## Quick start

Requires Python 3.11 or newer. For a local development setup:

```bash
python -m pip install -e ".[dev]"
make check
```

To serve a local repository over MCP stdio:

```bash
export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="owner/repo"
export FETCHCORE_BACKEND="local-semantic"
fetchcore-mcp
```

The [getting started guide](docs/guides/getting-started.md) covers containers and optional backend services. [Indexing](docs/guides/self-hosted-indexing.md), [searching](docs/guides/searching.md), and [runtime configuration](docs/guides/runtime-configuration.md) have task-specific instructions.

## Architecture at a glance

```text
Repository sources → registry / indexer → canonical Repository Knowledge
    → KnowledgePacks and typed relations → projection → rebuildable search/vector stores
    → backend-neutral retrieval → application service → CLI / MCP / SDK consumers
```

Canonical Repository Knowledge carries source identity and provenance; backend stores are replaceable projections. The current retrieval adapters are local semantic, Solr, OpenSearch, and Qdrant vector. The current embedding adapter is Ollama. Retrieval supports lexical, dense, and hybrid paths where the chosen backend advertises them; operational indexing through the CLI currently targets Solr. Graph projection and federated search remain future directions.

FetchCore is at `0.1.0.dev0`. The contracts and baseline are versioned, while public integrations and capabilities are still evolving. Start with the [documentation index](docs/README.md) for architecture, contracts, guides, benchmarks, decisions, migration, and historical extraction records.
