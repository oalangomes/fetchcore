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
pinned container image versions in production. No live gateway, provider
account, API secret or repository content is provisioned by FetchCore.

Reference documentation:
- https://docs.litellm.ai/api-reference
- https://docs.litellm.ai/docs/proxy/docker_quick_start
- https://github.com/maximhq/bifrost
- https://github.com/maximhq/bifrost/blob/dev/docs/deployment-guides/config-json.mdx
