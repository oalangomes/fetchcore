# S1–S5 typed extraction: post-merge acceptance gate

Scope: **FetchCore knowledge acquisition, safety, provenance, typed evidence and bounded local relations**. This is NOT an agent workflow, consumer satisfaction gate or new retrieval algorithm.

## Frozen S0 handoff

- Historical V0.1: `e5b364a0f978186c47911246298f7c9fcc6c1631`.
- Clean V0.2: FetchCore `3cd62ba28a6da2a716711e867d0162f15f9c9076`, NeuroTrack_MS `74ce3244956a65baed61f1dae6f498cb2be4f3b7`.
- Artifact archive: `archive/baseline-v1-s0-v0.2` at `c4b49f79fe3b4835a7794b6cb9a3d417a773371a` (13 frozen files). Never overwrite or publish local `.run-state.json`.
- Verification: PR #179, `docs/benchmarks/baseline-v1-s0-verification.md`.
- Evidence-based historical verdict: `RETRIEVAL_ONLY_OBSERVATION` because query-time snapshot attestations/indexing-contract fingerprints were not persisted.
- **Operator acceptance:** repository owner attested the S0 snapshot fingerprint identity and authorized proceeding. This closes the operational decision; it does not retroactively create missing historical metadata.

No further V0.2 rebuild, re-ranking or RRF tuning is required as a prerequisite to run the synthetic slice gates.

## NOW — execute existing slice gates together

Use a committed revision from the merged S1–S5 master, with Python development dependencies available:

```bash
source .venv/bin/activate
python -m pip install -e ".[dev]"
python benchmarks/typed-extraction-s1-s5/run.py \
  --output /tmp/fetchcore-s1-s5-gate.json
```

The script runs six existing contract/security test files and five existing synthetic benchmarks. It checks **S0 raw-artifact quarantine**, **S1 typed detection/contracts**, **S2 OpenAPI/Swagger operation recovery**, **S3 Postman Collection 2.1 request recovery**, **S4 exact Markdown spans/provenance**, and **S5 bounded relationships/false-positive exclusions**.

| Slice | Source of evidence | Gate |
| --- | --- | --- |
| S0 | `tests/unit/repository/test_api_artifact_s0.py`, `benchmarks/api-extraction-s0/run.py` | blocked get_source; zero synthetic sentinel in canonical, retrieval and source-access output |
| S1 | `tests/unit/repository/test_api_contracts_s1.py` | typed format/locator contracts, version and deterministic interpreter routing; no silent raw admission |
| S2 | `benchmarks/api-extraction-s2/run.py`, unit tests | 3/3 operations, no extras, Postman still quarantined in S2-only mode, separate indexing fingerprint |
| S3 | `benchmarks/api-extraction-s3/run.py`, unit tests | 2/2 requests, 3 OpenAPI operations preserved, environment excluded, no sentinel, distinct fingerprint |
| S4 | `benchmarks/markdown-extraction-s4/run.py`, unit tests | 6/6 reviewed facts and exact locators; no bogus or discarded-region observations; unchanged reindex no-op |
| S5 | `benchmarks/local-reference-s5/run.py`, unit tests | 3/3 positive links, zero wrong contract-to-code links, bounded budget reports omitted evidence; removal prunes relation |

The suite operates on synthetic fixtures only. It does **not** contact Solr, OpenSearch, Qdrant or Ollama, does not write indexes or vectors, and does not call live external APIs. Output contains only metadata/metric summaries, source revision, dirty-tree flag and PASS/FAIL; there is no private corpus source.

**FAIL is a stopping condition**. Do not suppress a failing gate or dilute Ground Truth. On failures, fix one slice at a time and rerun.

## Integrated canonical/projection gate (issue #181)

Run `python benchmarks/typed-extraction-s1-s5/integrated.py --output /tmp/issue-181-integrated.json` after installing development dependencies. The new versioned [Ground Truth](integrated-ground-truth.json) combines the existing safe S0, S4 and S5 fixtures in one temporary corpus. The output pins every input SHA-256, a corpus revision digest, the FetchCore revision, indexing fingerprints and canonical digests. CI runs this gate alongside the slice gate.

It checks the default quarantine → OpenAPI opt-in → combined API contract opt-in sequence, six exact Markdown spans, eight API operation/request facts, three proven local relations, forbidden joins, repository/revision/checksum provenance, Projection v3 round trips, no-op reindex, and deletion versus full rebuild equivalence. This is still a **synthetic, backend-free** gate; the six Markdown cases and three relation cases are inherited from the existing Ground Truth rather than newly reviewed real-repository cases.

## NEXT — live backend projection and ranked retrieval

The default `RepositoryIndexer` retains S0 quarantine. The OpenAPI and Postman S2/S3 indexers are **explicitly opt-in**. Merging S1–S5 does not mean default source scanning, MCP get_source or preexisting persisted indexes suddenly serve typed API facts.

An integrated check must explicitly choose `OpenApiRepositoryIndexer` or `ApiContractRepositoryIndexer`, with S5 `resolve_local_references=True` only when required. Preserve explicit repository binding and source-read safety.

Run a **new protocol**, not an overwrite of `baseline-v1`, on a fixed, safe test repository and reviewed evidence Ground Truth that includes API operations, Postman request identifiers, Markdown section locators and provable local relations. Measure:

1. acquisition: admitted/sanitized operation/request/section counts, skips, zero synthetic credential leakage;
2. canonical: deterministic identities, provenance, exact source spans or JSON Pointer, checksum/revision;
3. projection: independent Solr/OpenSearch/Qdrant snapshots, explicit indexing-contract/serializer/embedding fingerprints and rebuild/incremental diff;
4. retrieval: actual EvidenceRecall@K, SpanCoverage@K, MRR/nDCG, ContextYield, candidate budgets and p50/p95/p99 when indexed;
5. safety: no raw Postman environment values or unsafe get_source fallback.

Use **isolated backend resources**. Do not run a full rebuild, delete collections, or mutate user/shared indexes without explicit authorization. Do not infer authority from backend scores. Use retrievalgate externally if regression gate automation becomes useful.

## LATER / DO NOT DO YET

- LATER: dedicated real-repository operation/request/section/relation retrieval Ground Truth and controlled S1–S5 end-to-end comparison against a new indexed baseline.
- DO NOT DO YET: merge all typed interpreters into default indexing silently; promote S0 V0.2 into a causally controlled experiment; change baseline-v1 formulas; tune RRF based on acquisition shifts; treat S5 relations as graph-required or authoritative.
