# Postman Collection 2.1 typed acquisition — S3

Status: **opt-in canonical request extraction**. Tracking [issue #158](https://github.com/oalangomes/fetchcore/issues/158), stacked PR #167 over S2 #166 and S1 #165.

## Product boundary

FetchCore acquires and retrieves traceable **candidate evidence** about repository integration contracts. It does not run Postman collections, interpolate environment variables, validate endpoints against a running service, satisfy a consumer request, or authorize execution. **retrieval ≠ satisfaction ≠ authority.**

## Composition

The existing safe scanner is the only allowed source-acquisition channel. The ordinary `RepositoryIndexer` retains S0 quarantine; two opt-in indexers exist:

- `PostmanRepositoryIndexer`: Postman Collection 2.1 JSON only, leaving OpenAPI/Swagger quarantined;
- `ApiContractRepositoryIndexer`: combines Postman Collection 2.1 + OpenAPI 3.0/3.1 + Swagger 2.0 with shared canonical documents/evidence.

The S2 `OpenApiRepositoryIndexer` continues to admit only OpenAPI/Swagger. Per-indexer scanner capability negotiation ensures a Postman-only source is **never attempted** by the OpenAPI-only sanitizer, even if recognized by signature. Each opt-in interpreter carries a contract identity/version into the indexing fingerprint; changes require an explicit compatible rebuild.

```text
repository safe_scan
  -> conservative format/version recognition
  -> explicit format-specific sanitizer capability
  -> only bounded SanitizedApiUnit values
  -> PostmanCollectionSourceInterpreter
  -> KnowledgeDocument (original revision/source checksum)
  -> existing KnowledgeDocumentEnricher
  -> api_contract/api_request@1 in KnowledgeEvidence.semantic
  -> rebuildable backend-neutral projection
```

An item has a stable source-local JSON Pointer such as `#/item/0/item/2/item/0`. Repeated request names and duplicated HTTP paths in different folders remain distinct. Accurate original request line coordinates are not claimed: the canonical document preserves the **source file range**, and typed evidence carries a precise pointer with `line_start/line_end = null`.

## Observable extracted facts

One sanitized canonical document per safe request:

- Method and **path only** (`POST /customers`, `GET /customers/{id}`). Postman URL object path segments, raw absolute HTTP(S) URL paths and simple `{{baseUrl}}` prefixed paths are supported. No host names or untrusted query values are stored. Known path variables become structural `{id}` placeholders; no interpolation occurs.
- Bounded request name, folder ancestry, query parameter **names**, safe header **names**, source-declared collection/folder/request authentication **mechanism types only**. Do not infer effective authorization/credentials.
- Response example **status codes** and a validated MIME media type. Raw example names, response bodies and example header values are never indexed.
- Boolean metadata in the safe textual rendering such as `disabled` and `description_present`. The actual free-text description is not admitted.

A small lexical allowlist is intentional: unclear URL shapes, credentials-like names and arbitrary free text can be omitted or cause the request/collection to be quarantined. The sanitizer is **not** a universal secrets detector; it cannot promise to identify every deliberately encoded secret in an arbitrary request name or path. The no-raw-fallback invariant minimizes exposure.

The following are **never** emitted by this slice: original URL/host/query values, `Authorization`/cookie/API-key header values, request or response bodies, scripts, examples, tokens, environment values, globals, collection variable values, or executables. Nothing is executed or fetched over the network.

## Safety and boundaries

- Recognized Postman environment/globals files remain excluded before raw read. Postman v3 native multi-file YAML remains excluded pending S6.
- Strict JSON parse rejects duplicate object keys. Version/format and collection `info.schema` must match the supported v2.1 collection signature.
- Maximum source byte constraints still come from `safe_scan`; format preflight accepts at most 1,000,000 JSON characters, aligned with the default 1 MB scanner ceiling. Parsing retains bounded node/depth/folder-depth and request count limits. The expanded admission uses indexing-contract version 27 and requires an explicit rebuild of prior snapshots.
- Invalid, unsupported, ambiguous, malformed or empty/no-safe-request sources remain quarantined. There is no downgrade to raw JSON or fallback to a generic source interpreter.
- `get_source` retains the S0 raw-content access restriction even for collections that have generated sanitized canonical documents. Clients can retrieve the safe canonical evidence through retrieval APIs, not unsafe raw bytes.

## Example usage

```python
from pathlib import Path
from fetchcore.repository.indexing import RepositoryIndexConfig
from fetchcore.repository.postman_indexing import (
    PostmanRepositoryIndexer,
    ApiContractRepositoryIndexer,
)

config = RepositoryIndexConfig(repository="myorg/myrepo")
postman_only = PostmanRepositoryIndexer(config)
combined = ApiContractRepositoryIndexer(config)
knowledge = combined.index(Path("/repos/myrepo"))
requests = [
    doc for doc in knowledge.documents
    if any(e.namespace == "api_contract" and e.kind == "api_request"
           for e in doc.evidence.semantic)
]
```

These are **explicit programmatic** indexing modes, not automatic CLI/MCP enablement.

## Benchmark and known trade-offs

`benchmarks/api-extraction-s3/run.py` exercises the frozen S0 synthetic collection and its two exact request ground-truth entries, comparing S2 OpenAPI-only with S3 combined mode. The result measures **request acquisition and canonical evidence**; it is not Recall@K, nDCG, real-repository speed, Solr/Qdrant cleanup or end-to-end MCP retrieval. `tests/unit/repository/test_postman_s3_s3.py` tests the larger nested collection with fake credentials.

Next: independently benchmark ranked lexical/hybrid retrieval using retrievalgate; assess true incremental scanning costs (all active interpreters currently imply a full-scan path on changed sources), and expand typed contract fields only after a schema compatibility decision. Native Postman v3 YAML is separate S6; exact relation resolution remains S5. No graph or agent framework dependency is required.
