# Postman Collection 2.1 S3 — synthetic acquisition benchmark

This deterministic benchmark compares the S2 opt-in OpenAPI-only indexer to
the S3 combined API Contract indexer on exactly the same frozen S0 fixture corpus.
It reports **request-level ground truth recovered**, not search ranking, API
runtime success, end-user satisfaction, or parser conformance.

Run after installation:

    python benchmarks/api-extraction-s3/run.py

Expected: 2/2 Postman requests recovered, no duplicates, OpenAPI operations
preserved, environments/globals excluded, and zero synthetic credential
sentinels in serialized canonical documents. Postman raw get_source stays
blocked, including after safe typed indexing.

The S3 indexer is opt-in:

    from fetchcore.repository.indexing import RepositoryIndexConfig
    from fetchcore.repository.postman_indexing import ApiContractRepositoryIndexer

    indexer = ApiContractRepositoryIndexer(
        RepositoryIndexConfig(repository="owner/repository")
    )
    result = indexer.index(workspace)

For Postman alone, use PostmanRepositoryIndexer. For OpenAPI/Swagger alone,
use OpenApiRepositoryIndexer. Ordinary RepositoryIndexer retains the S0
quarantine. All three have distinct indexing fingerprints.

Metrics include document count and context chars plus local indexing runtime;
the latter is only descriptive for a small synthetic fixture. Actual production
recall/precision/latency and incremental costs require a separately controlled
retrievalgate experiment and real-repository observations.
