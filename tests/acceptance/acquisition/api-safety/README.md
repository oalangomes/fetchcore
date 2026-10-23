> **Classification:** synthetic acceptance fixture, not a production benchmark. The historical timing/metric records below remain archival observations.

# API artifact extraction S0 — quarantine and synthetic baseline

Tracking: issue #155, epic #154. Priority: P0. This document describes intentionally incomplete S0 coverage, not a general-purpose secret scanner.

## Motivation and enforcement

Generic JSON/YAML indexing previously accepted raw Swagger/OpenAPI specifications, Postman collections and Postman environments. Those files can contain live tokens, auth headers, cookies, secrets, variables and examples. Raw canonical chunks can flow to search indices, embeddings and MCP responses.

S0 **quarantines recognized API artifacts** rather than trying to redact unstructured raw content. Quarantine is an intentional temporary retrieval coverage reduction until typed extraction in S2 and S3.

The guard applies at four boundaries:
- canonical safe_scan: known filename hints before read, JSON/YAML content signatures after bounded read but before creating SafeSource or KnowledgeDocument;
- local lexical search: shares the canonical safe scanner;
- targeted local exact lookup: shares the canonical safe scanner;
- application get_source: denies recognized paths before read, and content signatures before returning text.

Diagnostic skip reasons contain only a finite format kind. No source values are serialized into scan diagnostics.

Supported deny signatures: Swagger and OpenAPI JSON/YAML; Postman Collection v2.x schema signatures and customary *.postman_collection.json filenames; Postman environment/globals signatures and file names; Postman v3 *.request.yaml filenames. Unsupported/version-invalid recognized artifacts are also quarantined. The gate uses lexical detection, not validation or YAML parsing, so some unusual encoded/escaped signatures and multi-file metadata may go undetected.

This is **not** blanket credential security for arbitrary JSON, Markdown, scripts, PDFs, external databases or persisted historical indices. Those remain separate risk areas. No API code or scripts are executed and no network references are followed.

## Historic persisted index remediation (operator-owned)

The indexing contract version increases from 23 to 24 so existing snapshots require an explicitly authorized rebuild, not silent incremental compatibility.

**Existing Solr/OpenSearch/Qdrant/third-party indexed data remains potentially exposed until cleaned.** The new code alone does not remove it.

Operator procedure:
1. Restrict serving affected existing backend resources until verified.
2. Inventory impacted artifact paths and revisions without logging values.
3. Use the explicitly authorized Solr rebuild workflow for dedicated compatible Solr resources; verify the new indexing fingerprint and that old raw documents disappeared. For other backends, use their own explicit purge/rebuild procedure.
4. Confirm vectors, caches, exported results, logs and backups are governed separately. Never assume a Solr rebuild purges an unrelated vector index.
5. Assess any necessary credential revocation/rotation with repository owners.
6. Run the synthetic guard tests and verify all relevant persisted stores. If safe removal cannot be proven, keep the index unavailable.

A changed fingerprint, read-only plan/status, or source-level guard is not a historical deletion.

## Frozen synthetic protocol

- Source code baseline: master commit ef57d6b8f9fc49e465e7a26f26d1c151e527316b (pre-S0).
- Corpus: synthetic JSON/YAML OpenAPI 3.1, Swagger 2.0, Postman Collection 2.1, Postman environment and Markdown docs; fake sentinel only.
- Ground truth: ground-truth.json, 5 source-level retrieval queries, known operation/request counts for subsequent slices.
- Measurements: indexable files, canonical document count, skips, source access observations, SourceRecall@5, source-level MRR, p50/p95/p99 indexing latency.
- Safety metrics: boolean presence of synthetic sentinel in canonical documents, retrieval results and source read. The benchmark emits booleans, not any source data.
- Retrievalgate and operation-level Recall@K, usefulness density, dense/hybrid and persisted-backend measurements are follow-on work, **not proven by this S0 benchmark**.

Execution with an isolated pre-S0 checkout (requires project development dependencies):

    git worktree add /tmp/fetchcore-pre-s0 ef57d6b8f9fc49e465e7a26f26d1c151e527316b
    python tests/acceptance/acquisition/api-safety/run.py --implementation-root /tmp/fetchcore-pre-s0 > /tmp/api-s0-before.json
    python tests/acceptance/acquisition/api-safety/run.py --implementation-root . > /tmp/api-s0-after.json

The benchmark does not send data to backends or run external requests. Compare latency only on the same runner. The existing baseline-v1 for private NeuroTrack_MS corpus (20 queries, 42 evidence requirements) is preserved unchanged and must not be republished as raw content.

## Completion boundaries

S0 ensures an explicit fail-closed guard for recognized artifacts entering these paths. S1 defines typed evidence, more complete detection and safe interpreter routing; S2 and S3 replace quarantine with sanitized OpenAPI and Postman evidence. S4 strengthens documentation. S5 resolves bounded references; S6 handles Postman v3 multi-file semantics.

Do not relax any S0 guard until typed candidate content is proven safe across canonical indexing, projections, embeddings, search and source serving.
