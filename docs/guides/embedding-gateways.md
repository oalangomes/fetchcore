# LiteLLM and Bifrost embedding gateways (opt-in)

Both gateways implement an OpenAI-compatible **POST /v1/embeddings** endpoint.
FetchCore provides a single bounded transport adapter with separate provider
identities: **litellm** and **bifrost**. These are optional infrastructure
components. Ollama direct and lexical-only operation remain unchanged.

## Start local gateway examples

Review the files under **infra/embedding-gateways** before using them.
They intentionally expose ports only on **127.0.0.1**, and both route to
OpenAI text-embedding-3-small. This sends source text outside your machine.

~~~sh
export OPENAI_API_KEY='your-provider-key'
export LITELLM_MASTER_KEY='sk-your-long-random-master-key'
export BIFROST_ENCRYPTION_KEY='your-secret-encryption-key'
docker compose -f infra/embedding-gateways/docker-compose.yml up -d
~~~

Then verify the actual embedding endpoint:

~~~sh
curl -fsS http://127.0.0.1:4000/v1/embeddings \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"fc-openai-small-1536-v1","input":["FetchCore smoke"]}'

curl -fsS http://127.0.0.1:8080/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"model":"openai/text-embedding-3-small","input":["FetchCore smoke"]}'
~~~

Start from either **infra/embedding-gateways/fetchcore-litellm.yaml**
or **infra/embedding-gateways/fetchcore-bifrost.yaml**, and set
FETCHCORE_CONFIG to its absolute path. These Qdrant examples assume that a
Qdrant instance already exists at localhost:6333 and use **1536 dimensions**,
which is the default size of the configured upstream model.

Solr and OpenSearch currently use **frozen 768-dimensional** vector schemas.
Do not try to use 1536-dimensional vectors with those schemas. To use a
768-dimensional encoder, configure a suitable model with a separate, immutable
gateway route and matching FetchCore dimensions.

## Gateway-owned capacity, retries, and model identity

The checked-in configs intentionally bind to **one upstream embedding model**,
OpenAI `text-embedding-3-small` (default 1536 dimensions). The immutable
LiteLLM alias is `fc-openai-small-1536-v1`; Bifrost uses its
provider-qualified `openai/text-embedding-3-small` name. They may have
different gateway IDs in FetchCore; never treat their indexes as
interchangeable merely because the numeric vectors have the same dimensions.

**Simple profiles** (used by default):

- `litellm.yaml`: one deployment, `max_parallel_requests=2`,
  `rpm=30`, `tpm=30000`, one gateway retry, no cross-model fallbacks,
  and `least-busy` routing (useful when more deployments are configured).
- `bifrost.json`: provider concurrency `2`, in-memory queue buffer `8`,
  one provider retry, bounded timeout and backoff. Content logging disabled.
  The buffer is **not** a persistent job queue.

**Capacity pool profiles** (optional):

- `litellm-pool.yaml` provides **two deployments** under the exact same
  `model_name` and `litellm_params.model`, with different upstream keys.
  LiteLLM chooses a compatible deployment.
- `bifrost-pool.json` provides **two keys** restricted to the same
  `text-embedding-3-small` model. Bifrost selects from those keys.
- Different keys do **not** imply independent quotas or higher throughput.
  If both keys belong to the same provider organization, account-wide limits
  may still apply. Never use these profiles to bypass provider limits.

To exercise both pool profiles locally, choose the alternate config mounts
and provide **a second authorized upstream credential**:

~~~sh
export OPENAI_API_KEY_SECONDARY='your-second-authorized-key'
export LITELLM_CONFIG_FILE=./litellm-pool.yaml
export BIFROST_CONFIG_FILE=./bifrost-pool.json
docker compose -f infra/embedding-gateways/docker-compose.yml up -d --force-recreate
~~~

To restore the simple profiles, unset `LITELLM_CONFIG_FILE` and
`BIFROST_CONFIG_FILE` and recreate the services. The alternate compose paths
are relative to the directory holding `docker-compose.yml`.

**Overload is different per gateway:**

- LiteLLM can return HTTP **429 immediately** when all deployment slots
  are in use. Its `max_parallel_requests` counter is per proxy process.
  The FetchCore adapter has a bounded retry; the **CI job runner**, not
  FetchCore, owns retries for a failed indexing job. LiteLLM does not
  provide a durable queue in this setup.
- Bifrost accepts bursts into its **bounded in-memory provider buffer**
  until capacity is exhausted. Its queue is lost if the process stops;
  the external CI system must own durable work and cancellation.
- Two gateway services are **independent**. These configs do not build a
  meta-router across LiteLLM and Bifrost, and a failed service is never
  silently replaced with another model.
- Online queries and large indexing batches contend for the same configured
  gateway budget. Tune the batch size, index-job concurrency and upstream
  quota using measured p95/429 rates; do not treat the example numbers as
  capacity recommendations.
- The Compose examples run each gateway as one process. Multi-replica
  LiteLLM requires shared coordination (e.g. Redis) for global limits.

**Costs and access:** the basic LiteLLM no-database example cannot enforce
a reliable cumulative spending cap. Apply budgets at the upstream provider,
or deploy LiteLLM with its database-backed virtual-key governance. The
Bifrost file-only example is trusted localhost only; production requires
authentication and scoped virtual keys for allowlisting models and applying
budgets/rate limits. Both examples bind to 127.0.0.1 and do not include TLS.

**Ollama / different models:** add a separate model route and separate
projection for any embedding model that differs from the upstream above.
The gateway may route to multiple **equivalent deployments** of the same
model, but must never rewrite a vector request to a different embedding
space simply because that provider is available. Source-code sharing with
remote providers requires explicit authorization.

This behavior is documented by [LiteLLM router settings](https://docs.litellm.ai/docs/router/api_reference/configuration),
[LiteLLM concurrency behavior](https://docs.litellm.ai/docs/routing),
[Bifrost provider concurrency](https://github.com/maximhq/bifrost/blob/dev/docs/quickstart/gateway/provider-configuration.mdx),
[Bifrost client queues](https://github.com/maximhq/bifrost/blob/dev/docs/deployment-guides/config-json/client.mdx),
and [Bifrost retry policy](https://github.com/maximhq/bifrost/blob/dev/docs/features/retries-and-fallbacks.mdx).

## Runtime configuration contract

~~~yaml
version: 1
retrieval:
  dense:
    enabled: true
    embedding:
      provider: litellm  # or bifrost
      model: stable-route-v1
      dimensions: 768
embedding:
  litellm:
    base_url: http://localhost:4000/v1
    api_key_env: FETCHCORE_LITELLM_TOKEN
    allow_source_upload: true
~~~

- **allow_source_upload** defaults to false and must explicitly be true
  for gateway embedding calls; both source documents and queries can be
  transmitted to the gateway/upstream provider.
- **api_key_env** is an environment-variable name, not a secret value.
  When set, a missing value causes a typed failure before HTTP requests.
  Bifrost without gateway API authentication can omit this setting only on
  trusted local loopback deployments. Set gateway virtual-key authentication
  before any wider exposure.
- Optional overrides: FETCHCORE_LITELLM_BASE_URL,
  FETCHCORE_LITELLM_API_KEY_ENV, FETCHCORE_LITELLM_ALLOW_SOURCE_UPLOAD,
  and the equivalent FETCHCORE_BIFROST_* names. The boolean override
  accepts only lowercase true or false.
- Model IDs must identify **one immutable embedding space**. Disable
  gateway fallbacks, dynamic upstream changes and model-group routing across
  incompatible embeddings. A gateway alias does not prove underlying model
  identity; FetchCore cannot detect a changed upstream behind an unchanged
  alias.
- Changing gateway, model, dimensions, serializer or projection means new
  vectors or explicit rebuild, never silent re-use. Vector shape is checked
  after every embedding batch; non-finite values, invalid indexes and missing
  vectors fail closed. Transport retries are bounded and never change models.
- Do not log or retain source text in gateways without an explicit data
  handling policy. Example Bifrost config disables gateway request logging;
  production logging, retention and access require a separate review.

For registered repositories, use the existing FetchCore index lifecycle:
**fetchcore doctor --json**, then an explicit
**fetchcore index --config /absolute/path.yaml rebuild REPO --json** when
an embedding identity change requires reconstruction. A green doctor alone
does not prove model equivalence or current snapshot correctness.

Local evaluation uses the LiteLLM master key; use scoped virtual keys and
pinned image digests, or explicitly reviewed version tags, in production.
The Compose defaults were checked against official Bifrost v2.3.2 and LiteLLM
1.108.2 release documentation; deployment behavior still requires live validation. No live gateway, provider
account, API secret or repository content is provisioned by FetchCore.

Reference documentation:
- https://docs.litellm.ai/api-reference
- https://docs.litellm.ai/docs/proxy/docker_quick_start
- https://github.com/maximhq/bifrost
- https://github.com/maximhq/bifrost/blob/dev/docs/deployment-guides/config-json.mdx
