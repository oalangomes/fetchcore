# Shared infrastructure consumer audit

Audit of `codex/shared-infra-p0` before consumer migration. This records the
starting state; completion evidence belongs in the PR and validation report.

| Consumer | Current resource/configuration | Gap or decision |
| --- | --- | --- |
| `scripts/shared_infra.py` | Resolves DEV and BENCH on shared services with separate repository resources, dedicated BENCH on separate ports, and OPERATIONAL on separate services and volumes. | Keep it the explicit infrastructure entry point; preserve host/WSL/container URL distinction. |
| Root `compose.yml` and `infra/compose/app.yml` | Root starts a legacy `fetchcore` Solr instance and app services resolve Docker service names. | Make the standard developer path reuse the shared DEV service and route the app to the selected resource. Retain the legacy entry point for CI/compatibility. |
| `tools/explorer/compose.yaml` | Legacy alias of root Compose. | Route Search Lab to an authorized shared target without implicit indexing. |
| CLI, API, MCP, SDK | `runtime_config` accepts explicit backend settings and repository binding; CLI indexing has separate `FETCHCORE_INDEX_*` inputs. | Verify identical explicit binding/resource routing. Do not add Docker behavior to core. |
| Search Lab API and web | `FETCHCORE_EXPLORER_TARGETS` is server allowlist; UI displays backend, repository, resource and on-demand retrieval readiness. | Add environment and effective status details to authorized targets; distinguish API health, retrieval readiness and indexing capability. |
| Baseline v1 | Frozen manifest supplies endpoints and resource settings; artifacts and ground truth are historical. | Add a separate new-run configuration path; leave frozen files byte-identical. |
| SearchIntent v2 | Runner accepts Solr URL and collection overrides. | Require explicit BENCH binding for new runs and preserve existing fixture/baseline inputs. |
| Targeted exact | Uses local/frozen baseline artifacts. | Preserve offline, hermetic behavior; no shared service required. |
| Repository learning | MCP wrapper has a historical Solr resource default. | Honor explicit BENCH resource and record run ownership/configuration. |
| Other extraction benchmarks | Local fixtures and deterministic outputs. | Preserve hermetic runs; no infrastructure migration. |
| Hosted Actions (`self-dogfood`, `explorer`, CI) | Run-owned ephemeral Solr and fixture resources. | Preserve hermetic provisioning and cleanup, never depend on developer-host ports. |
| Self-hosted operational Actions | Reusable indexing workflow uses explicit operational Solr URL/collection, `reuse_only`, sync. | Validate readiness, binding and snapshot; fail closed if unavailable. No automatic rebuild. |
| Documentation and `.env.example` | Shared-infra guide exists; getting started and examples still recommend legacy Compose/ports. | Make daily DEV/BENCH/OPERATIONAL paths and compatibility scope explicit. |

The same literal URL or resource name can be intentional in a fixture, frozen
manifest, local test, or ephemeral CI job. Those values must not be replaced by
global search-and-replace.
