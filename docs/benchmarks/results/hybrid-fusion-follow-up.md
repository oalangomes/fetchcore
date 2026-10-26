# Hybrid fusion #84: consolidation, channel attribution and performance disposition

The benchmark is complete without promotion. This follow-up repairs the
presentation of frozen observations, explains the dense/hybrid differences, and
preregisters one possible next experiment. No retrieval campaign, index rebuild,
embedding, Ground Truth, SearchIntent or default change was performed.

## Execution and artifact identity

The measurement revision remains `4a7af87429310fc6169856583c37085702f5cb20`,
runner SHA-256 `4d9332bf28c4b294e935bec1d09401bf5bd17ac6f56fad3b8cdbe330d7f4518d`,
Python 3.11.16 and NeuroTrack_MS revision
`74ce3244956a65baed61f1dae6f498cb2be4f3b7`. The original four consolidated
artifact hashes in the [benchmark result](hybrid-fusion.md) remain unchanged.

The control consolidation copied the first artifact and merged observations,
snapshots and checkpoints, but left its first-backend-only `aggregates` field
untouched. Capture had the same presentation defect. The repair recomputes every
eligible arm, checks exact equality against each input's stored aggregates,
rejects missing/duplicate/foreign arms, incomplete query manifests, inconsistent
snapshots and fingerprints, and seals output only after validation. Canonical
backend order makes reversed inputs byte-identical. Source observations,
provenance, snapshots and measurement identities are unchanged. Historical
schema-v1 artifacts remain readable; their partial aggregate maps are not used
as a complete summary.

The separately named corrected control was processed by commit `28a8797`, with
processing hash recorded under optional `consolidation.version=1`; this is not
a measurement by that revision. Its private artifact SHA-256 is
`ef77488959eafb5a229631183ffec586d851fba88e98905e007ab58d96f6f939`.
The [corrected public report](../../../benchmarks/hybrid-fusion/artifacts/runs/4d9332bf/control-consolidated-v1-report.json)
and [diagnostics](../../../benchmarks/hybrid-fusion/artifacts/runs/4d9332bf/control-consolidated-v1-diagnostics.json)
contain all six arms. Original public reports and private artifacts are preserved;
existing capture/replay still bind the original control checksum. The integrity audit
also found stale seals in the original public `control-report.json` and
`control-diagnostics.json` (SHA-256 `dc22cc40462e65daeb30e3602bef8a59d770c6eb6af7427b4e6edf0e52cf883a`
and `b0d7704ea7b329d6d8207b957e5722c54a398374775787bf233bc75c4ab8e1ce`).
Their aggregate values independently equal the three raw backend controls,
but their stored artifact fingerprints do not match their serialized contents.
They are preserved as explicitly invalid historical publication envelopes;
strict readers reject them. The corrected reports are valid sealed replacements,
not an in-place reseal or a reinterpretation of measurements.
The separately generated [capture report](../../../benchmarks/hybrid-fusion/artifacts/runs/4d9332bf/capture-consolidated-v1-report.json)
contains all three aggregate arms, bound to the original control; corrected
private capture SHA-256 is `8aa32aa25cf2e92d6dc24894cb17c5de36edfb239b6c3b8ca63601ed5e283f1d`.
The [integrity audit](../../../benchmarks/hybrid-fusion/artifacts/runs/4d9332bf/integrity-audit.json)
records valid original raw manifests and explicitly flags the two historical
public seal failures. Changes to
runner/runtime fingerprints invalidate resuming online measurements against
this new code; no historical data is relabeled.

## Method and confidence

The [reproducible attribution program](../../../benchmarks/hybrid-fusion/analyze.py)
uses only existing control/capture/replay artifacts and the canonical evaluator's
frozen Ground Truth regions. The [complete per-query evidence](../../../benchmarks/hybrid-fusion/artifacts/runs/4d9332bf/channel-analysis.json)
preserves full candidate IDs, original channel ranks, raw/normalized scores,
RRF contributions, all acquired/admitted candidate identities, checksums,
repository source references, revisions and spans. It contains no source text,
local endpoints or host paths.

All 60 backend/query pairs have identical channel order across 20 captures;
reconstructed control RRF fingerprints equal the real output across all 1,200
captures. Captured dense prefixes exactly equal dense-alone top-20. Returned
checksums/spans agree with captured evidence. Thus rank inversions are directly
attributable to fusion on the same candidate set, with high mechanical
confidence. Relevance is limited to the reviewed Ground Truth: a candidate with
no marked overlap may still be useful for another task. Partial useful-span loss
is distinct from loss of a complete GT requirement. Provenance validation covers
each representative acquisition and all returned control evidence; capture
shard checksums bind all repetitions.

Control's 120, capture's 60, replay's 60 and confirmation's 90 query shards were
validated without rerunning retrieval. The replay's 13 variants remain the
previous experiment, not new measurements. No graph, dense score or fusion score
is treated as satisfaction or authority.

Reproduce the offline attribution with the private captures available:

```bash
PYTHONPATH=src .venv/bin/python benchmarks/hybrid-fusion/analyze.py \
  --private-directory /tmp/fetchcore-bench84-v2 \
  --output /tmp/fetchcore-bench84-channel-analysis.json
```

## Aggregate comparison: hybrid control versus dense

The comparator here is **dense alone**. The earlier confirmation compared
`rrf-30` with **hybrid control** and its zero-lost-evidence result remains valid.
These are different comparisons. Direction counts below concern MRR only;
other metrics may change on an MRR-unchanged query.

| Backend | MRR dense → hybrid | Recall dense → hybrid | nDCG dense → hybrid | MRR improved / unchanged / regressed | Useful dense chunks outside hybrid K=20 |
| --- | --- | --- | --- | --- | --- |
| opensearch | 0.728846 → 0.550476 | 0.841667 → 0.800000 | 0.751517 → 0.592443 | 2 / 8 / 10 | 3 |
| qdrant-vector | 0.728700 → 0.737976 | 0.841667 → 0.900000 | 0.751316 → 0.753820 | 4 / 13 / 3 | 4 |
| solr | 0.709402 → 0.521934 | 0.766667 → 0.800000 | 0.714747 → 0.574202 | 1 / 10 / 9 | 2 |

Solr's average recall rises because the lexical-only health test recovers a
whole requirement, even while Docker loses one of three requirements. OpenSearch
loses one of three Docker requirements and one of two runtime-config requirements.
The nine displaced query/chunk occurrences include redundant evidence; they are
not nine lost GT requirements. In particular, AI-queue spans and a Qdrant MFA test
span can leave top-20 while complete requirements remain recovered.

## Causal classification

**RRF contribution and candidate displacement.** The expected dense candidates
remain admitted in every displaced case. Their losses are output-rank truncation,
not failed dense acquisition or deduplication. For each useful candidate, the
JSON names every lexical-supported predecessor with a worse/absent admitted
dense rank. Removing those lexical contributions cannot create those inversions;
the dense-only ordering was independently verified against the actual dense arm.
There is no unique one-to-one displacer when several candidates precede one
expected item; the reported set expresses that limitation explicitly.

**Channel imbalance and window effects.** Solr admits 20+20 with only 3.10 shared
IDs on average (36.90 unique); OpenSearch 3.55 shared (36.45 unique). Both acquire
80+80. Useful lexical ranks beyond 20 lose their lexical contribution even when
admitted through dense: the Solr MFA expected span has lexical rank 75; the
Docker expected span has lexical rank 26 on both backends. Enlarging a window
might restore that contribution but also introduces more competing candidates;
the existing 13-variant replay already tested 20/40/80 windows. It did not produce
a globally eligible window finalist. Admission is therefore a contributing
condition, not a demonstrated standalone remedy.

**Why rrf-30 does not fix Solr/OpenSearch.** With both admitted ranks <=20,
any dual-channel candidate has score at least `2/(k+20)`, greater than the best
single-channel score `1/(k+1)` when k>18. Both k=30 and k=60 preserve this consensus
advantage. Actual replay establishes metric equivalence for these backends;
the inequality explains the six dual-channel predecessors in the MFA example,
not a claim that every possible ranking is invariant to k.

**Deduplication and materialization.** The admitted union sizes equal
`lexical + dense - intersection`. All relevant dense top-20 IDs remain admitted,
and materialized checksums/spans match. No observed regression is explained by
lost identity or corrupt materialization. Multiple distinct chunks from one
file remain distinct evidence; this diagnosis does not assume a different
file-level deduplication policy.

**Corpus and evaluation limits.** `ai-component-architecture` has no useful
acquired overlap on any backend and stays MRR zero. Fusion cannot manufacture
that evidence. Qdrant additionally returns large generated/mutation-report
chunks outside this query's GT. Their presence is observed corpus content,
not authorization to alter admission policy, chunking or the corpus in #232.

## Concrete expected evidence and its predecessors

All IDs below are suffixes for readability; the JSON preserves full IDs and
checksums. L/D are admitted channel ranks, H is fused rank. A missing L signal
can mean the acquired signal was outside admission, not absent in acquisition.

### solr: `mfa-controller-errors`

Expected `src/modules/mfa/controllers/mfaController.js`:47–73, ID `kdoc_35ee355b3bd9957c041f0e7c9f474bbc389b638100f1125805ddb618f5e5b901`. D1 → H7; admitted contribution L=0.000000000, D=0.016393443. ΔMRR=-0.857143; ΔnDCG=-0.666667; ΔRecall=+0.000000.

| Lexical-supported predecessor | Span | L / D / H | RRF L + D |
| --- | --- | --- | --- |
| `353167fdfe2a` src/modules/mfa/controllers/mfaController.js | 39–46 | 1 / 2 / 1 | 0.016393443 + 0.016129032 |
| `a4a0edebf606` src/modules/mfa/controllers/mfaController.js | 1–27 | 12 / 4 / 2 | 0.013888889 + 0.015625000 |
| `8813e223f017` src/modules/mfa/services/mfaService.js | 39–47 | 11 / 5 / 3 | 0.014084507 + 0.015384615 |
| `e9227f33eabf` docs/reference/api/functional-flows/mfa.md | 11–25 | 6 / 12 / 4 | 0.015151515 + 0.013888889 |
| `e3a19ecc29c4` docs/reference/api/api/endpoints.md | 12811–12847 | 15 / 7 / 5 | 0.013333333 + 0.014925373 |
| `b71fc82d723d` src/modules/mfa/tests/mfaRoutes.test.js | 18–177 | 10 / 19 / 6 | 0.014285714 + 0.012658228 |

### solr: `medication-update-owner-service`

Expected `src/modules/medication/services/medicationService.js`:38–41, ID `kdoc_019793e3fb25f3ef9cfcce397dcb70a35f5c929924f2341a1b63269469cce2eb`. D2 → H4; admitted contribution L=0.000000000, D=0.016129032. ΔMRR=-0.250000; ΔnDCG=-0.200253; ΔRecall=+0.000000.

| Lexical-supported predecessor | Span | L / D / H | RRF L + D |
| --- | --- | --- | --- |
| `eebecafcebc0` src/modules/medication/services/medicationService.js | 30–37 | 18 / 3 / 1 | 0.012820513 + 0.015873016 |
| `514161862f5d` src/workers/dailySummaryWorker.js | 70–78 | 1 / None / 3 | 0.016393443 + 0.000000000 |

### solr: `docker-local-runtime`

Expected `docker-compose.yml`:143–245, ID `kdoc_38ba9434eb37c5d2b56154f37cfa5e31f0ac0a740b9b88f142970737ead7e213`. D13 → H24; admitted contribution L=0.000000000, D=0.013698630. ΔMRR=-0.076923; ΔnDCG=-0.262650; ΔRecall=-0.333333.

| Lexical-supported predecessor | Span | L / D / H | RRF L + D |
| --- | --- | --- | --- |
| `634890c9152b` src/modules/billing/services/subscriptionPolicyService.js | 262–269 | 1 / None / 4 | 0.016393443 + 0.000000000 |
| `bb58e206bbd7` src/modules/ai/services/promptService.js | 95–105 | 2 / None / 5 | 0.016129032 + 0.000000000 |
| `1be8476b4ee4` README.md | 326–372 | 3 / None / 6 | 0.015873016 + 0.000000000 |
| `a3b9084af173` .github/dependabot.yml | 1–5 | 4 / None / 9 | 0.015625000 + 0.000000000 |
| `525269b5f829` src/modules/auth/services/authService.js | 1–58 | 5 / None / 11 | 0.015384615 + 0.000000000 |
| `b084122dcae0` docs/reference/api/api/home.md | 22–29 | 6 / None / 12 | 0.015151515 + 0.000000000 |
| `49ac6fa3fa6c` src/services/featureFlagService.js | 6–13 | 8 / None / 14 | 0.014705882 + 0.000000000 |
| `cda30ffb731c` docs/reference/api/api/summary.md | 40–45 | 9 / None / 16 | 0.014492754 + 0.000000000 |
| `d296ff0523b3` docs/reference/api/api/summary.md | 22–27 | 10 / None / 18 | 0.014285714 + 0.000000000 |
| `e12067df22cb` docs/reference/api/api/summary.md | 62–67 | 11 / None / 21 | 0.014084507 + 0.000000000 |
| `3fabb3902aa1` docs/reference/api/api/summary.md | 158–163 | 12 / None / 22 | 0.013888889 + 0.000000000 |

### opensearch: `mfa-controller-errors`

Expected `src/modules/mfa/controllers/mfaController.js`:47–73, ID `kdoc_35ee355b3bd9957c041f0e7c9f474bbc389b638100f1125805ddb618f5e5b901`. D1 → H7; admitted contribution L=0.000000000, D=0.016393443. ΔMRR=-0.857143; ΔnDCG=-0.666667; ΔRecall=+0.000000.

| Lexical-supported predecessor | Span | L / D / H | RRF L + D |
| --- | --- | --- | --- |
| `353167fdfe2a` src/modules/mfa/controllers/mfaController.js | 39–46 | 1 / 2 / 1 | 0.016393443 + 0.016129032 |
| `a4a0edebf606` src/modules/mfa/controllers/mfaController.js | 1–27 | 13 / 4 / 2 | 0.013698630 + 0.015625000 |
| `8813e223f017` src/modules/mfa/services/mfaService.js | 39–47 | 12 / 5 / 3 | 0.013888889 + 0.015384615 |
| `b71fc82d723d` src/modules/mfa/tests/mfaRoutes.test.js | 18–177 | 3 / 19 / 4 | 0.015873016 + 0.012658228 |
| `e9227f33eabf` docs/reference/api/functional-flows/mfa.md | 11–25 | 9 / 12 / 5 | 0.014492754 + 0.013888889 |
| `e3a19ecc29c4` docs/reference/api/api/endpoints.md | 12811–12847 | 16 / 7 / 6 | 0.013157895 + 0.014925373 |

### opensearch: `medication-update-owner-service`

Expected `src/modules/medication/services/medicationService.js`:38–41, ID `kdoc_019793e3fb25f3ef9cfcce397dcb70a35f5c929924f2341a1b63269469cce2eb`. D2 → H4; admitted contribution L=0.000000000, D=0.016129032. ΔMRR=-0.250000; ΔnDCG=-0.200253; ΔRecall=+0.000000.

| Lexical-supported predecessor | Span | L / D / H | RRF L + D |
| --- | --- | --- | --- |
| `eebecafcebc0` src/modules/medication/services/medicationService.js | 30–37 | 10 / 3 / 1 | 0.014285714 + 0.015873016 |
| `53cc55b3f0c3` src/modules/dependent/controllers/dependentController.js | 113–122 | 1 / None / 3 | 0.016393443 + 0.000000000 |

### opensearch: `docker-local-runtime`

Expected `docker-compose.yml`:143–245, ID `kdoc_38ba9434eb37c5d2b56154f37cfa5e31f0ac0a740b9b88f142970737ead7e213`. D13 → H24; admitted contribution L=0.000000000, D=0.013698630. ΔMRR=-0.076923; ΔnDCG=-0.262650; ΔRecall=-0.333333.

| Lexical-supported predecessor | Span | L / D / H | RRF L + D |
| --- | --- | --- | --- |
| `a3b9084af173` .github/dependabot.yml | 1–5 | 1 / None / 4 | 0.016393443 + 0.000000000 |
| `49ac6fa3fa6c` src/services/featureFlagService.js | 6–13 | 2 / None / 5 | 0.016129032 + 0.000000000 |
| `634890c9152b` src/modules/billing/services/subscriptionPolicyService.js | 262–269 | 3 / None / 7 | 0.015873016 + 0.000000000 |
| `bb58e206bbd7` src/modules/ai/services/promptService.js | 95–105 | 4 / None / 9 | 0.015625000 + 0.000000000 |
| `1be8476b4ee4` README.md | 326–372 | 5 / None / 10 | 0.015384615 + 0.000000000 |
| `525269b5f829` src/modules/auth/services/authService.js | 1–58 | 6 / None / 13 | 0.015151515 + 0.000000000 |
| `b084122dcae0` docs/reference/api/api/home.md | 22–29 | 7 / None / 14 | 0.014925373 + 0.000000000 |
| `cda30ffb731c` docs/reference/api/api/summary.md | 40–45 | 9 / None / 16 | 0.014492754 + 0.000000000 |
| `d296ff0523b3` docs/reference/api/api/summary.md | 22–27 | 10 / None / 18 | 0.014285714 + 0.000000000 |
| `e12067df22cb` docs/reference/api/api/summary.md | 62–67 | 11 / None / 21 | 0.014084507 + 0.000000000 |
| `3fabb3902aa1` docs/reference/api/api/summary.md | 158–163 | 12 / None / 22 | 0.013888889 + 0.000000000 |

## Per-query quality, ordering and context

The table's first-useful ranks are computed from observed MRR; `—` means no
useful top-20 overlap. Δ columns are hybrid minus dense. ContextYield deltas are
ordered @100/@250/@500/@1000. Full expected regions, candidate-specific positions,
classification, confidence and predecessor sets are in the accompanying JSON.
Every MRR regression listed has at least one demonstrated lexical-induced rank
inversion, with unchanged dense acquisition. Improved and unchanged cases are
included below, rather than selecting only failures.

### opensearch

| Query ID | First useful D → H | ΔMRR | ΔnDCG | ΔRecall | ΔContextYield 100/250/500/1000 | Returned lines D → H |
| --- | --- | --- | --- | --- | --- | --- |
| `agent-development-rules` | 1 → 1 | +0.000000 | -0.070641 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 156 → 302 |
| `ai-component-architecture` | — → — | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 369 → 440 |
| `ai-controller-queue` | 1 → 1 | +0.000000 | -0.133575 | +0.000000 | +0.1429 / +0.1429 / +0.0000 / +0.0000 | 761 → 631 |
| `ai-queue-subject-context` | 1 → 4 | -0.750000 | -0.420553 | +0.000000 | +0.0000 / -0.1905 / -0.1905 / -0.1905 | 217 → 242 |
| `ai-worker-processing` | 2 → 5 | -0.300000 | -0.244077 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 1238 → 910 |
| `architecture-overview` | 9 → 3 | +0.222222 | +0.091799 | +0.000000 | +0.0000 / -0.3333 / -0.3333 / +0.0000 | 221 → 999 |
| `docker-local-runtime` | 13 → — | -0.076923 | -0.262650 | -0.333333 | +0.0000 / +0.0000 / -0.3333 / -0.3333 | 445 → 377 |
| `health-controller` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 938 → 1005 |
| `health-controller-tests` | 3 → 1 | +0.666667 | +0.500000 | +0.000000 | +0.0000 / +1.0000 / +0.0000 / +0.0000 | 847 → 792 |
| `health-resource-collection` | 1 → 2 | -0.500000 | -0.369070 | +0.000000 | -1.0000 / +0.0000 / +0.0000 / +0.0000 | 857 → 858 |
| `home-active-subject-data` | 1 → 3 | -0.666667 | -0.359196 | +0.000000 | -0.5000 / +0.0000 / +0.0000 / +0.0000 | 523 → 420 |
| `home-prompt-controller` | 1 → 2 | -0.500000 | -0.369070 | +0.000000 | -1.0000 / +0.0000 / +0.0000 / +0.0000 | 1217 → 896 |
| `medication-controller-tests` | 1 → 1 | +0.000000 | -0.073051 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 1541 → 1533 |
| `medication-create-controller-validation` | 1 → 2 | -0.500000 | -0.369070 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 625 → 663 |
| `medication-update-owner-service` | 2 → 4 | -0.250000 | -0.200253 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 508 → 306 |
| `mfa-controller-errors` | 1 → 7 | -0.857143 | -0.666667 | +0.000000 | -1.0000 / -1.0000 / +0.0000 / +0.0000 | 426 → 1007 |
| `mfa-http-routes` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 464 → 597 |
| `mfa-route-tests` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 936 → 821 |
| `mfa-totp-service` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | -0.5000 / +0.0000 / +0.0000 / +0.0000 | 522 → 674 |
| `runtime-config-queue-concurrency` | 18 → — | -0.055556 | -0.235409 | -0.500000 | +0.0000 / +0.0000 / -0.5000 / -0.5000 | 409 → 291 |

### qdrant-vector

| Query ID | First useful D → H | ΔMRR | ΔnDCG | ΔRecall | ΔContextYield 100/250/500/1000 | Returned lines D → H |
| --- | --- | --- | --- | --- | --- | --- |
| `agent-development-rules` | 1 → 1 | +0.000000 | +0.042506 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 156 → 1440 |
| `ai-component-architecture` | — → — | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 369 → 1237 |
| `ai-controller-queue` | 1 → 1 | +0.000000 | +0.066770 | +0.000000 | +0.0000 / +0.1429 / +0.0000 / +0.0000 | 761 → 943 |
| `ai-queue-subject-context` | 1 → 1 | +0.000000 | -0.022427 | +0.000000 | -0.2635 / -0.4540 / -0.4540 / -0.4540 | 217 → 467 |
| `ai-worker-processing` | 2 → 3 | -0.166667 | -0.130930 | +0.000000 | +0.0000 / -1.0000 / +0.0000 / +0.0000 | 1238 → 1281 |
| `architecture-overview` | 9 → 3 | +0.222222 | +0.084354 | +0.000000 | +0.0000 / -0.3333 / -0.3333 / +0.0000 | 221 → 694 |
| `docker-local-runtime` | 13 → 2 | +0.423077 | +0.304557 | +0.666667 | +0.0000 / +0.3333 / +0.6667 / +0.6667 | 445 → 983 |
| `health-controller` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 938 → 987 |
| `health-controller-tests` | 3 → 1 | +0.666667 | +0.500000 | +0.000000 | +0.0000 / +1.0000 / +0.0000 / +0.0000 | 847 → 1485 |
| `health-resource-collection` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 857 → 1127 |
| `home-active-subject-data` | 1 → 1 | +0.000000 | +0.026870 | +0.000000 | -0.5000 / -0.5000 / +0.0000 / +0.0000 | 523 → 1124 |
| `home-prompt-controller` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 1217 → 2027 |
| `medication-controller-tests` | 1 → 1 | +0.000000 | -0.136115 | +0.000000 | +0.0000 / +0.0000 / -0.2500 / -0.2500 | 1541 → 2458 |
| `medication-create-controller-validation` | 1 → 7 | -0.857143 | -0.666667 | +0.000000 | -1.0000 / -1.0000 / +0.0000 / +0.0000 | 625 → 975 |
| `medication-update-owner-service` | 2 → 5 | -0.300000 | -0.244077 | +0.000000 | -1.0000 / -1.0000 / +0.0000 / +0.0000 | 508 → 1414 |
| `mfa-controller-errors` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 426 → 1269 |
| `mfa-http-routes` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 464 → 640 |
| `mfa-route-tests` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 936 → 896 |
| `mfa-totp-service` | 1 → 1 | +0.000000 | +0.026870 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 522 → 1250 |
| `runtime-config-queue-concurrency` | 19 → 4 | +0.197368 | +0.198386 | +0.500000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 409 → 1318 |

### solr

| Query ID | First useful D → H | ΔMRR | ΔnDCG | ΔRecall | ΔContextYield 100/250/500/1000 | Returned lines D → H |
| --- | --- | --- | --- | --- | --- | --- |
| `agent-development-rules` | 1 → 1 | +0.000000 | -0.070641 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 184 → 247 |
| `ai-component-architecture` | — → — | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 369 → 458 |
| `ai-controller-queue` | 1 → 1 | +0.000000 | -0.053402 | +0.000000 | +0.1429 / +0.1429 / +0.0000 / +0.0000 | 761 → 690 |
| `ai-queue-subject-context` | 1 → 4 | -0.750000 | -0.418435 | +0.000000 | +0.0000 / -0.1905 / -0.1905 / -0.1905 | 156 → 270 |
| `ai-worker-processing` | 2 → 5 | -0.300000 | -0.244077 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 1238 → 910 |
| `architecture-overview` | 9 → 16 | -0.048611 | -0.067468 | +0.000000 | -0.3333 / -0.6667 / -0.6667 / -0.6667 | 221 → 1126 |
| `docker-local-runtime` | 13 → — | -0.076923 | -0.262650 | -0.333333 | +0.0000 / +0.0000 / -0.3333 / -0.3333 | 445 → 377 |
| `health-controller` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 842 → 923 |
| `health-controller-tests` | — → 5 | +0.200000 | +0.386853 | +1.000000 | +0.0000 / +1.0000 / +1.0000 / +1.0000 | 447 → 830 |
| `health-resource-collection` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 781 → 785 |
| `home-active-subject-data` | 1 → 3 | -0.666667 | -0.359196 | +0.000000 | -0.5000 / +0.0000 / +0.0000 / +0.0000 | 523 → 420 |
| `home-prompt-controller` | 1 → 2 | -0.500000 | -0.369070 | +0.000000 | -1.0000 / +0.0000 / +0.0000 / +0.0000 | 1217 → 883 |
| `medication-controller-tests` | 1 → 1 | +0.000000 | -0.073051 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 1541 → 1501 |
| `medication-create-controller-validation` | 1 → 2 | -0.500000 | -0.369070 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 625 → 606 |
| `medication-update-owner-service` | 2 → 4 | -0.250000 | -0.200253 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 508 → 306 |
| `mfa-controller-errors` | 1 → 7 | -0.857143 | -0.666667 | +0.000000 | -1.0000 / -1.0000 / +0.0000 / +0.0000 | 426 → 1041 |
| `mfa-http-routes` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 464 → 597 |
| `mfa-route-tests` | 1 → 1 | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 936 → 836 |
| `mfa-totp-service` | 1 → 1 | +0.000000 | -0.043771 | +0.000000 | -0.5000 / -0.5000 / +0.0000 / +0.0000 | 522 → 668 |
| `runtime-config-queue-concurrency` | — → — | +0.000000 | +0.000000 | +0.000000 | +0.0000 / +0.0000 / +0.0000 / +0.0000 | 230 → 309 |

## Qdrant comparison and candidate/context cost

Qdrant admits 80+80, averaging 15.75 shared IDs and 144.25 unique examined
candidates, versus 36.90/36.45 for Solr/OpenSearch. Its lexical channel is sparse
retrieval over its own analyzer, rather than the other adapters' lexical
ordering. Backend dense raw scores are also differently normalized; RRF uses
ranks and does not compare raw cross-backend scores. These structural differences
are confounded; this comparison does not isolate analyzer versus window effects.

On `mfa-controller-errors`, Qdrant's expected span has L18+D1 and stays first;
Solr's acquired L75 is excluded, OpenSearch has no lexical hit in the captured
80, and both become D1-only at H7. On `docker-local-runtime`, Qdrant places the
expected chunk at L2+D13 → H2 and gains other lexical evidence; the other
backends' L26 loses its contribution and D13 becomes H24. Qdrant therefore shows
real lexical gains, but still loses four useful dense chunks in two queries.
Its better aggregate MRR is not evidence of universally superior ordering.

| Backend | Examined dense → hybrid | Acquired hybrid L/D | Returned chars dense → hybrid | Returned lines dense → hybrid | Useful-line ratio dense → hybrid |
| --- | --- | --- | --- | --- | --- |
| opensearch | 80.00 → 36.45 | 80 / 80 | 23137.65 → 27038.15 | 661.00 → 688.20 | 0.054850 → 0.042221 |
| qdrant-vector | 80.00 → 144.25 | 80 / 80 | 23141.45 → 215143.05 | 661.00 → 1200.75 | 0.054850 → 0.029712 |
| solr | 80.00 → 36.90 | 80 / 80 | 21559.75 → 26974.85 | 621.80 → 689.15 | 0.055094 → 0.041686 |

“Examined” is the fusion/admitted union on hybrid, not total backend work:
all hybrid arms acquire 160 raw candidates. Solr/OpenSearch return ~25%/~17%
more characters than dense; Qdrant returns ~9.30 times as many characters and
~1.82 times as many lines. Generated HTML with long lines is visible in Qdrant's
lexical results. ContextYield measured in lines does not fully capture that
character cost; report both, without changing the frozen metric.

## One proposed follow-up: bounded reranking

Track this narrow experiment in [#234](https://github.com/oalangomes/fetchcore/issues/234), under existing [#40](https://github.com/oalangomes/fetchcore/issues/40)
and [#54](https://github.com/oalangomes/fetchcore/issues/54), linked to #84.
Nothing is implemented or promoted in #232.

Candidate admission has already been varied independently; expanding windows
alone was not an eligible finalist. Adaptive budgets add a second acquisition
policy when displaced dense evidence is already admitted. A single fixed
weighted RRF also has conflicting requirements: putting the MFA D1 candidate
strictly ahead of the L1+D2 predecessor needs lexical weight <1/62 with dense
weight 1, while preserving Solr's lexical-only health test against 20 dense
candidates requires weight >=61/80. This does not prove every partial weighted
improvement impossible; it argues against selecting that as the principal
hypothesis for both recovery and ordering.

**Problem:** useful admitted evidence is ranked below duplicate-channel consensus
and below K=20. **Hypothesis:** one fixed backend-neutral semantic scorer over
only the existing RRF top-40 improves ordering without losing lexical recovery.
Every currently displaced useful chunk is within that top-40 (maximum rank 36).
Reranking only top-20 cannot recover those displaced chunks.

**Single variable:** scorer enabled versus disabled. Freeze RRF k=60, existing
admission, top-40 pool, output K=20, queries, corpus, GT, snapshots, embeddings and
scorer-facing representation. RRF-30 and dense remain reference arms. Reuse the
validated captures for offline quality; do not reacquire or rebuild. The existing
ten holdout queries have now been observed and are a regression set for future
work, not a fresh independent holdout. Generalization needs a separate untouched,
source-reviewed set before any broader claim.

**Hard prerequisites:** there is no registered functional scorer in current
FetchCore; test fixtures are not quality evidence. Under #40/#54, preregister one
scorer ID/version, model/weights revision/digest, query and payload serializers,
tokenizer, truncation policy, seeds and hardware before evaluating outcomes.
Do not silently use the extraction-era serializer if it drops canonical query
meaning. This missing binding is a dependency, not an implemented feature.

**Acceptance:** mean MRR improves >=0.05 on each Solr/OpenSearch; no complete GT
requirement lost per query versus hybrid control; no negative aggregate Recall,
SpanCoverage, nDCG or ContextYield at any frozen budget/backend; no per-query MRR
drop >0.05; Qdrant mean MRR does not regress. Returned mean lines/chars may not
increase. Report all losses and improvements, including disagreement with dense.

**Cost bounds:** unchanged 80/channel acquisition and current admission; <=40
scorer evaluations/query; <=2,048 Unicode characters/candidate and <=24,000 total
tokenizer tokens/query with explicit truncation diagnostics and original hashes;
incremental scorer peak RSS <=2 GiB. These are proposed limits to preregister,
not measured costs. Measure host/device memory, scorer input, rerank/fusion/
materialization and end-to-end p50/p95/p99. Comparable online confirmation must
pass the existing per-backend p95 <=115% paired-control gate. Stop on invalid
representation rather than silently changing truncation or scorer.

**Abandonment and risks:** any lost GT, frozen quality/cost failure, nondeterminism,
incompatible fingerprint, unbounded input, RSS excess or comparable p95 failure
rejects this fixed experiment. Do not tune model/window after regression-set
results. Semantic scoring may distinguish neighboring spans, but model latency,
truncation and corpus specificity can erase its benefit. Reranking cannot recover
evidence outside the admitted pool. No backend-specific rules, automatic defaults,
authority or satisfaction semantics; retrievalgate may remain an external harness.

## Performance feasibility and disposition

At 2026-10-26 21:43–21:48 UTC, read-only observations found eight logical CPUs,
20,001 MiB RAM (10,379 MiB available at the first sample), load averages ranging
from 1.04/0.73/0.57 to 0.62/0.63/0.55 and 38 MiB swap used. These are descriptive
samples, not an exclusive interval. Twelve containers remained running: dedicated
BENCH plus SHARED and OPERATIONAL backends and the OPERATIONAL MCP/API/Lab.
Both FetchCore and ai-Ops-Alan runners were active; other Codex processes were
present. Docker CPU snapshots showed nonzero work from multiple services. BENCH
containers have no cpuset, CPU quota or memory reservation. Other workloads were
not stopped or reconfigured.

The [sanitized assessment](../../../benchmarks/hybrid-fusion/artifacts/runs/4d9332bf/performance-assessment.json)
records that container/volume ownership and endpoint bindings pass, all six
snapshots equal the frozen snapshots, service versions and model digest/identity
match, and the effective configuration matches. Service readiness at a point in
time is not proof of stability over an exclusive interval. `exclusive_host` is
false; an independently observed exclusive window is unavailable. The updated
runner and runtime fingerprints additionally differ from frozen measurements,
so the new code cannot resume online confirmation against historical artifacts.

No online confirmation was run. Status remains
`pending-performance-comparability`; existing p50/p95/p99 remain descriptive.
Quality confirmation is passed, performance comparability is pending, and
promotion remains closed. Dedicated containers do not establish host exclusivity.

## NOW validation and operational closure

The failing [self-dogfood run 40402438098](https://github.com/oalangomes/fetchcore/actions/runs/40402438098)
was on GitHub's hosted Ubuntu runner, not a local RunnerOps runner. Installation
and workflow policy passed; Solr preparation returned HTTP 503 after 15 seconds.
The full job log identifies `service_error`, before resource binding/indexing.
The owned startup loop retried connection failures/timeouts but treated all
5xx as terminal. This is an operational adapter startup-race defect, not a
retrieval defect or RunnerOps availability/permission failure. The historical
log does not preserve the Solr response body or container logs, so its exact
internal startup state cannot be reconstructed.

The fix preserves HTTP status and retries only 503 within the existing bounded
owned-container startup budget. Persistent 503, other 5xx, authentication and
protocol failures still fail; exact-owned cleanup remains enforced. An existing
configured service's 503 never authorizes replacement. The workflow routing,
tests and gates were not weakened, and RunnerOps adds no core dependency.
The subsequent RunnerOps watcher conclusively reported all three observed
workflows successful at `28a87975a45846efea9d681b244da47df0961f27`.
Final published-HEAD validation is recorded in the PR conversation.

Focused operational/contract/retrieval verification passed 261 tests before
publication; three further incomplete-query/snapshot cases passed in the
81-test consolidation/runtime suite. Ruff and strict Mypy passed (184 source
files). Documentation links passed; localization check exited successfully but
reported existing missing translations and stale source translations, which are
recorded rather than described as fully localized. Public artifacts were checked
for private paths/endpoints/source payloads. The final validation record, explicit historical seal exceptions and
artifact checksums accompany this follow-up.

PR #232 remains Draft and is not merged. Its code can be reviewed after the
final published-HEAD checks pass; performance promotion remains a separate
blocked decision. Issue #84 can close as a completed benchmark once its reviewed
delivery is accepted, even if the new experiment and promotion remain pending.
Absence of promotion is an experiment outcome, not benchmark failure.
