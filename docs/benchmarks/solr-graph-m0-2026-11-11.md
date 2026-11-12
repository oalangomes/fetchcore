# Issue #30 M0: Solr graph edge layout spike

## Scope and reproducibility

This is a disposable experiment, not GraphProjection v1 or serving support. It
does not write the knowledge collection. The source is
[`scripts/experiments/solr_graph_m0.py`](../../scripts/experiments/solr_graph_m0.py),
and the live assertion is
[`tests/integration/test_solr_graph_m0.py`](../../tests/integration/test_solr_graph_m0.py).

Run with the project's pinned Solr 9.10.1 image (digest from
`infra/images.lock`) in a fresh SolrCloud container. The production Compose
Solr service uses `solr-precreate`, which is standalone; this command does not
change that service. The script creates a uniquely named one-shard collection
from Solr's `_default` configset and deletes it in `finally`.

```bash
docker run -d --rm --name fetchcore-graph-m0 \
  -p 127.0.0.1:48983:8983 \
  'solr:9.10.1@sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb' \
  solr -c -f
PYTHONPATH=src .venv/bin/python scripts/experiments/solr_graph_m0.py \
  --solr-url http://127.0.0.1:48983/solr --samples 5
FETCHCORE_GRAPH_M0_SOLR_URL=http://127.0.0.1:48983/solr \
  .venv/bin/python -m pytest tests/integration/test_solr_graph_m0.py -q
docker stop fetchcore-graph-m0
```

The same image was run locally on 2026-11-11 with Docker Desktop's Linux
engine reached from WSL. The live pytest passed (1 test, 21.05 s). The five
sample CLI run also passed. No existing Solr collection was attached to this
container.

## Fixture and observed behavior

The 11 edge documents cover a three-link A→B→C→D path, C→A cycle, two distinct
A→B observations, a same-name B in another path, an alternate revision, a
second snapshot, a second repository, and `calls` versus `imports`. An actual
`SemanticRelationEvidence` for an unresolved target is held in the fixture but
excluded from the edge documents. Symbol identities come from
`DeclarationIdentity.key`, so the same declaration has the same identity across
revisions, while homonyms in different paths do not collide.

Each indexed edge stores the canonical source and target identity plus
`repository`, `revision`, `snapshot`, `relation_type`, `source_ref`, checksum,
line and resolution. The edge ID includes the evidence location and context,
so two observations of the same pair survive. No file body or embedding is
copied. Solr's `_default` dynamic fields are used **only** for this spike.

| Probe | Observed result |
| --- | --- |
| `nodes()` at depths 1, 2, 3 | 2, 3, 4 unique nodes including root; D first appears at depth 3 |
| `trackTraversal` | Non-root tuples include `ancestors`; cycle does not duplicate nodes |
| `shortestPath()` A→D, `maxDepth=3` | One A→B→C→D path; `maxDepth=2` returns none |
| Exact pair hydration | Three pair lookups recover four source observations; A→B yields both lines 10 and 11 |
| Scope | Repository, revision, snapshot and relation filters passed on every nested `nodes()` level and `shortestPath()` query; scoped negative paths returned none |
| Homonym and unresolved | Other B is excluded by identity/type filter; unresolved target is not indexed |

The streamed node/path tuples are *not evidence*. Hydration queries the exact
source-node/target-node pair under the same scope and reads every matching edge
document. The stored canonical identities, source reference, checksum and line
are checked against each pair. The fixture does not index a knowledge
collection, so it does not exercise `get_source` or a real canonical-symbol
hydration call; those remain later integration work.

## Measurements

Observed wall times include Python, HTTP, WSL/Docker Desktop forwarding,
SolrCloud and its embedded ZooKeeper. This 11-edge fixture is a compatibility
measurement, not a throughput or production latency benchmark. `min–max` and
median are milliseconds, five sequential samples except hydration's three.

| Operation | Median | Min–max | Samples |
| --- | ---: | ---: | ---: |
| `nodes()` 1 hop | 985.8 | 934.7–1028.1 | 5 |
| `nodes()` 2 hops | 1029.9 | 1025.3–1051.0 | 5 |
| `nodes()` 3 hops | 1030.7 | 1020.3–1046.8 | 5 |
| `shortestPath()` 3 hops | 958.8 | 945.7–1032.2 | 5 |
| Exact edge-pair hydration | 934.3 | 931.2–935.1 | 3 |

Initial batch indexing with commit took 1013.2 ms. The JSON edge payload was
7,791 bytes for 11 documents; that is transfer payload, **not** Solr index
size. The three pair lookups add about 2.8 seconds on this host after the
shortest-path request. No indexing throughput, memory, p95 or real corpus cost
claim is supported by this sample. Existing `get_related` latency from other
fixtures is not directly comparable.

## Limitation found and layout recommendation

The first version indexed only the canonical `symbol:v1:sha256:…` string as a
traversal field. `nodes()` worked, but `shortestPath()` failed with
`undefined field symbol`: its start value was interpreted as Lucene query
syntax. The successful edge-only run retained both canonical identity fields
and added `source_node_s`/`target_node_s` containing the final SHA-256 hex
component. Exact pair hydration checks the canonical identities returned on
each edge. This is an explicit, reversible encoding of the existing identity,
not a new canonical identifier or a node collection. Escaped canonical IDs
were not evaluated.

**Recommendation for M1 design review:** keep one document per resolved
relation observation in a separate graph collection, with canonical identity
and reversible traversal-key fields side by side, plus scope, provenance and a
stable evidence ID. Do not add node documents on the basis of this fixture.
Before a production layout is approved, verify an explicit graph schema,
high-degree fan-out, multi-shard `nodes()`/`shortestPath()`, exact hydration
budgets, and SolrCloud deployment compatibility. The current standalone
Compose topology cannot be assumed to support this streaming traversal.

`shortestPath(maxDepth=3)`, HTTP's 20-second timeout, a 20-row hydration cap,
and a five-tuple assertion bound the *observed* fixture. `nodes()` still has no
proven server-side hard limit on intermediate nodes, memory, or returned
tuples; checking the response length after traversal is not a safe service
budget. A deployment must fail closed on timeout, truncation, unsupported
scope, or stale generation. Publication consistency between knowledge and
graph collections was not tested and belongs to M2. No lexical/hybrid query
or existing snapshot was changed.

Solr 9.10 documentation: [graph traversal and `trackTraversal`](https://solr.apache.org/guide/solr/9_10/query-guide/graph-traversal.html),
[`shortestPath()` and `maxDepth`](https://solr.apache.org/guide/solr/9_10/query-guide/stream-source-reference.html).

## Standalone preflight: M1 blocked

The production topology is Solr 9.10.1 standalone, not SolrCloud. The M0
expressions were re-run against a disposable standalone core created from the
FetchCore configset. Both `nodes()` and `shortestPath()` returned an expression
error, `zkHost not found for collection 'graphprobe'`. This occurs before graph
data is traversed, so the Cloud traversal measurements below do not validate
the deployed topology. The core also rejected the M0 edge field
`source_identity_s` as unknown; `_default` dynamic fields from M0 are not the
FetchCore knowledge schema or a publishable GraphProjection schema.

Reproduce on the pinned image with no existing data volumes:

```bash
SOLR_IMAGE='solr:9.10.1@sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb'
docker run -d --rm --name fetchcore-graph-standalone \
  -p 127.0.0.1:48984:8983 \
  -v "$PWD/infra/solr/configset:/fetchcore-configset:ro" \
  "$SOLR_IMAGE" solr-precreate graphprobe /fetchcore-configset
docker run -d --rm --name fetchcore-graph-cloud \
  -p 127.0.0.1:48985:8983 "$SOLR_IMAGE" solr -c -f
PYTHONPATH=src .venv/bin/python scripts/experiments/solr_graph_m0.py \
  --solr-url http://127.0.0.1:48985/solr \
  --standalone-url http://127.0.0.1:48984/solr --standalone-core graphprobe \
  --samples 5 --output docs/benchmarks/results/solr-graph-preflight-2026-11-11.json
FETCHCORE_GRAPH_M0_SOLR_URL=http://127.0.0.1:48985/solr \
FETCHCORE_GRAPH_STANDALONE_URL=http://127.0.0.1:48984/solr \
FETCHCORE_GRAPH_STANDALONE_CORE=graphprobe \
  .venv/bin/python -m pytest tests/integration/test_solr_graph_m0.py -q
docker stop fetchcore-graph-standalone fetchcore-graph-cloud
```

The probe also verifies a 512-neighbor star with reverse edges (1,024 edge
observations): 513 unique nodes are returned, the cycle does not duplicate the
root, and `shortestPath(maxDepth=1)` returns the direct edge. This is a bounded
fixture, not evidence of a server-side node, memory, or response limit.
Hydrating all three pairs on the four-node path returned all four observations,
including both A→B source lines, with one `select` request. The script records
Solr `RESPONSE_TIME`/`QTime` separately from client wall time. On this host the
in the five-sample artifact, the high-fan-out traversal median was 952 ms
client-side and 18 ms server-side. Batch hydration had a 948 ms client wall time
and 1 ms Solr `QTime`, versus three individual edge-pair lookups at about 926 ms
each. Most observed delay is outside Solr expression execution (HTTP plus the
container/network path). It hydrates edge documents,
not canonical symbol/source objects; application-level hydration remains
unmeasured.

`DeclarationIdentity.key` currently has the exact form
`symbol:v1:sha256:<64 lowercase hex>`. The spike now rejects any other form and
maps that digest to the parser-safe `symbol_v1_sha256_<digest>` traversal key.
This mapping is injective for the validated canonical v1 format and avoids
Lucene's colon query parsing; it does not claim support for future identity
formats.

**M1 is blocked for the current deployment.** Do not silently substitute
`{!graph}`, a different traversal engine, or a Cloud-only serving path. The
separate graph schema and publisher cannot be accepted as compatible until a
supported Standalone traversal/publication design is chosen and tested. M1
projection work that depends on a traversable graph collection is therefore
not implemented in this PR. No knowledge/lexical/hybrid collection was changed.
