# Typed knowledge retrieval

The versioned [Ground Truth](ground-truth.json) pins a real repository and
revision in **corpus data**. It contains 20 acquisition cases (17 positive,
2 negative, one pending ambiguity), five relation cases, and a separate set of 17 positive ranking
queries. Source hashes and exact locators were checked directly against the
pinned checkout, independently of FetchCore output. Case rationales state why
each source was selected; these cases are not a representative statistical
sample of all repositories.

The acquisition check is part of the existing acceptance gate:

```bash
python tests/acceptance/acquisition/run.py --corpus /path/to/pinned/checkout
```

The source checkout must be clean and match the pinned revision and checksums.
The check is offline and backend free. Ranked retrieval, snapshot consistency,
latency and cost remain separate benchmark gates. The synthetic fixture checks
are behavioral acceptance tests and do not establish retrieval quality.

The API preview ceiling now matches the existing 1 MB scanner file ceiling.
This changes admission for eligible files between 256,001 and 1,000,000
characters; the indexing contract version is 27. Existing persisted typed
snapshots require an explicit rebuild before this corpus can be projected.

## Offline and authorized runs

`run.py` first repeats source and canonical attestation. Without
`--execute-authorized` it writes an [offline artifact](artifacts/offline-preflight.json)
with `BLOCKED` for backend snapshot and retrieval fields. The command performs
no service calls:

```bash
python benchmarks/typed-knowledge-retrieval/run.py \
  --corpus /path/to/pinned/checkout --output /tmp/typed-offline.json
```

The [configuration example](run-config.example.json) is **unapproved**. An
authorized run requires an external, explicit operational approval covering
the named BENCH services, endpoints, generated `fc-typed-*` resource prefix,
embedding endpoint, owner and lifecycle. Record that approval in a separate
configuration, then pass both `--config` and `--execute-authorized`. The runner
checks Docker Compose labels, published ports, owned volumes, existing resource
inventory, backend capabilities, and the selected embedding model and dimension
before writing. The runner currently executes in WSL, where the hosted Ollama
endpoint `http://172.21.32.1:11434` responds. BENCH containers reach the same
hosted service through `http://host.docker.internal:11434`. The latter currently
refuses connections from this WSL host, so it is not the runner endpoint. Verify
the exact WSL-reachable address before each run because it can change, and record
it in both `authorization.embedding_endpoint` and `endpoints.ollama`. Do not
substitute WSL loopback or silently fall back to another address. A healthy
endpoint is not authorization. On any mismatch it fails closed. The generated namespace must
be absent on all backends. Resources are retained for review; deletion requires
a separate operator action after checking the recorded before/after inventories.
No baseline, DEV or OPERATIONAL namespace is selected or cleaned up.

After a permitted run, each backend snapshot must match canonical record IDs,
checksums, repository, revision, corpus checksum, indexing and Projection v3
fingerprints, serializer and embedding identity. Ranking begins only after all
snapshots attest. The runner then applies one source deletion incrementally
and compares each backend with the expected canonical state. An exception
leaves a `FAIL` artifact and records newly observed resource names for review.

Each arm declares a backend and supported lexical, dense or hybrid mode.
Solr and OpenSearch additionally exercise their real exact route lookup on
API cases with an effective two-result window; Qdrant has no declared targeted
lookup arm. This acquisition path is scored separately from ranked discovery.
The runner records the canonical route-path candidate count for each case.
An exact-ID score can establish unique targeted acquisition only when the path
has one candidate; repeated methods or requests can make the path ambiguous.
SourceRecall@K checks the source path; EvidenceRecall@K, MRR and nDCG use the
exact reviewed canonical evidence ID. API pointers have no proved line span,
so SpanCoverage and ContextYield are unavailable for API cases. For Markdown
cases, `ContextYield` reuses the frozen baseline definition: best Ground Truth
span coverage in a ranked prefix packed under 100/250/500/1000 **line**
budgets. Useful evidence density is relevant hits divided by returned hits;
returned characters and candidates examined are separate measurements.
Unavailable tokens, cost or time-to-first-candidate appear as `null`, never
as an invented zero. A completed run is `MEASURED`; promoting it to PASS
requires reviewed objective thresholds and comparable isolation evidence.

The [first authorized measurement](artifacts/backend-measurement-projection-r3.json.gz)
completed on projection contract revision 3. Its [result review](../../docs/benchmarks/results/typed-knowledge-retrieval.md)
records real backend queries and the targeted route failure. The
[second authorized measurement](artifacts/backend-measurement-projection-r4.json.gz)
ran against projection contract revision 4 and recovered all six reviewed
unique route paths in both targeted backends. Ambiguous path-only lookups and
performance comparability remain limited as described in the result review.
The [third authorized measurement](artifacts/backend-measurement-projection-r4-quiescent.json.gz)
ran during a temporarily reserved workload window on the current PC and records
`performance_comparable=true`; the
[isolation attestation](artifacts/isolation-attestation-r4.json) specifies its
operational observations and limits. All three typed resource namespaces remain
retained. A further backend run needs a new approval, namespace and output path.
