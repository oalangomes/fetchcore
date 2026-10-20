# BENCH infrastructure and benchmark operations

FetchCore benchmarks measure evidence acquisition, retrieval quality, and operational
cost. They never decide consumer satisfaction or semantic authority. This document
specifies a common operational standard without changing the four suites' native
protocols, equations, Ground Truth, or frozen artifacts.

## Suites, runs and isolation

A **suite** has a stable identifier and owns its benchmark protocol. A **run**
has its own run ID, product revision, native outputs and checksums. Do not encode
issue numbers in suite, resource or run identities.

| Suite | Native runner | Remote BENCH capability |
| --- | --- | --- |
| baseline-v1 | baseline-v1/run_bench.py | Solr, OpenSearch, Qdrant, local |
| search-intent-v2 | search-intent-v2/evidence.py | Solr, OpenSearch, Qdrant, local |
| targeted-exact | targeted-exact/run.py | local only in current benchmark |
| typed-knowledge-retrieval | typed-knowledge-retrieval/run.py | Solr, OpenSearch, Qdrant |

Two infrastructure modes serve different purposes:

- **Functional BENCH**: one persistent Solr, one persistent OpenSearch, one
  persistent Qdrant instance in the already-managed BENCH Docker Compose fleet.
  Each suite has a fixed logical collection/index reservation. There is *not*
  one Docker fleet per suite. Native runners must explicitly bind to the
  selected names and verify snapshot/Projection/embedding identities.
- **Performance BENCH**: a new isolated Docker Compose project per run with
  its own three containers, host ports and volumes. New resources are created
  only after explicit native runner authorization. A new project alone does not
  guarantee exclusive CPU, RAM, I/O or Ollama capacity on the physical host.

Functional resource names: fc-bench-baseline-v1, fc-bench-search-intent-v2,
fc-bench-targeted-exact (reserved; remote targeted measurement is not currently
supported), fc-bench-typed-knowledge. The same logical name in different
backends identifies different physical resources. The typed suite currently
uses fresh resource namespaces for authorized writes and cannot silently
reuse a fixed existing snapshot; the plan is not an implicit index migration.

The persistent BENCH instance continues to be operated by
[scripts/shared_infra.py](../guides/shared-development-infrastructure.md).
The existing [Compose definition](../../infra/compose/shared.yml) is reused
for ephemeral projects. Ollama is an external service; neither mode creates
an Ollama container. New host ports must be available and cannot use the
reserved DEV/BENCH/OPERATIONAL ports. No automatic Docker or resource creation
occurs through read-only planning.

## Common run bundle

For new operational runs, use benchmarks/benchmark_ops.py. This wrapper
invokes the original suite runner and writes its native results under one
run directory outside Git:

~~~text
~/.local/state/fetchcore/benchmarks/runs/<suite>/<run-id>/
  bundle.json           # schema, identity, status, native hashes, review
  native/               # native runner outputs, unmodified
~~~

The sidecar bundle uses execution kind (preflight/smoke/diagnostic/full),
execution status (running/completed/blocked/failed), and independent review
status (unreviewed/accepted/rejected). A successful smoke is not a completed
full benchmark; an offline BLOCKED preflight is not remote retrieval; a
completed measurement is not automatically approved performance. Every
native file is hashed and size-attested. Extra files, changed bytes, symlinks
and unsafe paths invalidate a bundle.

~~~bash
python benchmarks/benchmark_ops.py run \
  --suite typed-knowledge-retrieval --kind preflight \
  -- --corpus "$PINNED_CORPUS"
~~~

For real backend writes, the envelope requires --authorize-writes in addition
to the native suite's independent explicit approval. Do not infer that the
envelope can override a native runner's ownership, snapshot or capability
checks. Config hashes and hashed native arguments are recorded, without
embedding the operator's complete configuration or secrets into bundle.json.

~~~bash
python benchmarks/benchmark_ops.py inspect --run "$BENCH_RUN"
python benchmarks/benchmark_ops.py review --run "$BENCH_RUN" \
  --decision accepted --note "Reviewed protocol, Ground Truth and source artifacts"
python benchmarks/benchmark_ops.py publish --run "$BENCH_RUN" \
  --catalog "$HOME/.local/state/fetchcore/benchmarks/published"
python benchmarks/benchmark_ops.py publish --run "$BENCH_RUN" \
  --catalog "$HOME/.local/state/fetchcore/benchmarks/published" --write
~~~

Publishing is offline, explicit, atomic and immutable. It requires an accepted
full run and validates native files again before copying. The dry-run does
not publish. The operator must inspect native files for sensitive metadata:
baseline bench-run.json includes operator-supplied URLs and identity.
The baseline's stricter native publication validator remains authoritative
for baseline-specific completeness; a generic bundle cannot replace it.
Published files are not automatically committed to Git.

The frozen V0.1–V0.4 baseline is byte-for-byte untouched. Do not backfill
unknown provenance or regenerate historical measurements.

## Performance stack: plan, start, run, retain and stop

Use an operator-selected unique run ID and five distinct unused host ports.
Plan creation is read-only and writes outside the source repository:

~~~bash
export BENCH_RUN_ID=abcdef123456
export BENCH_PLAN="$HOME/.local/state/fetchcore/benchmarks/plan-$BENCH_RUN_ID.json"
python benchmarks/benchmark_infra.py plan \
  --suite typed-knowledge-retrieval --mode performance \
  --run-id "$BENCH_RUN_ID" \
  --ports '{"solr":49183,"opensearch":49200,"opensearch_metrics":49600,"qdrant":46333,"qdrant_grpc":46334}' \
  --output "$BENCH_PLAN"
python benchmarks/benchmark_infra.py up --plan "$BENCH_PLAN" --authorize
~~~

Before starting: verify Docker identity and resource ownership, actual port
availability, WSL/Windows Ollama reachability, host capacity, and explicit
operational authorization. The tool refuses existing project containers or
volumes and unknown ownership rather than adopting them. No DEV/OPERATIONAL
resources are modified.

Bind the native benchmark configuration to the plan's endpoint/project and
fresh resource scope explicitly. In particular, typed-knowledge-retrieval
must still receive a separately approved config with its expected Compose
project, labels, embeddings, and retain-only lifecycle.

~~~bash
python benchmarks/benchmark_ops.py run \
  --suite typed-knowledge-retrieval --kind full --mode performance \
  --run-id "$BENCH_RUN_ID" --infra-plan "$BENCH_PLAN" \
  --authorize-writes -- \
  --corpus "$PINNED_CORPUS" --config "$APPROVED_CONFIG" --execute-authorized
~~~

Persist result, inventories and diagnostics before stopping the project:

~~~bash
python benchmarks/benchmark_infra.py down --plan "$BENCH_PLAN" --authorize
~~~

By default, down **retains volumes** to permit inspection. Only explicit,
separately approved --remove-volumes destroys the project's volumes. Both
actions verify project/container/volume ownership first. Another performance
run must use a new run ID, ports and project. Docker isolation does not set
performance_comparable=true; document host exclusivity and provider load.

Functional routing is inspect-only:

~~~bash
python benchmarks/benchmark_infra.py plan \
  --suite baseline-v1 --mode functional
~~~

This prints fixed reservations and existing BENCH endpoints without creating
indexes or Docker resources. The functional stack lifecycle remains in the
shared infrastructure CLI.

## Validation and regression policy

Tests protect meaningful boundaries: checksums, inventory integrity,
path/symlink safety, status meaning, publication immutability, port collisions
and Docker ownership. They do not simulate retrieval to manufacture quality
improvements. Source/schema/fingerprint incompatibility fails closed.

Use native benchmark measures and an external regression harness when needed.
Historical results are immutable evidence, not a shared mutable database.
Do not re-run the full backend matrix merely to validate the operational
wrapper. See [benchmark catalog](../../benchmarks/README.md) and
[experiment protocol](protocol.md).
