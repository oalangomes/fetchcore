# Repository acquisition acceptance tests

These are deterministic **tests**, not retrieval benchmarks. The small
synthetic fixtures prove data-admission invariants, safe typed extraction,
provenance, bounded relations and incremental no-op behavior. They do not
establish backend retrieval recall, general ranking, real-workload throughput
or production tail latency.

Run the complete acceptance gate from the repository root:

```bash
python tests/acceptance/acquisition/run.py \
  --output /tmp/fetchcore-acquisition-acceptance.json
```

The gate runs the established contract/unit checks and the following local
fixture checks; it never contacts Solr, OpenSearch, Qdrant or Ollama:

For the pinned real corpus, add `--corpus /path/to/checkout`. This verifies
the revision and source checksums in the [versioned Ground Truth](../../../benchmarks/typed-knowledge-retrieval/ground-truth.json),
checks independent source locators, then compares canonical typed evidence,
relations, provenance, Projection v3 round trips, no-op reindexing and a
deletion against a full rebuild. The checkout must be clean. No backend writes
or ranked retrieval occur. CI runs the redistributable synthetic checks; the
real corpus check is opt-in because that repository is not vendored here.

| Fixture check | Requirement |
| --- | --- |
| [API safety](api-safety/) | Recognized unsafe raw artifacts remain quarantined; no synthetic secret sentinel escapes |
| [OpenAPI](openapi/) | Typed operation recovery without exposing unsanitized sources |
| [Postman](postman/) | Typed request recovery, preserved OpenAPI evidence and excluded environments |
| [Markdown evidence](markdown/) | Correct source spans, bounded typed observations and no-op reindex |
| [Local relations](local-relations/) | Proven relation recovery, false-positive exclusions and bounded output |

The gate emits `fetchcore.acquisition-acceptance-v1` with named checks and
PASS/FAIL. It verifies behavior with fixed fixtures; it is not a new benchmark
or a source of semantic authority. Timing diagnostics inside legacy fixture
scripts are informative only and **must not** be used for performance promotion.

Historical notes and stage terminology (S0–S5) are preserved in
[HISTORY.md](HISTORY.md) solely to document the original experiments. Their
original JSON protocol IDs remain unchanged for compatibility. Historical
measurement JSONs are preserved byte-for-byte, not regenerated.

A new acquisition invariant belongs in the relevant test module. A real
indexing or retrieval performance experiment belongs in
[benchmarks](../../../benchmarks/) with its own controlled protocol.
