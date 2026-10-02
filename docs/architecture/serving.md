# Serving boundary

The current serving path is MCP stdio → application runtime bootstrap → `RetrievalApplicationService` → `KnowledgeSearchBackend`. The service builds a repository-scoped request, bounds search and source reads, and rejects unauthorized source paths. MCP defines transport tools and does not translate a request into Solr, OpenSearch, or Qdrant protocol.

The CLI currently provides operational Solr indexing commands (`bootstrap`, `status`, `dry-run`, `sync`), composed through application index operations. The SDK can construct the service directly. HTTP/auth/ACL serving and federated multi-repository retrieval are future directions. See [MCP guide](../guides/mcp.md) and [CLI/MCP reference](../reference/cli-mcp.md).
