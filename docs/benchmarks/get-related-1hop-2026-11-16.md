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
in the YAML are the Ground Truth. Expected response status is also fixed in
the YAML so a coverage divergence remains visible.

## Observed baseline

The [JSON report](results/get-related-1hop-2026-11-16.json) and
[Markdown report](results/get-related-1hop-2026-11-16.md) came from MCP stdio
against the isolated SolrCloud collection for the pinned repository revision.
Seven of eight scenarios passed. All four expected relationships matched;
there were zero missing and zero unexpected relationships. Provenance was valid
in all six scenarios that returned a response. Two expected tool errors were
observed (`source_missing` and `unsupported_capability`), with no unexpected
tool or contract errors. Eight `get_related` calls and one `get_source` call
returned four relationships. MCP wall-time p50/p95 was 3,744/7,504 ms.

The one divergence is `resolved-call-incoming`: the exact reverse edge was
returned with correct source and target identities, but the response was
`partial` rather than the pinned `success` expectation. The response's
`cross_file_resolution` coverage is `partial` for the admitted corpus. The
scenario stays failed; its Ground Truth was not edited to make the run green.
This is a coverage-status discrepancy, not a missing or invented edge.

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
