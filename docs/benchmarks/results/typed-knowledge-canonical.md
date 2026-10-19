# Typed knowledge canonical validation

## Scope and status

The acquisition and Projection v3 gate **passed** locally on FetchCore
`3b77aa1` plus the reviewed changes in this branch. The corpus was
`oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`,
with a clean checkout and all source SHA-256 values matching the
[versioned Ground Truth](../../../benchmarks/typed-knowledge-retrieval/ground-truth.json).
This is canonical validation, not a ranked retrieval or backend benchmark.
The exact product commit for the final gate is the PR head and must be recorded
when the PR is published.

## Method

The 20 acquisition cases were selected from the pinned source before testing
FetchCore: eight OpenAPI operations across several domains, four Postman
requests, five exact Markdown spans, one wrong-method negative, one
quarantined environment, and one pending ambiguous Postman route. Five relation cases cover a local JSON Pointer, two
explicit Markdown links, and two unproven contract-to-code joins. Source
parsing and numbered-line checks validate the Ground Truth before indexing.
The 17 positive locators form a separate ranking Ground Truth; no ranking
metric is promoted in this report. These cases deliberately cover competing
health sources, route parameters, and an unsafe source class. They do not form
a random or statistically representative sample.

The controlled arms were the default indexer, OpenAPI opt-in, combined API
contract opt-in, then local reference resolution enabled. No backend was
contacted. The existing synthetic acquisition gate also ran. Projection v3
records were decoded back to canonical documents. An unchanged reindex and
deleting a linked Markdown target were compared with a full rebuild.

## Observed results

| Gate | Observation |
| --- | --- |
| Source identity | Pinned revision and all 12 source hashes matched |
| Default quarantine | OpenAPI, Postman collection, and two environments excluded |
| Typed recovery | 17/17 positive evidence cases; 2/2 negative cases excluded; one verified ambiguity pending and excluded from metrics |
| Relations | 3/3 positive resolved; 2/2 unproven joins not resolved |
| Canonical state | 799 typed documents with repository, revision, source checksum and canonical checksum checked |
| Projection v3 | 799/799 records round-tripped without canonical evidence change |
| No-op reindex | No added, updated or removed documents |
| Deletion | 20 removed and 2 updated; incremental state equaled a full rebuild |

The four arms emitted 175, 647, 799 and 799 documents respectively. The
combined and local-resolution arms have different indexing fingerprints;
the same document count does not imply identical evidence. Source admission
had two `api_artifact_blocked_postman_environment` skip observations. The
full machine-readable run is reproducible using:

```bash
python tests/acceptance/acquisition/run.py \
  --corpus /path/to/pinned/checkout --output /tmp/acquisition.json
```

## Limits and adoption

No real backend snapshot was written or attested. EvidenceRecall@K,
SpanCoverage@K, ContextYield, tail latency, indexing throughput, embedding
cost and live incremental deletion are **BLOCKED** pending isolated resources
and explicit operational write authorization. Existing frozen baseline-v1
artifacts were not modified. Typed indexing should remain **opt-in**; this
gate supports safe canonical extraction for the reviewed corpus only.
