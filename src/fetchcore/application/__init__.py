"""Repository-scoped retrieval application service and composition root."""

from .composition import (
    BackendFactory as BackendFactory,
    BackendRegistry as BackendRegistry,
    EmbeddingFactory as EmbeddingFactory,
    EmbeddingProviderRegistry as EmbeddingProviderRegistry,
    builtin_backend_factories as builtin_backend_factories,
    builtin_embedding_provider_factories as builtin_embedding_provider_factories,
)
from .runtime_config import (
    FetchCoreRuntimeConfig as FetchCoreRuntimeConfig,
    OllamaRuntimeSettings as OllamaRuntimeSettings,
    load_runtime_config as load_runtime_config,
    service_from_runtime_config as service_from_runtime_config,
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
from .repository_serving import (
    RepositoryRetrievalApplicationService as RepositoryRetrievalApplicationService,
)
from .readiness import (
    ReadinessFact as ReadinessFact,
    ReadinessReport as ReadinessReport,
)
from .navigation import (
    RepositoryKnowledgeNavigationService as RepositoryKnowledgeNavigationService,
    FindSymbolRequest as FindSymbolRequest,
    GetRelatedRequest as GetRelatedRequest,
    NavigationResult as NavigationResult,
)
