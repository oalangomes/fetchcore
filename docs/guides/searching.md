# Search a repository

For the current MCP path, install the optional transport, bind a repository, and start stdio serving:

```bash
python -m pip install -e ".[mcp]"
export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="owner/repo"
export FETCHCORE_BACKEND="local-semantic"
fetchcore-mcp
```

Connect an MCP client to the `fetchcore-mcp` command and call `search` with `query`, optional `repository`, `limit`, and `knowledge_types`. Use `get_source` with a returned `source_ref` for a bounded source read. For a configured Solr/OpenSearch/Qdrant index, select that backend through an [operator-owned runtime file](runtime-configuration.md). The [MCP guide](mcp.md) lists the transport contract. To embed search in Python, construct `RetrievalApplicationService` using `service_from_runtime_config` and send a `SearchRequest`; see [serving](../architecture/serving.md).
