"""Repository-scoped retrieval application service and composition root."""

from .composition import (
    BackendFactory as BackendFactory,
    BackendRegistry as BackendRegistry,
    builtin_backend_factories as builtin_backend_factories,
)
from .service import (
    MAX_SEARCH_LIMIT as MAX_SEARCH_LIMIT,
    MAX_SOURCE_BYTES as MAX_SOURCE_BYTES,
    MAX_SOURCE_LINES as MAX_SOURCE_LINES,
    RepositoryBinding as RepositoryBinding,
    RetrievalApplicationService as RetrievalApplicationService,
    SearchRequest as SearchRequest,
    ServingLimits as ServingLimits,
    SourceReadRequest as SourceReadRequest,
    SourceReadResult as SourceReadResult,
)
