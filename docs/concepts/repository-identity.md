# Repository identity

Canonical `KnowledgeDocument` and `KnowledgeProvenance` record repository identity with revision and source path. Search scope carries a repository and authorized workspace; the serving application resolves a `RepositoryBinding` and limits source access to it. A source path alone is insufficient to identify the same evidence across repositories or revisions.

Current MCP serving binds one authorized repository per process. Federated multi-repository ranking is a future direction. Repository names and source kinds do not confer semantic authority. See [Repository Knowledge](../architecture/repository-knowledge.md) and [serving](../architecture/serving.md).
