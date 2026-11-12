# SolrCloud OPERATIONAL cutover runbook

This runbook covers the OPERATIONAL Solr writer and its SDK, CLI, MCP, and API
consumers. It does not authorize a cutover. Do not change repository variables,
consumer configuration, or the existing Standalone resource until the operator
explicitly approves the change.

## Audited state

The last verified automatic indexing run used Solr Standalone, not SolrCloud.
The repository currently has only the `FETCHCORE_OPERATIONAL_OLLAMA_URL`
variable; the SolrCloud routing variables are absent. The
[`operational-index.yml`](../../.github/workflows/operational-index.yml)
workflow therefore continues to use the Standalone endpoint, collection, and
configset defaults. Automatic indexing remains a strict `sync`; it must fail
when no compatible snapshot exists.

The existing local OPERATIONAL Standalone service is Solr 9.10.1, exposed on
loopback port 38983, with the independent
`fetchcore-operational_solr-data` volume. Do not stop it, reuse its volume for
Cloud, or run cleanup commands with volume removal.

An isolated local validation Cloud has been started from
[`infra/compose/solr.yml`](../../infra/compose/solr.yml):

| Resource | Observed value |
|---|---|
| Solr | 9.10.1, one active node |
| Endpoint | `http://127.0.0.1:18984/solr` (loopback only) |
| Configset | `fetchcore-projection-v3` |
| Data volume | `fetchcore-operational-cloud-validation_solrcloud-data` |
| ZooKeeper | Embedded with the Solr process; persisted under the Cloud data volume |

This is a disposable single-node validation environment, not a production or
high-availability deployment. It does not establish that an external,
persistent SolrCloud service or an external ZooKeeper ensemble is provisioned.
The consumer containers still use the Standalone runtime file
[`operational-mcp.runtime.yml`](../../infra/compose/operational-mcp.runtime.yml).
Its configured serving revision differs from the currently indexed repository
revision, and the API readiness check previously reported the snapshot as
incompatible. Consumers have not been switched.

The Cloud full-rebuild workflow run `43131644383` was started against the
isolated validation alias. Record its final status, artifact evidence, and
retrieval results here before treating Cloud validation as complete. Its
dispatch did not alter OPERATIONAL repository variables or the Standalone.
The rebuild step has completed and the alias currently resolves to a versioned
generation. The live Cloud snapshot reports repository `oalangomes/fetchcore`,
branch `master`, revision
`a607d8105029ee5164aa9d008fbcd9a7fcc82b94`, 5,584 documents, corpus checksum
`ca683e4273a64d5fc9593e313abf57d590170b121bdf079b852346a731565dad`,
Projection v3 fingerprint
`sha256:4390897bae3636a7d79fb5318e80d79358053c8e4de0cfb6f295b884fd4c7a20`,
and Ollama `embeddinggemma` at 768 dimensions with serializer
`projection-v3` version 2. These values match the last inspected Standalone
snapshot. The workflow's identity verification and hybrid readiness steps
passed. The workflow overall failed at MCP evaluation because its shell
conversion passed `lexical`, `dense`, and `hybrid` as one literal argument;
the failure-manifest step also rejected the valid `full_rebuild` plan mode.
Both issues have local code fixes and regression tests, but a workflow rerun
against the patched branch has not yet occurred. Consequently, this run supplies
no Cloud MCP relevance result and is not end-to-end success.

The Cloud full rebuild took 417,256 ms, writing 5,584 documents. Embedding
telemetry reported 5,584 vectors in 28 requests, 217,684 ms embedding time, and
25.652 vectors/second. No provider/platform monetary cost was reported. These
are rebuild measurements, not directly comparable to the prior Standalone
incremental run (50 added, 246 updated, 4 deleted; 296 vectors, 16,324 ms
embedding time, 2 requests, 18.133 vectors/second). The workloads differ, so
they do not establish a performance win or regression.

The local Cloud MCP lexical evaluation and the read-only Standalone lexical
evaluation used the same six `fetchcore-self.yaml` scenarios, repository
revision, and query mode. Both passed 5/6 scenarios with MRR 0.6167 and the same
miss (`targeted-incremental-sync`); all `get_source` checks succeeded. Cloud
search latency was 1,033.5 ms p50 / 1,128 ms p95 (6,271 ms total); Standalone
was 1,052.5 ms p50 / 1,547 ms p95 (6,780 ms total). This is one sequential run
per backend, not a controlled latency benchmark. It shows no observed lexical
quality difference for these scenarios; it does not certify dense or hybrid
retrieval.

The live SolrCloud integration test passed against the isolated Cloud. It
covered rebuild, incremental add/update/delete, no-op, post-write rollback,
alias-promotion response loss, verified rollback after post-promotion
validation failure, stale-lock fail-closed behavior and ownership inspection,
provenance, `find_symbol`, and `get_related`. After the test, only the isolated
Cloud container was restarted. Reconciliation remained ready and read-only with
the same active generation, 5,584 documents, and corpus checksum. Lexical
retrieval, MCP source reads, `find_symbol` (1 result), and `get_related` (16
import observations) worked against the persisted snapshot after restart.
Restart recovery is proven only after an indexing run has quiesced; interruption
during a write was separately reproduced against the then-current in-place
writer and exposed mixed-revision content, as detailed below. The staged
writer's crash behavior is covered separately below. Concurrent-writer behavior
remains unverified.

Three isolated lock cycles measured median SolrCloud collection creation at
1,518.642 ms and removal at 1,174.230 ms. The median full lock acquire/release
cycle was 4,769.220 ms, including ownership write and verification. These
measurements are from the local single-node validation Cloud and are not
production latency estimates or monetary cost.

### Full-projection staging benchmark

A disposable alias was pointed at the validated 5,584-document Cloud
generation. The generation manager copied 5,585 projection records (documents
plus snapshot), preserving all 5,584 stored vectors without embedding requests.
The checksummed copy took 110,237 ms; collection setup, validation, publication,
and the writer-lock cycle brought the measured manager call to 274,412 ms.
The harness used an unchanged corpus and only refreshed snapshot indexing time;
it measures full-copy and publication overhead, not a representative changed
file delta or production throughput. The source alias was unchanged and all
temporary resources were removed. The local Cloud reported no monetary cost.

### Incremental crash-recovery finding

A disposable alias was seeded at revision
`842d5354afe9ec32fa3df09730d71764d5492b1a` with 2 documents and checksum
`1801d1d4822c2f606aaf7254aec45df539bc0f9e534c041b16609bec6dfabd4a`. An
incremental worker was terminated immediately after Solr accepted a write for
revision `9c49ada3916204bf2c249377e777d97b76bf2c67`, then the isolated Solr
container was restarted. The alias still exposed the old snapshot metadata,
but 3 documents loaded with checksum
`c076307cffcc841921805617444d553d9aaee47fe7c5ca6b3bef5a8b642dbb5d`; content
from the new revision was searchable while requests were scoped to the old
snapshot. Reconciliation failed closed with `repository_resource_collision`
(physical collection count 4, loaded document count 3, snapshot count 2).

The worker's orphan lock remained. After verifying its repository, alias, and
token ownership and confirming the worker had stopped, the test removed that
exact lock, preserved the damaged generation, pointed only the disposable
alias at an owned empty seed, and ran an explicit full rebuild. The rebuilt
revision `9c49ada3916204bf2c249377e777d97b76bf2c67` had 2 documents and checksum
`ad2e3bbc5a5693ed01ea70375ce34a3edf6a4f2e6ed41afb91a82f2ef2e9f28a`; the
subsequent reconciliation was ready/read-only. The test resources were then
removed. This demonstrates operator-assisted recovery by rebuild for the
in-place writer; it does not demonstrate automatic resume or safe reads during
an interrupted in-place update.

The in-place protocol's writer lock and caught-exception compensation are
insufficient for continuous readers: process termination bypasses compensation,
the lock does not fence retrieval, and the active alias can serve mixed-revision
content under a stale snapshot. Do not treat the old snapshot metadata as
evidence that the active contents remain valid.

Alternatives considered:

| Protocol | Benefit | Cost or limitation | Assessment |
|---|---|---|---|
| Keep in-place writes and compensation | Lowest indexing overhead; already supports ordinary rollback | Crash case above exposes partial content; lock only excludes writers | Not acceptable for cutover |
| Stage a new version and publish it atomically | Readers keep using the old generation until validation and alias publication succeed | Copies the full projection on each changed sync; requires temporary storage and measured copy/publication cost | Implemented as a prototype; operational scale and HA remain unproven |
| Fence reads with a lock and generation/snapshot checks | Could avoid copying the full corpus | Every retrieval surface must honor a race-safe fence; adds read-path latency and coordination; protocol is not implemented or proven | Alternative requiring a formal race proof and cross-consumer tests |
| Quiesce all consumers during each sync | Avoids serving while mutation is in progress | Requires coordinated downtime across SDK, CLI, MCP, and API; does not meet continuous operation | Not suitable as the target operating mode |

The Cloud incremental path now stages a unique physical generation, copies and
checksum-validates the active raw projection (including stored vectors), applies
the existing delta sync to that copy, validates its snapshot, and then promotes
the stable alias. Readers continue using the prior generation until promotion.
The writer lock covers copy through publication. A no-op is detected against
the active snapshot before staging, so it does not copy or publish a generation.
Rebuild and incremental publication share the alias-promotion verification.

The expanded live SolrCloud test terminated a worker after Solr accepted an
update to the staged collection. The stable alias continued serving the
complete prior generation; a subsequent successful sync published the new
generation. The test also covers failed staging, alias-promotion response loss,
post-publication validation/rollback, vector-preserving raw copy, and no-op.
The 27 focused Solr unit tests pass. These results validate the protocol on the
disposable single-node Cloud only; they do not prove production HA behavior,
cross-process concurrency, or that retained generations can be safely garbage
collected.

A process crash can leave an unpublished staging collection and an orphan
writer lock. The old alias remains active, but the next writer must fail closed
until the stopped writer, lock ownership, alias target, and staging collection
are inspected. Do not automatically delete either resource. The prototype
copies the full repository projection on every changed sync, including vectors,
so storage/write amplification and copy/publication latency must be measured at
the full corpus size before cutover. No ranking tuning was performed.

The MCP evaluation was run through the public stdio transport with explicit
Cloud and Standalone endpoints; this did not reconfigure the long-running MCP
or API containers. A separate read-only CLI search and Python retrieval
application-service search, both using temporary Cloud runtime configuration,
each returned 5 results from the Cloud alias. The CLI readiness check reported
service, schema, snapshot, indexing fingerprint, and retrieval ready, but
overall readiness failed because this checkout had uncommitted repository
changes (`currentness=stale/changes_pending`). This proves the isolated routing
path, not clean-workspace consumer readiness. `find_symbol` and `get_related`
were exercised by the live integration test. The repository has no HTTP API
server implementation; the API container is outside this checkout and its
previously observed readiness failure against a stale Standalone runtime
remains unverified against Cloud. No actual dense or hybrid retrieval
evaluation succeeded; hybrid readiness alone does not establish it.

## Preconditions for a real Cloud service

Before any cutover is considered, an operator must provision and independently
verify:

1. A dedicated SolrCloud service pinned to Solr 9.10.1 and the validated
   Projection v3 configset. Do not reuse the Standalone volumes or resources.
2. Persistent Solr data and persistent ZooKeeper state appropriate to the
   deployment. The local single-node embedded-ZooKeeper setup is not a
   substitute for a production topology.
3. Network routes and TLS/authentication policy for the self-hosted indexing
   runner and every serving consumer. The Solr URL must be credential-free;
   endpoints containing credentials are not supported by this workflow.
4. An alias dedicated to `oalangomes/fetchcore`, resolving to exactly one
   owned collection. Physical generation collections must be unique and
   versioned. Verify collection/configset ownership before use.
5. Ollama reachability from the indexing runner and consumers, with
   `embeddinggemma` returning 768-dimensional vectors. Do not download models
   as part of this runbook.
6. A rollback window, backup/retention policy, operator on call, and a recorded
   pre-cutover Standalone snapshot identity.

Keep GraphProjection, Qdrant, and OpenSearch outside this migration.

## Validate an isolated Cloud target

Use a dedicated validation alias and a disposable Cloud resource. Never point
this validation at the OPERATIONAL Standalone alias or volume. The following
dispatch illustrates the `repository-index.yml` inputs; replace the marked
endpoints only with operator-approved, credential-free values:

```bash
gh workflow run repository-index.yml --ref master \
  -f target_repository=oalangomes/fetchcore \
  -f target_ref=master \
  -f target_branch=master \
  -f fetchcore_ref=master \
  -f solr_url='<CLOUD_BASE_URL>/solr' \
  -f solr_deployment_mode=solrcloud \
  -f collection='<VALIDATION_ALIAS>' \
  -f solr_configset=fetchcore-projection-v3 \
  -f create_if_missing=false \
  -f index_lifecycle=rebuild \
  -f embedding_provider=ollama \
  -f embedding_model=embeddinggemma \
  -f embedding_dimensions=768 \
  -f ollama_url='<RUNNER_REACHABLE_OLLAMA_URL>' \
  -f evaluate=true \
  -f evaluation_modes_json='["lexical","dense","hybrid"]' \
  -f evaluation_gate=true
```

Inspect the exact run and download its evidence before proceeding:

```bash
gh run list --workflow repository-index.yml --limit 5
gh run view <RUN_ID>
gh run download <RUN_ID> --dir <OPERATOR_OWNED_EVIDENCE_DIRECTORY>
```

The rebuild must verify repository identity, branch, revision, Projection v3
schema and fingerprints, document count, checksum, and embedding identity
(`ollama/embeddinggemma`, 768 dimensions). Compare lexical, targeted, dense,
and hybrid retrieval against the same source revision and ground truth on
Standalone and Cloud. Keep ranking parameters fixed; attribute differences to
the environment or backend only after inspecting match evidence and provenance.
The MCP dogfood workflow is not by itself proof of `find_symbol`, `get_related`,
SDK, CLI, or API readiness; validate each of those separately against the
Cloud alias.

On the disposable Cloud, also prove:

- one added file, one changed file, and one deleted file are reflected by
  incremental `sync`, with provenance and snapshot checksum intact;
- a second unchanged `sync` is a no-op;
- the process-interruption case leaves readers on the prior complete generation;
  verify the orphan-lock/staging recovery procedure before retrying the writer;
- an orphaned writer lock blocks new writers until its repository/alias/token
  ownership has been checked;
- injected staging failures leave the prior generation active, while
  post-publication failures restore the prior verified alias or return an
  explicit indeterminate/recovery-required result;
- creation and removal latency of the distributed lock are measured separately
  from indexing and embedding time.

Do not delete an orphan lock or generation unless the writer is stopped and
ownership has been verified. A failed or indeterminate publication is a stop
condition, not permission to rebuild or switch aliases.

## Authorized activation

Proceed only after the isolated rebuild, incremental/resilience checks,
consumer checks, and rollback rehearsal have passed and the operator explicitly
authorizes cutover.

Set repository variables for the writer in one approved change. The following
commands are instructions only; they have not been executed:

```bash
gh variable set FETCHCORE_OPERATIONAL_SOLR_MODE --body solrcloud
gh variable set FETCHCORE_OPERATIONAL_SOLRCLOUD_URL --body '<CLOUD_BASE_URL>/solr'
gh variable set FETCHCORE_OPERATIONAL_SOLRCLOUD_ALIAS --body '<OPERATIONAL_ALIAS>'
gh variable set FETCHCORE_OPERATIONAL_SOLRCLOUD_CONFIGSET --body fetchcore-projection-v3
```

Preserve `FETCHCORE_OPERATIONAL_OLLAMA_URL` unless the operator has separately
verified and approved a replacement. Do not set these variables until the
OPERATIONAL alias exists, its ownership is proven, and an authorized full
rebuild has produced a compatible snapshot.

Update the writer and all consumers as a coordinated change:

1. The writer is routed by the four repository variables above. Keep the
   automatic `operational-index.yml` lifecycle as strict `sync`; never change
   it to rebuild-on-failure.
2. Update the operator-owned runtime configuration for SDK and CLI callers
   (`FETCHCORE_CONFIG`, or the equivalent explicit SDK bootstrap) to use the
   Cloud alias, Cloud URL, repository identity, serving revision, Projection
   v3, and the same dense/hybrid embedding identity.
3. Update the MCP and API runtime configuration using the same contract, then
   restart only those serving processes. Do not assume changing the workflow
   variables updates long-running consumers.
4. Verify each consumer reports the same backend, alias, repository, revision,
   projection/indexing fingerprints, and embedding identity before accepting
   traffic. A consumer that still points to Standalone is not migrated.

## Success criteria

Cutover is successful only when all of the following are true:

- The automatic flow passes for a real `push master` only after CI succeeds.
- `operational-index.yml` performs Cloud incremental `sync`, writes the expected
  snapshot, and records the exact source SHA and evidence artifact.
- A second run with no repository change reports no-op; a changed file does not.
- Any backend, schema, fingerprint, embedding, lock, or snapshot failure is
  visible and fails the run. No fallback masks it and no incompatible snapshot
  is silently rebuilt.
- SDK, CLI, MCP, and API all return provenance from the same expected revision;
  targeted lookups include `find_symbol` and `get_related`.
- Lexical, targeted, dense, and hybrid checks pass their recorded ground truth.
- Latency, throughput, embedding time, lock create/release latency, document
  counts, checksums, and any cost are reported. Do not claim a cost value when
  the provider or platform did not expose one.

## Rollback

1. Stop or disable new writer runs through the approved operational procedure;
   wait for any active run to finish. Do not terminate an in-flight writer
   while its publication state is unknown.
2. Inspect the Cloud alias, active and retained generations, lock ownership,
   snapshot identity, and workflow evidence. Preserve the Cloud resources for
   investigation; do not delete the active or prior generation.
3. Confirm that the retained Standalone snapshot is compatible with the
   intended serving revision, repository, configset/projection, indexing
   fingerprint, and embedding identity. The recorded Standalone snapshot may be
   stale; do not equate its presence with readiness.
4. After explicit rollback authorization, restore writer routing to the
   verified Standalone endpoint and collection. Restore SDK, CLI, MCP, and API
   runtime configuration together and restart the consumers. If Standalone
   needs an update, use only an explicitly authorized compatible `sync`; do not
   rebuild it as an automatic rollback step.
5. Verify the same success criteria against the selected Standalone snapshot.
   Keep both Cloud and Standalone data volumes until the rollback window closes
   and retention is explicitly approved.

## Indeterminate state

If a request times out during a write or alias publication, do not infer
whether it succeeded. Stop further writers, preserve all collections and
volumes, inspect the alias target and the active snapshot, compare ownership,
revision, count, checksum, and fingerprints, and retain the workflow logs.
For the historical in-place protocol, the reproduced failure left old snapshot
metadata alongside searchable new-revision content; never serve that damaged
generation. With the immutable-generation prototype, a crash before alias
promotion should leave readers on the prior generation, but the alias target
and snapshot must still be verified. If promotion outcome is indeterminate,
or the active generation fails identity/checksum validation, stop serving and
preserve all resources. The current manager cannot safely repair an
inconsistent active generation through ordinary `sync` or `rebuild`. Verify
the stopped writer and lock/alias ownership, then require an operator-approved
recovery using a separate owned clean generation, an explicit full rebuild,
and identity/checksum/provenance validation before any alias or consumer is
switched. If ownership cannot be verified, leave the lock/resources intact and
escalate. Do not automatically delete an orphan lock, rebuild over a damaged
generation, or resume traffic.

## Standalone retention

Retain the Standalone Solr data volume and its pre-cutover snapshot throughout
the Cloud validation, cutover, and rollback window. Preserve a verified backup
and its identity metadata before any later decommission. Never run
`docker compose down -v`, remove named volumes, or repurpose the Standalone
volume as part of this migration.
