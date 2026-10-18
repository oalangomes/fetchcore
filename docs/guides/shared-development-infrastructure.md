# Shared development infrastructure and resource lifecycle

Status: shared DEV/BENCH/OPERATIONAL local infrastructure. Retrieval contracts, Projection v3,
serializer/embedding fingerprints and frozen benchmark artifacts are unchanged.

## Audit and scope

The legacy root `compose.yml` includes app and Solr, precreates `fetchcore`, and
keeps its existing ports and volumes for compatibility. Independent backend
files exist for Solr, OpenSearch and Qdrant. Ollama is reused as a host/provider
service; it is not started or duplicated by this tooling. Existing shared backend
instances can be selected explicitly and are never adopted as owned Compose projects.

`repository-index.yml` already separates reuse-only service discovery from explicit
service/resource provisioning, validates repository-resource binding, and emits
run evidence. Self-dogfood explicitly provisions disposable Solr on hosted runners;
that hermetic CI path remains unchanged. Operational indexing is reuse-only and
now defaults to the isolated operational endpoint/resource below. Backend-neutral
operational indexing parity is tracked separately in issue #123.

Benchmark `search-intent-v2/run.py` accepts routed environment defaults while
retaining explicit CLI arguments. Frozen baseline-v1 and paired `evidence.py`
read manifest endpoints/settings; they intentionally remain unchanged. New
baseline-v1 runs use `run_bench.py --run-config` for explicit BENCH endpoints/resources,
owner, policy and embedding identity. That file and output must live outside the
frozen artifact directory. Shared-host timings are functional observations;
comparable performance requires dedicated BENCH and exclusive-host attestation.

## Instances and endpoints

Use `python scripts/shared_infra.py env --environment dev --repository oalangomes/fetchcore`
to print routing JSON. This has no Docker or filesystem side effects.

| Physical project | Consumers | Solr | OpenSearch / metrics | Qdrant HTTP / gRPC | Ollama |
| --- | --- | --- | --- | --- | --- |
| fetchcore-shared | DEV, functional BENCH | 18983 | 19200 / 19600 | 16333 / 16334 | existing host service |
| fetchcore-bench-dedicated | performance BENCH | 28983 | 29200 / 29600 | 26333 / 26334 | existing host service |
| fetchcore-operational | persistent OPERATIONAL | 38983 | 39200 / 39600 | 36333 / 36334 | existing host service |

Endpoints bind to `127.0.0.1`; Solr has `/solr`. Each project has its own named
backend data volumes and containers. Ollama is not part of these projects and has
no per-environment port or volume. Operational persistence is
physically separate from development containers/volumes, on the same Docker host;
it is not host fault-domain isolation or a production deployment.

Logical names are `fc-<environment>-<lowercase-repo-name-first-24>-<sha256(exact-owner/name)-first-16>`.
Repository identity must be canonical `owner/name` and retains its original case. Full repository
identity remains in indexed provenance and existing repository binding validation;
the hash is a naming aid, not authorization. No revision, schema or fingerprint is
encoded into a new identity or silently rewritten. DEV and BENCH use different
logical resources even when sharing a service. A service volume can contain many
repositories; never delete it to clean up one repository.

### Reuse existing shared services

Copy [the endpoint example](../../infra/shared-endpoints.example.json) to an
operator-owned location and inspect the existing services before selecting them:

```bash
python scripts/shared_infra.py env --environment dev --repository oalangomes/fetchcore \
  --shared-endpoints /absolute/operator/shared-endpoints.json
python scripts/shared_infra.py run --environment bench --repository oalangomes/fetchcore \
  --shared-endpoints /absolute/operator/shared-endpoints.json -- fetchcore doctor --json
```

The JSON accepts only `solr`, `opensearch`, `qdrant`, and `ollama` HTTP(S) URLs.
Solr URLs must end in `/solr`; credentials, query strings and fragments are rejected.
Only shared DEV/BENCH endpoints change. Resource names remain environment-specific;
indexing and serving receive the same Solr endpoint. OPERATIONAL and dedicated
BENCH ignore these overrides. Reserved isolated-service ports are rejected, but an
operator must also verify that DNS aliases/proxies do not hide a shared physical
service. Endpoint selection does not grant ownership of an external container.
`up` refuses a selected external backend; use its existing operator lifecycle.
Omitted backends retain their managed defaults; an unavailable endpoint fails explicitly.

Host-process and Docker URLs are different configurations. A host process can use
published loopback ports; containers use service DNS on the correct network.
For Ollama, resolve the operator runtime first: `FETCHCORE_CONFIG` selects the
operator YAML, `FETCHCORE_OLLAMA_URL` overrides its URL, and `OLLAMA_URL` is the
legacy environment fallback. The runtime loader records the winning source.
`shared_infra.py` defaults to `host.docker.internal:11434` for Docker callers;
when running in WSL, pass the verified WSL-reachable URL through `--ollama-url`.
Do not assume that WSL loopback reaches the Windows Ollama service. Confirm
`/api/tags` lists the required model and `/api/embed` returns the configured
dimension before dense indexing. Record the chosen URL and its configuration
source; an unreachable or mismatched provider blocks the operation rather than
triggering an endpoint fallback. This tool never creates an Ollama instance.
`shared_infra.py app` starts only API, MCP and web after backend service readiness,
using `infra/compose/shared-app.yml` and the already created network. It never
prepares a logical resource. One `fetchcore-lab` serves authorized DEV and BENCH
targets on web/MCP ports 5173/8766; OPERATIONAL remains on its isolated backend
project and is not a Lab target. For an operator-managed external
shared backend, pass `--shared-endpoints` and an explicit container-reachable
`--container-url` to `app`; host loopback addresses are not valid container DNS.
`host.docker.internal` may reach host Ollama from Docker even when it is unreachable
from WSL. The routing default is `http://host.docker.internal:11434`; pass
`--ollama-url` when a host process has a different reachable endpoint. Do not replace
it with container `localhost` or start another Ollama. `FETCHCORE_BACKEND` selects
`solr`, `opensearch`, or `qdrant-vector` (`--backend qdrant` maps to the latter);
`FETCHCORE_INDEX_BACKEND` and its endpoint/resource settings select that same backend.
The indexing CLI consequently rejects OpenSearch/Qdrant with its existing
`unsupported_backend` error (operational capability unavailable, #123). Selection
must never redirect an unsupported indexing operation to Solr.

## Explicit service and resource preparation

Review the image versions in `infra/compose/shared.yml` and existing backend files.
The infrastructure CLI never starts the optional standalone Compose Ollama service.
Other backends do not require Ollama. No Ollama model is pulled by this tooling.

```bash
python scripts/shared_infra.py up --environment dev --repository oalangomes/fetchcore --backend solr
python scripts/shared_infra.py ready --environment dev --repository oalangomes/fetchcore --backend opensearch
python scripts/shared_infra.py prepare --environment dev --repository oalangomes/fetchcore --backend opensearch
python scripts/shared_infra.py prepare --environment dev --repository oalangomes/fetchcore \
  --backend qdrant --vector-dimensions 768
```

Selecting an environment or backend with `env`, `ready` or `run` never starts
containers or creates indexes/collections. `up` is explicit and starts only the
selected service, then waits for its HTTP readiness endpoint. It refuses reserved projects/volumes without
matching ownership labels. It starts only the selected backend, never the app or
indexer, and Solr starts without precreating a logical core. Repeated `up` uses
`--no-recreate` and reuses owned instances, even if Compose configuration changed.
Applying a changed container configuration requires a separately reviewed explicit
Compose recreation. There is no discovery-triggered fallback to a new instance.
`run` only supplies routing to the requested command; the command's own mutation
semantics remain explicit. Missing services/resources fail in that command.

`prepare` is explicit and idempotent for OpenSearch and Qdrant: without
`--create-if-missing`, it only validates/reuses the selected repository alias,
Projection v3 mapping and vector dimensions. With that flag it creates an empty
schema/resource only when absent; it never overwrites, rebuilds, deletes or indexes
documents. Qdrant requires an explicit positive `--vector-dimensions` value for both
validation and creation. OpenSearch reports the schema capabilities actually
present (for example, existing indexes without a vector mapping are lexical-only).
Solr resource preparation continues to use its existing Solr runtime.

The shared Solr startup script installs the pinned configset into
`/var/solr/data/configsets/fetchcore` (standalone's default configset base), without
creating a core. An existing different configset fails startup instead of being
overwritten. A bind mount under `/opt/solr/server/solr/configsets` alone is insufficient
for CoreAdmin CREATE with this image. Existing external services need a separately
verified server-side configset; preserve broken/old configsets and install a distinct
named copy when necessary, then pass its name to `prepare --configset`.

Prepare a new Solr logical resource explicitly with the existing
`python -m fetchcore.operational.solr_runtime prepare` command: use the emitted
URL, resource and repository. Use `--provision-mode provision_if_missing`
with `--create-if-missing` only for an intentional new resource; subsequent
checks use `--provision-mode reuse_only`. Then explicitly run `fetchcore index bootstrap`, registration,
plan and sync using the routed environment. Bootstrap remains Projection v3.
OpenSearch/Qdrant infrastructure preparation creates only empty schemas and aliases;
it does not create an indexed snapshot. Their operational index lifecycle remains
unavailable pending #123 (`unsupported_backend` in the current indexing CLI,
reported as an unsupported operational capability). Do not switch operational consumers to those
resources until the indexing lifecycle is explicitly implemented and validated.

`run` exports host-process `FETCHCORE_*` settings, not container DNS or MCP-specific
configuration. Use the emitted values explicitly for MCP-specific settings, or
`--solr-url`/`--collection` in dogfood. Existing explicit CLI settings take precedence;
review them before any mutation. It is not a sandbox for arbitrary commands.

## Operational migration

Before merging/enabling the new workflow default, explicitly provision the
operational service, create the emitted operational resource, register the same
repository and bootstrap/sync it. Verify readiness and revision/fingerprints before
switching consumers. The old `fetchcore` core and volumes are retained. There is no
automatic copy, adoption, rebuild or deletion. Until preparation finishes the
workflow fails closed instead of indexing development.

`FETCHCORE_OPERATIONAL_SOLR_URL` and `FETCHCORE_OPERATIONAL_SOLR_COLLECTION` repository
variables override the workflow defaults for an operator-managed isolated service.
Do not point these at DEV/BENCH. Existing external operational installations can
retain their resource name through these variables after verifying physical isolation.

## Dedicated performance and cleanup

```bash
python scripts/shared_infra.py up --environment bench --dedicated --repository oalangomes/fetchcore --backend solr
python scripts/shared_infra.py run --environment bench --dedicated --performance --exclusive-host --repository oalangomes/fetchcore -- python benchmarks/search-intent-v2/run.py --help
python scripts/shared_infra.py cleanup --environment bench --dedicated --repository oalangomes/fetchcore --confirm-project fetchcore-bench-dedicated
```

Replace `--help` with the existing workspace/revision/scenarios/output/label arguments
after explicit logical-resource preparation. Performance mode requires dedicated
BENCH and an operator attestation that no competing workload is running. Separate
containers do not reserve CPU, disk, memory or GPU: stop other workloads or use a
separate host before attesting. P0 provides no distributed reservation or automatic
contention detector. Record host, source revision, routing JSON, service images,
model digest, warmups, latency and baseline alongside experiment artifacts.

Shared BENCH is for functional comparisons. Serialize writers to the same resource;
dedicated BENCH is a single operator reservation, not a per-run scheduler.

Cleanup is instance-wide for dedicated BENCH only. It verifies every container's
project and owner labels before removing any container. Wrong ownership, project,
environment or missing confirmation refuses deletion. It never deletes volumes,
logical resources, models, DEV or OPERATIONAL, and never invokes global prune.
Persistent data retirement requires an operator's separate reviewed action after
checking repository binding and fingerprints. Docker administrator access can forge
labels; this is an accidental-deletion boundary, not a hostile-admin security model.

## Validation boundaries

Hermetic tests cover routing, backend selection, invalid identity, no implicit provisioning, volume
adoption refusal, ownership mismatch, safe cleanup and resource isolation. Compose
can be rendered without starting services. No retrieval tuning or schema migration
is introduced. `ready` checks actual HTTP health; for Qdrant this is an external
readiness probe because the upstream image has no in-container HTTP client.
Run live service/resource preparation and readiness on an explicitly allocated host
before operational migration; use existing shared indexes read-only for retrieval
validation and never overwrite them as a smoke-test target.

Initial P0 validation on 2026-10-16: Python 3.11 suite **828 passed, 7 skipped**;
Ruff, mypy (171 source files), wheel/sdist build, Markdown links and legacy/new
Compose rendering passed. Frozen ContextYield was regenerated offline with metrics
fingerprint `sha256:f021672682b6cee1ae420636b567924321ab65ab894580be738ab0241d17b693`.
No canonical/retrieval source or frozen baseline artifact changed. This establishes
code/config compatibility, not a new live retrieval quality or latency measurement.
Python 3.13 additionally exposed the existing repository symlink-loop error-category
failure; repository materialization was outside this change. Localization inventory
reports 33 missing and 2 stale translations, with no orphan files; generated PT-BR
translations were not rewritten by this infrastructure delivery.

The original branch validation above did not start services. The subsequent
[local execution report](../operations/shared-infra-p0-local-validation.md) records
actual provisioning, live checks, persisted artifacts and daily commands. Its
shared-host timings are functional observations, not a performance baseline.
The [consumer migration validation](../operations/shared-infra-consumer-validation.md)
records the later full suite, live BENCH benchmark, cross-surface search and
restart checks for PR #183.
