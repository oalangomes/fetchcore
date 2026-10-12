# Search a repository

Search is **read-only retrieval** over an authorized repository and an existing, compatible index. Adding a source to the registry or starting the MCP server does not make the source searchable.

## Repository-scoped CLI

From a checkout, install FetchCore and register a local source:

```bash
python -m pip install -e ".[dev]"
fetchcore repo add . --json
fetchcore repo list --json
```

These commands only register a source. Follow [self-hosted indexing](self-hosted-indexing.md) to provision a compatible Solr resource and [indexing lifecycle](repository-indexing.md) to plan and explicitly rebuild its first snapshot. Once that snapshot and the [operator runtime](runtime-configuration.md) are configured:

```bash
fetchcore search "RepositoryIndexer" --repo fetchcore --limit 5 --json
```

Replace `fetchcore` with the repository identity from `repo list` if needed. CLI search **requires** `--repo`; `--config` selects operator-owned runtime settings. No search or source-read call silently indexes, provisions a backend or repairs a snapshot.

Use a real `source_ref` returned by search with `fetchcore source --help` to request bounded source lines and optional revision constraints. A guessed source path is not a replacement for `source_ref`.

## MCP stdio

Install the optional transport, bind an authorized repository, and select a compatible retrieval backend:

```bash
python -m pip install -e ".[mcp]"
export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="oalangomes/fetchcore"
export FETCHCORE_BACKEND="local-semantic"
fetchcore-mcp
```

Connect an MCP client to the stdio process and call `search` with `query`, optional `repository`, `limit`, `knowledge_types` and optional typed `intent`. Call `get_source` with a returned `source_ref`. Starting the server alone does not create an index. For Solr/OpenSearch/Qdrant, select the appropriate compatible backend through [runtime configuration](runtime-configuration.md). See the [MCP contract](mcp.md).

For Python, construct `RetrievalApplicationService` using `service_from_runtime_config` and send `SearchRequest`; see the [serving architecture](../architecture/serving.md). [Readiness](runtime-configuration.md#operational-readiness-issue-134) observes snapshot and repository binding independently of process health. Retrieval returns evidence candidates, not satisfaction or authority.
