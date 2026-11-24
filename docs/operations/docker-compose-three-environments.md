# FetchCore — three canonical Docker Compose environments

**Target topology (exact names; four services each):**

| Compose file | Project name | Services | Solr host | MCP host | Search Lab |
| --- | --- | --- | --- | --- | --- |
| `compose.dev.yml` | `fetchcore-dev` | `solr, api, mcp, lab` | `127.0.0.1:18983` | `127.0.0.1:18766` | `127.0.0.1:18767` |
| `compose.yml` | `fetchcore` | `solr, api, mcp, lab` | `127.0.0.1:28984` | `127.0.0.1:38766` | `127.0.0.1:38767` |
| `compose.benchmark.yml` | `fetchcore-benchmark` | `solr, api, mcp, lab` | `127.0.0.1:48983` | `127.0.0.1:48766` | `127.0.0.1:48767` |

The three Compose files extend **the same internal service definitions** in
`infra/compose/fetchcore-stack.yml`; this fragment is *not* a fourth deployment.
All core-to-Solr traffic uses `http://solr:8983/solr` on the project's private
default network. Only the three declared host ports are published, on loopback.
The graph is a Solr collection, **not** a fourth container.

DEV and PROD reference the historical *existing* Docker volumes, respectively
`fetchcore-shared_solrcloud-data` and
`fetchcore-operational-cloud_solrcloud-data`, as **external**.
This deliberately fails if either is absent, instead of silently starting an
empty replacement. BENCH uses its own expendable
`fetchcore-benchmark_solrcloud-data`. Never mount the same productive volume
into two running Solr containers. Merely retaining a volume does **not**
prove an embedded-ZooKeeper SolrCloud will accept a renamed node:
**verify collection aliases, active revisions, checksums and cluster status
after migration before enabling consumers**.

## Standard commands — after migration

From the repository root:

```bash
# Developer stack (existing DEV volume must already exist):
docker compose -f compose.dev.yml config --quiet
docker compose -f compose.dev.yml up -d --build

# Disposable benchmark stack:
docker compose -f compose.benchmark.yml config --quiet
docker compose -f compose.benchmark.yml up -d --build

# Production: use operator-owned, verified, absolute paths, never a moving dev checkout:
export FETCHCORE_OPERATIONAL_SOURCE_PATH=/absolute/path/to/pinned/clone
export FETCHCORE_OPERATIONAL_RUNTIME_CONFIG=/absolute/path/to/verified/runtime.yml
docker compose -f compose.yml config --quiet
# Only after the approved migration/rollback rehearsal:
docker compose -f compose.yml up -d --build
```

Do not run `docker compose up` against PROD simply to explore a feature.
`api` and `mcp` load the **same** read-only runtime file. Config must bind
to the matching physical source revision, repository and existing Solr alias.
The runtime must use `http://solr:8983/solr`; if the persisted snapshot is
incompatible or dense provider is unavailable, readiness must fail rather than
silently changing retrieval policy. The frontend container is named `lab`.

## Migration is a distinct operator action

The live host was previously observed running two productive Compose projects:
`fetchcore-operational-cloud` (Solr) and `fetchcore-operational-mcp`
(API/MCP/Lab), plus `fetchcore-shared` (DEV Solr). That audit is historical,
not proof of present host state. The **operator-reviewed migration script is
posted in the PR discussion**, not executed by this repository change.
It must re-inspect containers, mounts, volume names, aliases, revision,
checksum, live consumers and backup/rollback readiness. An old productive
SolrCloud data directory may embed cluster-node identities: refuse cutover
if the cluster cannot serve its previous alias/snapshot after restart.

Never use `down -v`, `docker volume rm`, global `prune`, automatic reindex,
automatic graph rebuild, or a speculative volume copy for this migration.
Stop old serving/writer processes only within a coordinated cutover window.
Leave all old stopped containers and data intact until rollback acceptance;
retiring historical project containers is a separate, specifically authorized
step after the rollback window.

### DEV and BENCH

DEV uses a different existing data volume from PROD. It may be indexed by
explicit, independently authorized commands, never automatically on `up`.
BENCH is disposable and may be reset or seeded using an **independently
verified copy/snapshot** of PROD; no live volume sharing. Performance
benchmarks should run in the BENCH network to avoid the measured Docker
Desktop published-port penalty (~1 second per HTTP request on the audited
host). External-port latency is a separate integration metric.

No Compose stack should be started using deprecated
`infra/compose/operational-mcp.yml`, `infra/compose/shared.yml`,
`infra/compose/app.yml` or `scripts/shared_infra.py app/up`.
The one official owner of each supported environment is its root Compose file.
These historical entrypoints remain subject to follow-up legacy-lifecycle
retirement; the three-root-file contract does not make arbitrary
`docker compose -p arbitrary-name` invocations impossible.
