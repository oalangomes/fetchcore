> **Classification:** synthetic acceptance fixture, not a production benchmark. The historical timing/metric records below remain archival observations.

# S2 — OpenAPI/Swagger safe extraction benchmark

This is a **small, synthetic, self-contained operation recovery fixture** built on
the frozen S0 corpus in ../api-safety/fixtures. It makes **no network calls**,
does not access live APIs, and does not persist to a search backend.

Run after installation:

    python tests/acceptance/acquisition/openapi/run.py

Expected: 3/3 OpenAPI/Swagger operations recovered as typed canonical evidence;
the Postman collection/environment stay quarantined, the synthetic credential
sentinel is absent, and the opt-in indexing contract differs from S0's fingerprint.

This metric is **ground truth operation recovery**, not ranked retrieval Recall@K,
MRR, nDCG, or full OpenAPI parser conformance. Benchmark before/after of real
retrieval relevance and real repository incremental indexing is separate.

The S2 implementation uses OpenApiRepositoryIndexer explicitly:

    from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer
    from fetchcore.repository.indexing import RepositoryIndexConfig

    indexer = OpenApiRepositoryIndexer(RepositoryIndexConfig(repository="owner/repo"))
    result = indexer.index(workspace)

This opt-in prevents accidental bypass of S0 until all serving/backends have
independent safety tests. The existing RepositoryIndexer and MCP get_source still
quarantine raw API bytes. Exact JSON Pointer evidence remains available even
when original line spans cannot be established. Never infer semantic authority
or runtime implementation from an OpenAPI declaration.
