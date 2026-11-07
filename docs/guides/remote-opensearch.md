# Remote OpenSearch pilot — isolated FetchCore projection

This is an **operator-controlled pilot**, not a claim that Aiven Free has passed
FetchCore's Projection v3 and indexing lifecycle compatibility gates. Keep the
FetchCore CLI, MCP, repository sources, canonical extraction and embedding
provider on the local/authorized host; the remote OpenSearch service receives
**rebuildable projections**, including source excerpts, paths, symbols, metadata
and vectors. Treat the remote index as sensitive repository content.

## Deployment boundary

```text
authorized workstation / self-hosted runner
  ├─ repository materialization, safe scan and canonical knowledge
  ├─ FetchCore index / search / local MCP
  └─ local embedding provider (e.g. Ollama / embeddinggemma)
             |
             | HTTPS + HTTP Basic Auth, certificate + hostname verification
             v
        remote OpenSearch
        (BM25 + k-NN vectors; Projection v3 snapshot)
```

No public FetchCore HTTP/MCP listener is required. No source repositories need
to be cloned to the remote host, **but projection data is not just opaque vectors**.
Keep the canonical repository materialization and its provenance local.

The adapter supports optional Basic Auth with
`FETCHCORE_OPENSEARCH_USERNAME` and `FETCHCORE_OPENSEARCH_PASSWORD` (or the
equivalent operator-only YAML/SDK settings). Authenticated endpoints **must**
use HTTPS. Credentials embedded in a URL are rejected. Python's default TLS
certificate/hostname verification is retained; authenticated redirects are
rejected rather than forwarding Authorization to another host. No
`verify=false` or insecure HTTP fallback exists.

## Security gate before any real repository

1. **Data admission:** start with a small synthetic, intentionally public
   repository. Review the active source policy and scan candidate files for
   secrets, credentials, personal data, internal endpoints and licensing
   restrictions **before** indexing. A file-extension exclusion alone is not a
   guarantee that source code contains no secrets; do not assume the current
   scanner is a comprehensive secret detector. Only add private corpora after
   their owner explicitly authorizes off-host indexing.
2. **Account and access:** create a separate Aiven service or equivalent
   isolated test cluster. Use dedicated credentials/ACLs scoped to the pilot
   index where the service supports them. Note that Aiven's Free plan does not
   include the OpenSearch Security plugin/RBAC; service ACL behavior and API
   permissions must be verified separately. Do not infer that index ACLs restrict
   every administrative API.
3. **Secret handling:** never commit, paste into a ticket, or log connection
   passwords or credential-bearing Service URIs. Inject credentials into the
   FetchCore process via your OS secrets mechanism or an ephemeral environment;
   do not put real credentials in repository YAML, Compose files, command-line
   flags, benchmark artifacts or terminal transcripts. Restrict process access,
   rotate credentials after any exposure.
4. **Network:** use the provider's HTTPS hostname. Restrict network access by
   provider-side IP allowlist or equivalent where supported; otherwise document
   the lack of private networking before using private corpora. Do not expose
   local Ollama, Solr, MCP or raw source over the public internet.
5. **Storage and recovery:** preserve original source and canonical inputs in
   their authorized location. Remote search indices are rebuildable, not
   backups. Capture index identity/revision/fingerprints and test an explicit
   rebuild from a known-safe corpus. Plan for free-tier suspension, version
   changes and limited retention.

## Configure from the workstation

Use the existing `fetchcore` installation and registered test repository.
Use a **service URL without user information**, such as
`https://opensearch.example.test:12345`, not the credential-bearing Service
URI copied from the cloud console.

Example operator-only configuration (no credentials in YAML):

```yaml
version: 1
runtime:
  backend: opensearch
  workspace: /absolute/path/to/authorized/synthetic-repo
  repository: example/synthetic-repo
retrieval:
  dense:
    enabled: true
    embedding:
      provider: ollama
      model: embeddinggemma
      dimensions: 768
  hybrid:
    enabled: true
backends:
  opensearch:
    url: https://opensearch.example.test:12345
    index: fetchcore-remote-pilot
embedding:
  ollama:
    url: http://127.0.0.1:11434
```

Save as an operator-owned file outside the repository, then set
`FETCHCORE_CONFIG` to its absolute path. On Bash/WSL, for a **manual** test:

```bash
export FETCHCORE_CONFIG="$HOME/.config/fetchcore/remote-pilot.yaml"
export FETCHCORE_OPENSEARCH_USERNAME="YOUR_SERVICE_USER"
read -r -s -p "OpenSearch password: " FETCHCORE_OPENSEARCH_PASSWORD
echo
export FETCHCORE_OPENSEARCH_PASSWORD
fetchcore config show --json  # password must be redacted
fetchcore doctor --json       # read-only connectivity observation
```

Credentials in environment variables can still be read by sufficiently
privileged local processes; prefer a secret manager in CI or automation.
Unset the secret when done. If a provider supplies a CA not trusted by the
host, install the verified CA into the trusted store; **do not** disable TLS
verification.

## Compatibility acceptance (must be run, not assumed)

The remote provider's OpenSearch version, allowed APIs, k-NN mapping behavior,
shard quotas and alias operations may differ from the local reference backend.
Start with `plan` and read-only `doctor`; do not modify preexisting resources.

- **Schema/provisioning:** create only an operator-owned isolated index/alias
  with the current FetchCore Projection v3 mapping. A missing or incompatible
  index is not a reason to bypass schema verification or reuse another index.
- **Lifecycle:** explicitly authorize initial `rebuild`; then run a no-change
  `sync`, a single-file changed `sync`, deletion and a repeated no-op.
  Verify the active snapshot revision, repository identity, checksum and
  projection/indexing/embedding fingerprints after each step. No silent repair.
- **Retrieval:** run the same test queries as the local baseline through
  lexical, dense and hybrid/RRF and compare Recall@K, useful evidence density,
  p50/p95, time-to-first-candidate, embedding calls, and result provenance.
  A responsive cluster alone is not proof of retrieval correctness.
- **Failures:** deliberately test wrong credentials (typed
  `authentication_denied`), absent index, incompatible mapping, provider
  outage, network loss and concurrent updates. Confirm failures are explicit,
  existing snapshots remain recoverable and no secrets appear in diagnostics.
- **Capacity:** confirm real index size and shard count against the provider's
  free limits. Test persistence/restart and document recovery behavior after
  inactivity shutdown; avoid relying on any free-tier SLA.

Run only against the intentionally isolated test resource. Do not point
operational GitHub Actions workflows at the pilot cluster, reindex a private
repository, or declare remote hybrid readiness without observing the actual
remote snapshot and embedding model.

For the lifecycle and readiness contract, see
[repository indexing](repository-indexing.md),
[operator runtime configuration](runtime-configuration.md) and
[operational readiness](runtime-configuration.md#operational-readiness-issue-134).

Provider references:
- [Aiven OpenSearch Free limitations](https://aiven.io/docs/products/opensearch/concepts/opensearch-free-tier)
- [Aiven Python connection / Service URI](https://aiven.io/docs/products/opensearch/howto/connect-with-python)
- [Aiven service users and access control](https://aiven.io/docs/products/opensearch/howto/control_access_to_content)
