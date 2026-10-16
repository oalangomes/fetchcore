# API contract evidence v1 — FetchCore S1 (issue #156)

Status: **schema and routing foundation, not a live API extractor**. Depends on S0 (#155).

## Boundary and purpose

FetchCore transforms repository files into searchable, typed, traceable *candidate evidence*. It does not decide which API contract is authoritative, whether an operation is implemented, whether a client call succeeds, or whether a consumer task is satisfied. **retrieval ≠ satisfaction ≠ authority.**

S1 installs a small, dependency-light, versioned schema and a deterministic format detector. OpenAPI and Postman files remain **quarantined** by the S0 `safe_scan` and raw `get_source` rules until S2/S3 add proven sanitizing extractors. A classification of `supported` means *recognized supported version signature*, **not** full schema validity or permission to index raw content.

## Canonical storage

Reuse these existing boundaries without an API-specific database:

- `KnowledgeSourceInterpreter` creates **sanitized** `KnowledgeSourceUnit` instances only when activated by a future safe implementation.
- `KnowledgeDocument` owns repository, revision, checksum, source_ref, file path and actual line-span provenance.
- `KnowledgeDocumentEnricher` can append `SemanticEvidenceEnvelope` without changing identity/classification/source text.
- `KnowledgeCorpusResolver` may add only exactly supported links; no guesses.
- `SemanticEvidenceEnvelope` stores namespace `api_contract`, one of `api_operation`, `api_request`, `api_schema`, `api_reference`, version `1`, and a deterministic JSON payload.

The typed payload is **closed**: `format`, `format_version`, `path`, `pointer`, `line_start`, `line_end`, `method`, `route`, `name`, `target`. Additional values are rejected on hydration. Authorization, cookies, request bodies and executable scripts deliberately have **no fields** in this first minimal schema. No generic `attributes` dictionary or arbitrary content allowance.

Source-local identity: `<kind>:<RFC6901 pointer>`; the owning canonical document already scopes the discriminator by repository and source file. JSON Pointer is the preferred locator, not a fabricated line number. If an extractor can prove a span, it can additionally supply 1-based line coordinates. Postman request names may repeat in different folders; pointer-based identity prevents collisions. Schema v1 forbids ambiguous or escaped source paths.

An illustrative envelope:

```json
{
  "namespace": "api_contract",
  "kind": "api_operation",
  "version": "1",
  "payload": {
    "format": "openapi",
    "format_version": "3.1.0",
    "path": "contracts/api.yaml",
    "pointer": "#/paths/~1customers/post",
    "line_start": null,
    "line_end": null,
    "method": "POST",
    "route": "/customers",
    "name": "createCustomer",
    "target": null
  }
}
```

Strict schema mismatch is an explicit error, not a downgrade to older shape.

## Detection and routing

`detect_api_artifact(path, content)` returns a closed classification: `unrecognized`, `supported`, `unsupported`, `invalid`, `quarantined`. It recognizes:

- Swagger 2.0 in JSON/YAML;
- OpenAPI 3.0.x and 3.1.x in JSON/YAML;
- Postman Collection 2.1 JSON;
- Postman environments/globals and v3 `*.request.yaml` as quarantined types.

Postman 3.0 native YAML multi-file support is tracked in #161 and is **not** a supported interpreter here. Unexpected/invalid recognized artifacts never raw-fallback.

For safety and performance, JSON detection has a strict 256,000-character limit, rejects duplicate keys and does **not** resolve external references. YAML only examines top-level version declarations using a constrained lexical probe; it is not yet a full YAML parser. The S0 admission guard remains broader and independent of the detector.

`select_api_interpreter` is a deterministic **preflight declaration selector**, not a bypass for safe scan. It accepts only `supported` classification, matches exact format/version, and fails if two registrations claim the same format/version. A missing interpreter means remain quarantined.

## Security, compatibility, observability

- S0 quarantine continues to protect broad, local targeted retrieval and `get_source`.
- Source-derived credential values must **never** enter canonical content, semantic evidence, projection, embedding serialization, response metadata or logs. Closed schema is necessary but not sufficient: S2/S3 must prove sanitization, not assume all strings are safe.
- Scanner coverage includes `api_artifact_status_counts` with bounded status names (no paths, versions, source values); existing `SafeScanResult.audit_mapping` remains unchanged.
- There is **no new active source interpreter and no change to canonical corpus admission** in S1. Therefore the existing indexing contract fingerprint and Projection v3 fingerprint stay unchanged; enabling actual extractors in S2/S3 must fingerprint schema/parser versions and require explicit compatible rebuild when necessary.
- Scanner classification based on filename alone can only label `quarantined_by_filename` since S0 blocks reading those paths. This is expected, not an unmeasured false assurance of format validation.
- No LLM/backend/vendor dependency, no network access, no collection execution, no graph requirement.

## S2/S3 handoff gate

Before replacing S0 quarantine, the future extractor must implement bounded safe parsing and format validation, sanitize raw inputs **before** canonical units exist, prove locators and source spans, detect conflicting claims, track invalid/unsupported/truncated/omitted outcomes, and pass synthetic sentinel tests across canonical/projection/embedding/search/source outputs. Benchmark against frozen S0 and pre-S0 protocols, reporting any real indexing and retrieval regressions. Do not claim this contract alone restores SourceRecall@5 or supports API operation retrieval.
