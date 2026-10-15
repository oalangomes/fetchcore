# OpenAPI/Swagger safe acquisition — S2

Status: **opt-in, operation-level canonical extraction**. Tracking: S2 issue #157, stacked PR #166 on top of S1 PR #165 and S0 #155.

## Scope

A new `OpenApiRepositoryIndexer` provides a deliberate opt-in to safe acquisition for Swagger 2.0 and OpenAPI 3.0.x/3.1.x source files in JSON/YAML. The ordinary `RepositoryIndexer`, lexical local backend, targeted raw source reads, and `get_source` **retain S0 quarantine**. This delivery does not silently enable API parsing in installations that have not reviewed the relevant security policy.

Flow:

    bounded safe scan
      -> content/version preflight
      -> opt-in sanitizer (raw bytes consumed only locally)
      -> SafeSource with only allowlisted, sanitized API units
      -> KnowledgeSourceInterpreter
      -> canonical KnowledgeDocument (one operation/schema/ref per unit)
      -> KnowledgeDocumentEnricher -> api_contract/...@1 evidence
      -> rebuildable backend-neutral projection

`api_sanitizer` is a **trusted local acquisition capability**, not an arbitrary remote plugin or a generic replacement for security review. There is no raw fallback for recognized OpenAPI/Swagger files whose parsing, version detection, size or validation fails. Postman collection/environment files remain quarantined independently.

## Typed evidence

- `api_operation`: one unit for each observed HTTP method/path pair, bounded safe operationId when available, safe parameter names and response status codes in searchable text, exact RFC 6901 source pointer;
- `api_schema`: a unit for each safe named `definitions` (Swagger 2.0) or `components.schemas` (OAS 3), limited observable property names;
- `api_reference`: only source-observed *local* `$ref` targets from request body, parameter, response and content schema locations; no reference resolution, no remote fetch, no inferred implementation relationship.

Envelope `api_contract/{kind}@1` uses the S1 `ApiEvidenceV1`, retaining `format`, version, method, route, name, pointer and local target. Repository identity, revision and **original raw source checksum** are preserved in canonical provenance. JSON Pointer is precise; line_start/line_end in typed evidence are **null** until a parser can prove the exact structural span. Canonical document whole-file bounds are recorded only as an inclusive source range, not as an exact operation line.

The source rendition is an *allowlist* of safe structural tokens — **never summary, description, examples, raw bodies, auth data, credentials, cookie values, server URLs, scripts or unfiltered extension fields**. String filtering is conservative and may exclude legitimate identifiers. It is not a universal secret detector.

## Parser safety and explicit limits

- Strict JSON parsing with duplicate-key rejection; bounded YAML SafeLoader, no aliases, duplicate map keys rejected; bounded nesting, nodes, units and source file byte limits.
- OpenAPI/Swagger content signature and version are required. Wrong, malformed and unsupported formats remain blocked; files with no safe operations or schemas remain blocked.
- Schema/reference traversal is shallow and bounded. Recursive JSON Schema, composition/anyOf/allOf/oneOf, nested examples and external/multi-file refs are not interpreted.
- Only local `$ref` *observations* (not resolved links). No HTTP I/O, code execution, spec validation-as-a-service, generated API clients or agent orchestration.
- Indexing is opt-in through `OpenApiRepositoryIndexer`. Semantic interpreter changes trigger explicit indexing fingerprint incompatibility/rebuild; no silent reuse of old search projections.
- Existing semantic interpreters cause full-scan behavior on changes. S2 does **not** claim incremental indexing efficiency improvements.
- Not all valid OpenAPI path/header/schema shapes are admitted by this deliberately bounded sanitizer. The benchmark tracks useful evidence recovery, not normative conformance.

## Usage

    from pathlib import Path
    from fetchcore.repository.indexing import RepositoryIndexConfig
    from fetchcore.repository.openapi_indexing import OpenApiRepositoryIndexer

    indexer = OpenApiRepositoryIndexer(RepositoryIndexConfig(repository="owner/repository"))
    corpus = indexer.index(Path("/path/to/repository"))
    operations = [
        doc for doc in corpus.documents
        if any(e.kind == "api_operation" and e.namespace == "api_contract"
               for e in doc.evidence.semantic)
    ]

This is a programmatic indexing entrypoint, **not yet a default CLI/MCP configuration setting**. Consumers receive candidates/evidence, never satisfaction or semantic authority.

## Reproducibility

The [S2 synthetic protocol](../../benchmarks/api-extraction-s2/README.md) uses the frozen S0 fixture corpus and checks 3/3 explicitly annotated Swagger/OpenAPI operation ground truths, retained Postman quarantine, original source provenance and sentinel exclusion. Coverage compares the old S0 default against the S2 opt-in.

This gate is **operation acquisition/recovery**, not ranked Recall@K/MRR, multirepository routing, p95 real workload performance or complete cross-backend security auditing. Those require the separately controlled retrievalgate measurements and operational stack gates before promoting default rollout.

S3 adds Postman request-level safe extraction. S5 adds bounded local reference resolution. Neither should import any consumer's authority/satisfaction logic.
