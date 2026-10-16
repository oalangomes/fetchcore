# Shared development infrastructure and resource lifecycle

Status: P0 opt-in local infrastructure. Retrieval contracts, Projection v3,
serializer/embedding fingerprints and frozen benchmark artifacts are unchanged.

## Audit and scope

The legacy root `compose.yml` includes app and Solr, precreates `fetchcore`, and
keeps its existing ports and volumes for compatibility. Independent backend
files exist for Solr, OpenSearch and Qdrant. Ollama previously used an external
endpoint. None of those existing instances is adopted or deleted by this delivery.

`repository-index.yml` already separates reuse-only service discovery from explicit
service/resource provisioning, validates repository-resource binding, and emits
run evidence. Self-dogfood explicitly provisions disposable Solr on hosted runners;
that hermetic CI path remains unchanged. Operational indexing is reuse-only and
now defaults to the isolated operational endpoint/resource below. Backend-neutral
operational indexing parity is tracked separately in issue #123.

Benchmark `search-intent-v2/run.py` accepts routed environment defaults while
retaining explicit CLI arguments. Frozen baseline-v1 and paired `evidence.py`
read manifest endpoints/settings; they intentionally remain unchanged. Do not
assume environment variables override their frozen manifests. For a new dedicated
experiment, create a separately reviewed manifest/harness and record its identity;
never overwrite frozen artifacts or publish shared-host timings as dedicated.

## Instances and endpoints

Use `python scripts/shared_infra.py env --environment dev --repository oalangomes/fetchcore`
to print routing JSON. This has no Docker or filesystem side effects.

| Physical project | Consumers | Solr | OpenSearch / metrics | Qdrant HTTP / gRPC | Ollama |
| --- | --- | --- | --- | --- | --- |
| fetchcore-shared | DEV, functional BENCH | 18983 | 19200 / 19600 | 16333 / 16334 | 21434 |
| fetchcore-bench-dedicated | performance BENCH | 28983 | 29200 / 29600 | 26333 / 26334 | 31434 |
| fetchcore-operational | persistent OPERATIONAL | 38983 | 39200 / 39600 | 36333 / 36334 | 41434 |

Endpoints bind to `127.0.0.1`; Solr has `/solr`. Each project has its own named
`<project>_<backend>-data` volumes and containers. Operational persistence is
physically separate from development containers/volumes, on the same Docker host;
it is not host fault-domain isolation or a production deployment.

Logical names are `fc-<environment>-<repo-name-first-24>-<sha256(owner/name)-first-16>`.
Repository identity must be canonical lowercase `owner/name`. Full repository
identity remains in indexed provenance and existing repository binding validation;
the hash is a naming aid, not authorization. No revision, schema or fingerprint is
encoded into a new identity or silently rewritten. DEV and BENCH use different
logical resources even when sharing a service. A service volume can contain many
repositories; never delete it to clean up one repository.

## Explicit service and resource preparation

Review the image versions in `infra/compose/shared.yml` and existing backend files.
Set `FETCHCORE_OLLAMA_IMAGE` to an explicitly reviewed tag or digest when selecting
the optional Ollama service in `infra/compose/ollama.yml`. Other backends do not
require Ollama. No Ollama model is pulled by this tooling.

```bash
python scripts/shared_infra.py up --environment dev --repository oalangomes/fetchcore --backend solr
python scripts/shared_infra.py run --environment dev --repository oalangomes/fetchcore -- fetchcore readiness --json
```

Only `up` can provision services. It refuses reserved projects/volumes without
matching ownership labels. It starts only the selected backend, never the app or
indexer, and Solr starts without precreating a logical core. Repeated `up` reuses
owned instances. There is no discovery-triggered fallback to a new instance.
`run` only supplies routing to the requested command; the command's own mutation
semantics remain explicit. Missing services/resources fail in that command.

Prepare a new Solr logical resource explicitly with the existing
`python -m fetchcore.operational.solr_runtime prepare` command: use the emitted
URL, resource and repository, `--provision-mode reuse_only`, and only when intended
`--create-if-missing`. Then explicitly run `fetchcore index bootstrap`, registration,
plan and sync using the routed environment. Bootstrap remains Projection v3.
OpenSearch/Qdrant logical preparation remains adapter-specific; P0 does not add a
cross-backend provisioning framework or operational-indexing support.

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

Hermetic tests cover routing, invalid identity, no implicit provisioning, volume
adoption refusal, ownership mismatch, safe cleanup and resource isolation. Compose
can be rendered without starting services. No retrieval tuning or schema migration
is introduced. Run live service/resource preparation and readiness on an explicitly
allocated host before operational migration; do not use an existing shared index
as a smoke-test target.

Local validation on 2026-10-16: Python 3.11 suite **828 passed, 7 skipped**;
Ruff, mypy (171 source files), wheel/sdist build, Markdown links and legacy/new
Compose rendering passed. Frozen ContextYield was regenerated offline with metrics
fingerprint `sha256:f021672682b6cee1ae420636b567924321ab65ab894580be738ab0241d17b693`.
No canonical/retrieval source or frozen baseline artifact changed. This establishes
code/config compatibility, not a new live retrieval quality or latency measurement.
Python 3.13 additionally exposed the existing repository symlink-loop error-category
failure; repository materialization was outside this change. Localization inventory
reports 33 missing and 2 stale translations, with no orphan files; generated PT-BR
translations were not rewritten by this infrastructure delivery.

Docker host access and Compose rendering were available. New services and indexes
were not started or mutated during validation. Live backend readiness, dedicated
host contention verification, explicit operational bootstrap/sync and consumer
cutover remain operator deployment steps before enabling the operational workflow.
