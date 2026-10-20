# Developer Local: installation to first evidence

This is the [ADR-0005](../adr/ADR-0005-developer-local-installation.md) installation contract. P0.1 validates the complete sequence against a clean installed artifact and an isolated backend. The commands below use existing interfaces; replace the example values with a repository and Solr resource that you own. `retrieval != satisfaction != authority`.

## Prerequisites and installation

Use Python 3.11 or newer, Git, an isolated Python environment and an operator-owned, compatible Solr 9 resource. Docker is one way to run Solr, but FetchCore does not require it. Ollama is not needed for this lexical path. For a local repository, choose an allowed root and keep its checked-out revision available to CLI and MCP. A Git registration alone does not give the current MCP process a persistent workspace; use a local workspace for the first MCP journey.

The current source distribution builds a wheel and sdist. From a FetchCore source checkout, build a candidate artifact; the installation and subsequent commands can then run from another directory without importing the checkout:

```bash
python -m build
python -m venv /tmp/fetchcore-local-venv
/tmp/fetchcore-local-venv/bin/python -m pip install 'dist/fetchcore-0.1.0.dev0-py3-none-any.whl[mcp]'
/tmp/fetchcore-local-venv/bin/fetchcore --help
test -x /tmp/fetchcore-local-venv/bin/fetchcore-mcp
```

The wheel filename is an example for the current development version; use the artifact actually built. `fetchcore-mcp` is a stdio process, so its useful smoke is a client handshake, not a help page. A published package can be installed as `fetchcore[mcp]` once a release exists. The backend configset currently lives under `infra/solr/configset` in the source tree; it is **not** included in the wheel. An installed user therefore needs an operator-provisioned compatible resource. P0.1 must close or explicitly retain that packaging gap before claiming a checkout-free first use.

## Operator values and persistent data

Copy [the safe YAML example](../../examples/developer-local/runtime.yaml) outside the repository, for example to `$XDG_CONFIG_HOME/fetchcore/runtime.yaml`, and replace its absolute workspace, repository identity, Solr endpoint and collection. Keep it private to the operator. Export `FETCHCORE_CONFIG` to its absolute path and `FETCHCORE_REPOSITORY_CACHE` to an operator-owned data directory. The latter contains `registry.json` and Git mirrors; the Solr resource contains index documents and snapshots. Back up/move both deliberately. Ordinary process restarts and `docker compose stop` preserve them; volume/resource deletion does not.

```bash
export FETCHCORE_CONFIG=/absolute/path/to/operator/runtime.yaml
export FETCHCORE_REPOSITORY_CACHE=/absolute/path/to/fetchcore-data
fetchcore config show --json
fetchcore capabilities --json
fetchcore doctor --json
```

The config loader precedence is built-in defaults < operator YAML < environment < explicit caller overrides. `config show` masks secrets. Repository-owned policy cannot redirect a runtime endpoint. The index CLI also accepts `FETCHCORE_INDEX_*` compatibility inputs; avoid setting conflicting values in this path.

## Operational sequence

Provision a **dedicated** Solr service/resource with the FetchCore Projection v3 configset by an explicitly authorized operator action. The existing `python -m fetchcore.operational.solr_runtime prepare` supports `reuse_only` preflight and an explicit `provision_if_missing` mode; [self-hosted indexing](self-hosted-indexing.md) gives its exact options. Creation requires a known configset directory or a server-side configset and resource ownership. `docker compose up` may start a service, but does not register or index a repository. Never point this sequence at a shared collection or BENCH resource.

For an already provisioned local resource and a local Git workspace under `/absolute/allowed-root`, substitute the canonical identity returned by `repo add`:

```bash
fetchcore repo --allowed-root /absolute/allowed-root add /absolute/allowed-root/project --json
fetchcore index --config "$FETCHCORE_CONFIG" --allowed-root /absolute/allowed-root plan owner/project --json
fetchcore index --config "$FETCHCORE_CONFIG" --allowed-root /absolute/allowed-root rebuild owner/project --json
fetchcore index --config "$FETCHCORE_CONFIG" --allowed-root /absolute/allowed-root status owner/project --json
fetchcore readiness --config "$FETCHCORE_CONFIG" --json
fetchcore search 'bounded query' --repo owner/project --config "$FETCHCORE_CONFIG" --allowed-root /absolute/allowed-root --json
fetchcore source 'repository://owner/project/path/from/result' --repo owner/project --config "$FETCHCORE_CONFIG" --allowed-root /absolute/allowed-root --json
```

Use the **actual** `source_ref` returned by search; the source command line above only shows its shape. Record returned repository, revision, path, line span and checksum when available. `repo add` persists the registration and may materialize a Git mirror, but it does not index. `plan`, `status`, `doctor`, `readiness`, `search` and `source` do not rebuild. `rebuild` is the explicit first projection write. A missing snapshot makes registered `sync` fail with `snapshot_missing`; an incompatible one requires a separate rebuild decision. A healthy service alone is not ready retrieval.

Set the same YAML for the MCP client stdio command `fetchcore-mcp`; its `search` and `get_source` tools use an explicit single `RepositoryBinding`. The current MCP bootstrap does not consult the registry. The YAML workspace/repository/backend must therefore match the authorized CLI target; test this identity before accepting an MCP result. Starting the process never registers or indexes. Use the returned `source_ref` with `get_source`, optionally pinning the observed revision. Source reads are bounded and reject path escapes.

Restart only the FetchCore CLI/MCP process (or an owned Solr service while keeping its data volume), then repeat readiness, search and source. For manual updates, edit a controlled source, run `index ... plan` and `index ... sync` with the same options, and repeat `sync` unchanged to observe `noop` and zero document writes. A local workspace may be dirty; `status` can report `unknown` until a full plan establishes content equivalence. No automatic update scheduler is part of this profile.

## Failure and recovery

Malformed YAML, unauthorized roots, missing sources, unavailable backend, missing snapshot, changed fingerprint and revision mismatch fail explicitly. Diagnose with `config show`, `doctor`, `index ... status`, `index ... plan` and `readiness`; these operations do not repair state. On `rebuild_required`, inspect the changed schema/fingerprint and choose an explicit owned-resource rebuild or a documented migration. Do not delete a shared collection or registry lock to make a check pass.

Developer Local covers one operator and explicitly bound repositories. Remote authenticated MCP, shared tenant isolation, HA, Enterprise policy and federated multi-repository ranking are separate future gates.
