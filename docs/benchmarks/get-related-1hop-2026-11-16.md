# Deterministic `get_related` one-hop baseline

The v3 scenarios in [fetchcore-related.yaml](../../dogfood/scenarios/fetchcore-related.yaml)
pin FetchCore commit `fa94f4ee456710f348d05f16425c5b5a016c867c` (the master
merge of PR #250). Ground Truth was checked against that revision's source and
canonical indexing output before running MCP. Each expected relationship names
its exact source identity, relation kind, target text, resolution vocabulary,
epistemic status, direction, source reference and line. Resolved edges also
name the canonical target identity and source location. Unresolved observations
are recorded without a target identity. No similarity score is used.

The scenarios exercise a resolved call, its incoming counterpart, a distinct
`get_related` homonym whose import-bound call is not resolved, an explicit
resolution filter, a file-owned import with a one-result bound and partial
coverage, an unsupported relation family, an absent source, and incoming
navigation on a file reference. The pinned corpus has no canonical relation
with `resolution: ambiguous`; ambiguity is covered with negative evaluator tests
and is not fabricated in the live Ground Truth.

The evaluated hypotheses are: (1) an import-bound call with a unique canonical
declaration returns the exact target identity and a reverse edge; (2) an
import-bound call without an exact target remains unresolved, including under
the `require_resolution` filter; (3) a one-result bound does not hide partial
extraction coverage; and (4) unsupported navigation and unknown sources fail
explicitly, rather than inventing a relation. Source lines and canonical IDs
in the YAML are the relationship Ground Truth. `expected_status` records the
current navigation contract separately from the optional aspirational
`coverage_goal_status`; the goal never changes scenario pass/fail.

## Observed baseline

The [initial JSON report](results/get-related-1hop-2026-11-16.json) and
[initial Markdown report](results/get-related-1hop-2026-11-16.md) are retained
as the original MCP measurement against the isolated SolrCloud collection.
They recorded 7/8 scenarios, 4/4 matched relationships, no missing or
unexpected relationships, and p50/p95 of 3,744/7,504 ms. Their
`provenance_valid_rate=6/6` measured response-level consistency, including
two empty successful responses; it was not evidence that six relationships
had valid provenance.

The [review JSON report](results/get-related-1hop-review-2026-11-16.json) and
[review Markdown report](results/get-related-1hop-review-2026-11-16.md) record
a new MCP run on the same pinned revision. The evaluator and scenario changed;
the indexed corpus and retrieval behavior did not.

| Measure | Initial run | Review run |
| --- | ---: | ---: |
| Passing scenarios | 7/8 | 8/8 |
| Exact relationships | 4/4 | 4/4 |
| Missing / unexpected relationships | 0 / 0 | 0 / 0 |
| Observed `partial` statuses | 2 | 2 |
| Response provenance validity, including empty responses | 6/6 | 6/6 |
| Relationship provenance validity | not reported | 4/4 |
| Contract / unexpected tool errors | 0 / 0 | 0 / 0 |
| `get_related` p50 / p95, ms | 3,744 / 7,504 | 3,765 / 7,476 |

The passing-scenario increase comes solely from correcting the expected
incoming status to `partial`. The new relationship provenance measure excludes
empty responses. Latency comes from separate local runs and does not establish
a performance change.

The original `resolved-call-incoming` expectation classified `success` as the
current contract, although the exact reverse edge was returned with
`cross_file_resolution: partial`. The `navigation_result()` rule returns
`partial` when coverage is partial, and the MCP/SDK/CLI reverse-navigation
contract test asserts the same behavior for a mixed corpus. The pinned corpus
has 370 supported sources among 617 under that capability, with partial
coverage. The corrected
scenario expects the contract's `partial` and retains `success` only as an
aspirational coverage goal. The relationship Ground Truth, observed response
and original measurement are unchanged.

A future GraphProjection cannot claim complete coverage from a `success`
status alone. Compare the declared per-capability coverage, admitted source
counts, exact relationship matches, scan completeness, and missing/unexpected
edges on the same pinned corpus. The aspirational goal is diagnostic, not a
score or authority decision.

This run is the **current 1-hop navigation baseline**: canonical structural
lookup through MCP, without GraphProjection. The earlier
[structural-live run](structural-live-2026-11-09.md) measures direct application
calls on synthetic 1k/10k corpora and records backend acquisition cost, which
this MCP response does not expose. The [#30 M0 spike](solr-graph-m0-2026-11-11.md)
measured a separate 11-edge graph fixture: median `nodes()` 1-hop 985.8 ms,
before the exact edge hydration needed for evidence. Neither measurement uses
this pinned corpus and MCP protocol. Their wall times cannot establish a
latency improvement or regression against this baseline.

The unchanged v1/v2 evaluator paths were also exercised through MCP against
the PR #250 pinned revision `32a288977fc03bf7b903ef38d258c91ac975fe42`
in the same isolated collection after a separate explicit rebuild. v1 returned
5/6 scenarios, expected-path hit rate 0.8333 and MRR 0.6167, matching the
PR #250 result. v2 returned 2/2 scenarios, 3/3 exact symbols and 3/3 source
verifications, with no contract errors, also matching PR #250. These runs
verify evaluator compatibility; their latency is separate from the v3 run.
The review reran v1 over the new baseline's `fa94f4e` snapshot and observed
the same 5/6 scenarios, 0.8333 hit rate and 0.6167 MRR.

## Reproduction

Materialize the pinned commit in a separate checkout. Explicitly index it in
an isolated Solr collection with Projection v3 and the repository's current
indexing fingerprint. The existing repository-index workflow shows the
operational preparation, registration and bootstrap steps. Then run:

```bash
.venv/bin/python scripts/dogfood_mcp_eval.py \
  --workspace /path/to/pinned-checkout \
  --repository oalangomes/fetchcore \
  --revision fa94f4ee456710f348d05f16425c5b5a016c867c \
  --solr-url http://127.0.0.1:8983/solr \
  --collection YOUR_ISOLATED_COLLECTION \
  --scenarios dogfood/scenarios/fetchcore-related.yaml \
  --server-command "$PWD/.venv/bin/fetchcore-mcp" \
  --output-json related.json --output-markdown related.md --report-only
```

The report compares all returned relationships one-to-one with the explicit
list. Unexpected relationships remain visible, including when all expected
ones matched. Tool errors, contract errors, incomplete scans and coverage
statuses stay distinct from missing relationships. `limit_reached` means the
returned count reached the bound; it does not prove that another edge exists.
The optional `get_source` call verifies the observed source line, revision,
repository, reference and checksum precondition. It does not prove that the
target is semantically related beyond the canonical resolver's evidence.
The retained `provenance_valid_rate` is a response-level consistency measure
whose denominator includes empty responses. The additive
`relationship_provenance_valid_rate` checks only returned relationship objects;
its value is `null` when none were returned. These checks validate metadata
consistency, while optional `get_source` supplies a separate source-read check.
When any target location field is disclosed, the evaluator requires the
document ID, source reference, path, revision and checksum as one complete
trace. A wholly withheld target location remains valid under the contract.

The latency fields are wall time per MCP `get_related` call, including stdio
transport and canonical document acquisition. p50/p95 over this small scenario
set describe this run, not a throughput or production service-level objective.
The JSON also records MCP call counts and returned edge counts as bounded cost
proxies. Backend transport calls, CPU and memory are not exposed by this MCP
contract; compare those separately using the existing
[live structural benchmark](structural-live-2026-11-09.md). That benchmark uses
synthetic 1k/10k corpora, so its latency numbers are not directly comparable
with this pinned real-repository run.

GraphProjection #30 should be compared on the **same pinned revision, queries,
ground truth and bounded protocol**. Report exact relationship precision,
coverage/status, p50/p95 latency and acquisition cost separately. A graph
neighbor is a candidate, not proof of a relationship or consumer authority.
