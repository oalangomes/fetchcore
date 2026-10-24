# Hybrid fusion benchmark (#84)

This protocol studies candidate admission and RRF parameters independently.
SearchIntent v2, canonical queries, Ground Truth, acquisition, ANN, indexing,
embeddings, and default retrieval behavior remain fixed. Retrieval observations
never establish consumer satisfaction or authority.

## Frozen inputs and controls

The corpus is `oalangomes/NeuroTrack_MS` at
`74ce3244956a65baed61f1dae6f498cb2be4f3b7`. Reuse the original 20 canonical
queries and 42 requirements from SearchIntent v2. K and output limit are 20;
acquisition is at most 80 candidates per channel; use one warmup and 20 measured
repetitions per query. Embeddings remain Ollama / embeddinggemma / 768 dimensions.
Do not change ANN breadth between comparisons.

`artifacts/historical-checksums.json` freezes every historical V0.2 artifact.
The runner verifies those checksums, the original query/GT identities, and the
source-reviewed confirmation set before contacting backends. Historical results
and fresh measurements are separate; a snapshot difference cannot be relabeled
as a reproduced historical result.

Solr/OpenSearch legacy admission is `max_results` per channel; Qdrant uses the
effective acquisition window and sparse eligibility with original rank gaps.
An explicit fusion window selects an eligible ordered prefix without renumbering.
It does not increase acquisition or change output limits. RRF remains the existing
`FusionKernel`; its tie breaking and fingerprints are unchanged for the control.

## Execution

Run from an installed development checkout (or set `PYTHONPATH=src`). Keep raw
outputs and the existing BENCH run configuration outside the repository. Use the
schema of `benchmarks/baseline-v1/run-config.example.json`, with
`provisioning: reuse_only`, `dedicated: true`, and owned dedicated BENCH resources.
The runner checks current Docker ownership, volume bindings, endpoints and
snapshots; it never starts containers, indexes, rebuilds, migrates or cleans up.
Operators must verify recorded ownership/configuration before starting resources.
Incompatible or missing snapshots stop **all arms before retrieval**.

```bash
python benchmarks/hybrid-fusion/run.py control \
  --run-config /private/bench-config.json --workspace /private/frozen-corpus \
  --output /private/hybrid84/control.json.gz

python benchmarks/hybrid-fusion/run.py capture \
  --run-config /private/bench-config.json --workspace /private/frozen-corpus \
  --control /private/hybrid84/control.json.gz \
  --output /private/hybrid84/capture.json.gz

python benchmarks/hybrid-fusion/run.py replay \
  --capture /private/hybrid84/capture.json.gz \
  --output /private/hybrid84/replay.json.gz

python benchmarks/hybrid-fusion/run.py confirm \
  --run-config /private/bench-config.json --workspace /private/frozen-corpus \
  --control /private/hybrid84/control.json.gz \
  --replay /private/hybrid84/replay.json.gz \
  --output /private/hybrid84/confirmation.json.gz

python benchmarks/hybrid-fusion/run.py report \
  --artifact /private/hybrid84/confirmation.json.gz \
  --output /private/hybrid84/public-report.json
```

Raw output paths must be new and outside the repository. Artifacts are sealed
with canonical checksums; incomplete, tampered, duplicate or incompatible input
observations fail explicitly. Record the runtime and runner source fingerprints,
exact revision/dirty status, effective configuration identity and model digest.
Do not edit code or the configuration between stages. A corrected run uses a new
output, rather than merging partial measurements or overwriting history.

The control measures all three dense and hybrid arms through the application and
real adapters. It must contain all 400 observations per arm and repeatable ranked
spans per query. Capture repeats hybrid acquisition once per query/repetition,
records full bounded lists with source materialization/provenance, and checks
that offline control replay matches actual spans and fusion fingerprints.

## Explicitly authorized preparation

When an operator explicitly authorizes replacing incompatible BENCH projections,
perform preparation separately, before freezing the new control. Copy the private
BENCH configuration and set `provisioning: explicit_rebuild` only in that copy.
The measurement configuration remains `reuse_only` throughout all four stages.

```bash
python benchmarks/hybrid-fusion/rebuild.py --execute-rebuild \
  --run-config /private/bench-rebuild-config.json --workspace /private/frozen-corpus \
  --embedding-cache /private/hybrid84/preparation-embeddings.sqlite \
  --output /private/hybrid84/authorized-rebuild.json.gz
```

This command validates owned dedicated BENCH containers/volumes/endpoints and
frozen inputs, indexes canonical knowledge once, and calls the existing native
full-rebuild operations. It never provisions containers or changes OPERATIONAL.
The old compatibility errors, new canonical count, per-backend rebuild telemetry,
post-write snapshots and corpus checksum equivalence are recorded privately.
A preparation-only exact-input SQLite vector cache binds provider/model/digest,
dimensions and projection/serializer identities. It shares compatible vectors
between backend writes; it is never used in measured retrieval. Incompatible
vectors fail before caching. The initial blocked artifacts remain historical
evidence; newly rebuilt snapshots establish a separately identified control.

## Experiments and interpretation

Allocation: the nine pairs `{20,40,80} × {20,40,80}`, with RRF k=60. Causal
comparisons change one window at a time; the artifact includes those paired
comparisons. RRF: k `{10,30,60,120}` with each backend's legacy windows. Never
combine the winning windows and k in this issue. The unconfigured control remains
an explicit arm even where an explicit configuration produces identical input.

Replay uses the same acquired lists for every variant without backend or embedding
calls. It records admission, exclusion, original ranks, individual RRF
contributions, final ordering, recovered requirement identities and per-query
regressions. Replay durations are **not retrieval latency**.

Real confirmation alternates control/finalist order deterministically through the
application and adapters. Observer overhead is included equally in paired
confirmation timings. Full acquisition fingerprints distinguish changed backend
acquisition from fusion changes; identical acquisition must reproduce offline
ranks and output fingerprints. Snapshot/model identities remain fixed.

Use the original evaluators for EvidenceRecall, SpanCoverage, MRR, nDCG,
UsefulContextLineRatio and ContextYield at 100/250/500/1000 **lines**. The additional
`useful_hit_density` is the fraction of returned hits with positive reviewed span
coverage; it does not replace UsefulContextLineRatio. Record acquired/admitted/
returned counts, returned lines/chars, stages, backend duration and measured
application end-to-end duration. Percentiles used by the cost gate are nearest
rank over the measured application durations. Missing acquisition or timing
measurements fail the gate rather than becoming invented zeroes.

## Selection and promotion

Protect the exact set of recovered requirements in every paired query/repetition;
aggregate recall cannot hide substitution or loss. Require nondecreasing aggregate
MRR and ContextYield at every line budget for each backend, with improvement in
at least one of these metrics across the three backends. Qdrant can be unchanged.
Rank eligible variants by mean MRR gain across backends, then mean ContextYield
gain over all four budgets, then fewer candidates examined. A deterministic
variant-name tie break applies only after those three criteria tie.

Real promotion also requires no more than 80 acquired candidates per channel,
compatibility/provenance tests, and end-to-end p95 at most 115% of control for
every backend. The BENCH config flag alone does not establish isolation.
Without a separately recorded exclusive observation window, the decision remains
`pending-performance-comparability` regardless of apparent timing gains.

`confirm --performance-attestation /private/isolation.json` accepts an explicit
operator record with `owner`, `dedicated: true`, `exclusive_host: true`, a
`config_fingerprint` matching the canonical BENCH configuration fingerprint,
`started_unix`/`finished_unix` enclosing the run, and nonempty `observations`
describing the observed isolation. The BENCH configuration must also declare
dedicated exclusive use. Preserve the record and measurements for review; the
runner validates binding/coverage, not the truth of an operator's declaration.
No unrelated workloads are stopped automatically to manufacture comparability.

Ten additional queries were frozen and reviewed directly against source spans
before parameter exploration. Their review is agent-based, not independent human
review; its file checksums are in `confirmation-review.json`. Selection uses only
the original cases. The selected configuration is tested once as a fixed parameter
choice on the additional set, with the same warmup/repetition protocol; no retuning
or alternative selection follows its results. Even with passing gates, report the
opt-in as experimental and validated only on the studied corpus, not generalizable.
A failed holdout or quality gate means no promotion.

## Publication and present result

The private capture contains source content and must never be committed. The
`report` command uses an allowlist excluding source payloads, endpoint settings
and raw snapshots; inspect public reports before versioning them. Publish protocol,
checksums, safe identities, metrics and per-query regressions. Preserve partial
provenance as partial. Do not fabricate unavailable results or measurements.

The recovered execution has a valid completed control with 400 observations per
arm across all six dense/hybrid arms. The expected private `capture.json.gz` is
missing, so replay, finalist analysis and confirmation remain pending. This is
not a negative quality result, and no configuration has passed the promotion
gates. See the [historical blocked control attestation](artifacts/blocked-control.json),
the [sanitized completed-control report](artifacts/control-report.json), and the
[current result report](../../docs/benchmarks/results/hybrid-fusion.md).
