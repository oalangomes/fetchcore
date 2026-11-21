# Auditoria do Docker local — FetchCore

**Captura:** 2026-11-21, aproximadamente 12h49 BRT. **Escopo:** inspeção somente de leitura do Engine local, Compose, arquivos do checkout e endpoints HTTP GET. Nenhum container, volume, imagem, rede ou configuração foi alterado. O estado é uma fotografia e deve ser conferido novamente antes de qualquer intervenção.

## 1. Resumo executivo

- Engine Docker 29.8.2, 8 CPUs, 20,97 GB de RAM expostos; 27 containers visíveis em `docker ps -a` (17 ativos, 10 parados), 42 volumes, 13 redes e 47 imagens. `docker system df` contou 30 containers e 20 ativos; a diferença de três registros não foi reconciliada e não entra nas recomendações.
- DEV em `18983` **já está em SolrCloud**: `shared_infra.py ready --environment dev ... --backend solr` retornou `ready`; volume novo `fetchcore-shared_solrcloud-data`. O Standalone legado `fetchcore-shared_solr-data` (258,3 MB) permanece separado e sem consumidor. A falha Standalone descrita no pedido era histórica, não o estado observado.
- OPERATIONAL em `28984` guarda cinco gerações com 5.609–5.708 documentos e alias ativo com 5.708 documentos. A validação em `18984` guarda duas gerações com 5.585 e 5.609 documentos. Estes volumes não podem ser removidos sem decisão específica sobre retenção.
- Qdrant e OpenSearch compartilhados estão ativos por `restart: unless-stopped`, mas suas coleções/índices FetchCore observados estão vazios. A parada condicional de ambos liberaria cerca de **1,43 GiB de RAM medida**. A parada condicional do Solr de validação acrescentaria **813,4 MiB**, total aproximado de **2,23 GiB**. Medições pontuais não garantem o mesmo valor em outra carga.
- Volumes Qdrant de OPERATIONAL legado (18,35 GB) e BENCH dedicado (16,61 GB) estão ligados a containers parados. Conteúdo e backup atual não foram validados; classificar como descartáveis seria incorreto. **Recuperação de disco comprovadamente segura nesta auditoria: 0 GB.**

## 2. Inventário de projetos e containers

| Projeto | Estado Compose | Origem dos arquivos |
|---|---|---|
| fetchcore-bench-dedicated | exited(3) | CHECKOUT_FETCHCORE/infra/compose/shared.yml |
| fetchcore-lab | exited(3) | WORKTREE_COPILOT/infra/compose/app.yml,WORKTREE_COPILOT/infra/compose/shared-app.yml |
| fetchcore-operational | exited(3) | WORKTREE_COPILOT/infra/compose/shared.yml |
| fetchcore-operational-cloud | running(1) | CHECKOUT_FETCHCORE/infra/compose/solr.yml,CONFIG_FETCHCORE/operational-cloud-network.yml |
| fetchcore-operational-cloud-validation | running(1) | CHECKOUT_FETCHCORE/infra/compose/solr.yml |
| fetchcore-operational-mcp | running(3) | CHECKOUT_FETCHCORE/infra/compose/operational-mcp.yml |
| fetchcore-shared | running(3) | CHECKOUT_FETCHCORE/infra/compose/shared.yml,WORKTREE_COPILOT/infra/compose/shared.yml |
| langfuse-coders | running(6) | ALAN_TOOLS/langfuse/docker-compose.yml |
| litellm-local | running(2) | WORKTREE_AI_OPS_ALAN/litellm/docker-compose.quickstart.yml |
| ntfy-local | exited(1) | WORKTREE_AI_OPS_ALAN/ntfy/compose.yml |

Legenda de caminhos: `CHECKOUT_FETCHCORE` é este checkout; `WORKTREE_COPILOT` e `WORKTREE_AI_OPS_ALAN` são worktrees externas; `CONFIG_FETCHCORE` e `ALAN_TOOLS` são diretórios locais externos. As origens são labels dos containers, não garantia de que os arquivos ainda representem a configuração executada. `fetchcore-shared` mistura o checkout principal (Solr) e a worktree `copilot` (Qdrant/OpenSearch); `fetchcore-lab` e `fetchcore-operational` legados também apontam para ela. Os containers LiteLLM/ntfy dependem de outra worktree; Langfuse vem de `ALAN_TOOLS`.

| Container | Projeto/serviço | Imagem | Estado/saúde | RAM/CPU pontuais | Portas host | Volumes e binds (destinos) | Origem Compose | Finalidade | Classe |
|---|---|---|---|---|---|---|---|---|---|
| dagger-engine-v0.21.11 | sem Compose/- | registry.dagger.io/engine:v0.21.11 | running/sem healthcheck | 104.1MiB / 19.53GiB / 0.00% | - | bdddb85c15115f126e499b61bcdfa7c71fdab6118719b6bf9cefa85d667cb650:/var/lib/dagger | - | engine Dagger | INVESTIGATE |
| fetchcore-bench-dedicated-opensearch-1 | fetchcore-bench-dedicated/opensearch | opensearchproject/opensearch:3.8.0 | exited/unhealthy | 0B / 0B / 0.00% | - | fetchcore-bench-dedicated_opensearch-data:/usr/share/opensearch/data | CHECKOUT_FETCHCORE/infra/compose/shared.yml | projeção OpenSearch | REMOVE CANDIDATE |
| fetchcore-bench-dedicated-qdrant-1 | fetchcore-bench-dedicated/qdrant | qdrant/qdrant:v1.19.0 | exited/sem healthcheck | 0B / 0B / 0.00% | - | fetchcore-bench-dedicated_qdrant-data:/qdrant/storage | CHECKOUT_FETCHCORE/infra/compose/shared.yml | projeção vetorial | REMOVE CANDIDATE |
| fetchcore-bench-dedicated-solr-1 | fetchcore-bench-dedicated/solr | solr:9.10.1@sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb | exited/unhealthy | 0B / 0B / 0.00% | - | bind:/fetchcore-configset, bind:/fetchcore-start-shared.sh, fetchcore-bench-dedicated_solr-data:/var/solr, bind:/opt/solr/server/solr/configsets/fetchcore | CHECKOUT_FETCHCORE/infra/compose/shared.yml | projeção Solr | REMOVE CANDIDATE |
| fetchcore-lab-api-1 | fetchcore-lab/api | fetchcore-lab-api | exited/unhealthy | 0B / 0B / 0.00% | - | bind:/bench-workspace, bind:/workspace | WORKTREE_COPILOT/infra/compose/app.yml,WORKTREE_COPILOT/infra/compose/shared-app.yml | API/MCP/Search Lab | REMOVE CANDIDATE |
| fetchcore-lab-mcp-1 | fetchcore-lab/mcp | fetchcore-lab-mcp | exited/unhealthy | 0B / 0B / 0.00% | - | bind:/bench-workspace, bind:/workspace | WORKTREE_COPILOT/infra/compose/app.yml,WORKTREE_COPILOT/infra/compose/shared-app.yml | API/MCP/Search Lab | REMOVE CANDIDATE |
| fetchcore-lab-web-1 | fetchcore-lab/web | fetchcore-lab-web | exited/unhealthy | 0B / 0B / 0.00% | - | - | WORKTREE_COPILOT/infra/compose/app.yml,WORKTREE_COPILOT/infra/compose/shared-app.yml | API/MCP/Search Lab | REMOVE CANDIDATE |
| fetchcore-operational-cloud-solr-1 | fetchcore-operational-cloud/solr | solr:9.10.1@sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb | running/healthy | 771.1MiB / 19.53GiB / 0.39% | 127.0.0.1:28984→8983/tcp | bind:/fetchcore-configset, bind:/fetchcore-start-cloud.sh, fetchcore-operational-cloud_solrcloud-data:/var/solr | CHECKOUT_FETCHCORE/infra/compose/solr.yml,CONFIG_FETCHCORE/operational-cloud-network.yml | projeção Solr | KEEP |
| fetchcore-operational-cloud-validation-solr-1 | fetchcore-operational-cloud-validation/solr | solr:9.10.1@sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb | running/healthy | 813.4MiB / 19.53GiB / 0.42% | 127.0.0.1:18984→8983/tcp | bind:/fetchcore-configset, bind:/fetchcore-start-cloud.sh, fetchcore-operational-cloud-validation_solrcloud-data:/var/solr | CHECKOUT_FETCHCORE/infra/compose/solr.yml | projeção Solr | STOP |
| fetchcore-operational-mcp-api-1 | fetchcore-operational-mcp/api | fetchcore-operational-lab:local | running/healthy | 70.05MiB / 19.53GiB / 21.45% | - | bind:/workspace, bind:/etc/fetchcore/operational-mcp.yml | CHECKOUT_FETCHCORE/infra/compose/operational-mcp.yml | API/MCP/Search Lab | KEEP |
| fetchcore-operational-mcp-lab-1 | fetchcore-operational-mcp/lab | fetchcore-operational-lab-web:local | running/healthy | 11.7MiB / 19.53GiB / 0.00% | 127.0.0.1:38767→8080/tcp | - | CHECKOUT_FETCHCORE/infra/compose/operational-mcp.yml | API/MCP/Search Lab | KEEP |
| fetchcore-operational-mcp-mcp-1 | fetchcore-operational-mcp/mcp | fetchcore-operational-mcp:local | running/sem healthcheck | 69.88MiB / 19.53GiB / 0.11% | 127.0.0.1:38766→8766/tcp | bind:/workspace, bind:/etc/fetchcore/operational-mcp.yml | CHECKOUT_FETCHCORE/infra/compose/operational-mcp.yml | API/MCP/Search Lab | KEEP |
| fetchcore-operational-opensearch-1 | fetchcore-operational/opensearch | opensearchproject/opensearch:3.8.0 | exited/unhealthy | 0B / 0B / 0.00% | - | fetchcore-operational_opensearch-data:/usr/share/opensearch/data | WORKTREE_COPILOT/infra/compose/shared.yml | projeção OpenSearch | INVESTIGATE |
| fetchcore-operational-qdrant-1 | fetchcore-operational/qdrant | qdrant/qdrant:v1.19.0 | exited/sem healthcheck | 0B / 0B / 0.00% | - | fetchcore-operational_qdrant-data:/qdrant/storage | WORKTREE_COPILOT/infra/compose/shared.yml | projeção vetorial | INVESTIGATE |
| fetchcore-operational-solr-1 | fetchcore-operational/solr | solr:9.10.1@sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb | exited/unhealthy | 0B / 0B / 0.00% | - | bind:/fetchcore-configset, bind:/fetchcore-start-shared.sh, fetchcore-operational_solr-data:/var/solr, bind:/opt/solr/server/solr/configsets/fetchcore | WORKTREE_COPILOT/infra/compose/shared.yml | projeção Solr | INVESTIGATE |
| fetchcore-shared-opensearch-1 | fetchcore-shared/opensearch | opensearchproject/opensearch:3.8.0 | running/healthy | 1.338GiB / 19.53GiB / 3.43% | 127.0.0.1:19200→9200/tcp, 127.0.0.1:19600→9600/tcp | fetchcore-shared_opensearch-data:/usr/share/opensearch/data | WORKTREE_COPILOT/infra/compose/shared.yml | projeção OpenSearch | STOP |
| fetchcore-shared-qdrant-1 | fetchcore-shared/qdrant | qdrant/qdrant:v1.19.0 | running/sem healthcheck | 94.07MiB / 19.53GiB / 0.13% | 127.0.0.1:16333→6333/tcp, 127.0.0.1:16334→6334/tcp | fetchcore-shared_qdrant-data:/qdrant/storage | WORKTREE_COPILOT/infra/compose/shared.yml | projeção vetorial | STOP |
| fetchcore-shared-solr-1 | fetchcore-shared/solr | solr:9.10.1@sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb | running/healthy | 742.6MiB / 19.53GiB / 1.07% | 127.0.0.1:18983→8983/tcp | bind:/fetchcore-configset, bind:/fetchcore-start-cloud.sh, bind:/fetchcore-start-shared.sh, fetchcore-shared_solrcloud-data:/var/solr | CHECKOUT_FETCHCORE/infra/compose/shared.yml | projeção Solr | KEEP |
| langfuse-coders-clickhouse-1 | langfuse-coders/clickhouse | docker.io/clickhouse/clickhouse-server:26.3.62.3 | running/healthy | 761MiB / 19.53GiB / 14.14% | - | langfuse-coders_clickhouse_data:/var/lib/clickhouse, langfuse-coders_clickhouse_logs:/var/log/clickhouse-server | ALAN_TOOLS/langfuse/docker-compose.yml | observabilidade Langfuse | KEEP |
| langfuse-coders-langfuse-web-1 | langfuse-coders/langfuse-web | docker.langfuse.com/langfuse/langfuse:4.83.0 | running/sem healthcheck | 788.5MiB / 19.53GiB / 0.08% | 127.0.0.1:3040→3000/tcp | - | ALAN_TOOLS/langfuse/docker-compose.yml | observabilidade Langfuse | KEEP |
| langfuse-coders-langfuse-worker-1 | langfuse-coders/langfuse-worker | docker.langfuse.com/langfuse/langfuse-worker:4.83.0 | running/sem healthcheck | 582.3MiB / 19.53GiB / 0.87% | - | - | ALAN_TOOLS/langfuse/docker-compose.yml | observabilidade Langfuse | KEEP |
| langfuse-coders-minio-1 | langfuse-coders/minio | cgr.dev/chainguard/minio@sha256:e9cf6b99c226e9539ed0f14b6f00e37b07da153c7ba1f4f8709c807574b471ec | running/healthy | 108.8MiB / 19.53GiB / 4.98% | 127.0.0.1:19090→9000/tcp | langfuse-coders_minio_data:/data | ALAN_TOOLS/langfuse/docker-compose.yml | observabilidade Langfuse | KEEP |
| langfuse-coders-postgres-1 | langfuse-coders/postgres | docker.io/postgres:17.7 | running/healthy | 53.12MiB / 19.53GiB / 0.00% | - | langfuse-coders_postgres_data:/var/lib/postgresql/data | ALAN_TOOLS/langfuse/docker-compose.yml | observabilidade Langfuse | KEEP |
| langfuse-coders-redis-1 | langfuse-coders/redis | docker.io/redis:7.4.7 | running/healthy | 14.08MiB / 19.53GiB / 0.50% | - | langfuse-coders_redis_data:/data | ALAN_TOOLS/langfuse/docker-compose.yml | observabilidade Langfuse | KEEP |
| litellm-local-db-1 | litellm-local/db | postgres:16 | running/healthy | 66.11MiB / 19.53GiB / 0.00% | - | litellm-local_postgres_data:/var/lib/postgresql/data | WORKTREE_AI_OPS_ALAN/litellm/docker-compose.quickstart.yml | gateway LiteLLM e banco | KEEP |
| litellm-local-litellm-1 | litellm-local/litellm | docker.litellm.ai/berriai/litellm:main-stable | running/healthy | 738.4MiB / 19.53GiB / 0.12% | 127.0.0.1:4000→4000/tcp | bind:/app/config.yaml | WORKTREE_AI_OPS_ALAN/litellm/docker-compose.quickstart.yml | gateway LiteLLM e banco | KEEP |
| ntfy-local-ntfy-1 | ntfy-local/ntfy | binwiederhier/ntfy:v2.28.0 | exited/unhealthy | 0B / 0B / 0.00% | - | bind:/var/lib/ntfy | WORKTREE_AI_OPS_ALAN/ntfy/compose.yml | notificações ntfy | INVESTIGATE |

**Políticas:** todos os containers Compose listados usam `unless-stopped`; Dagger usa `always`. `docker inspect` mostrou `Memory=0` e `NanoCpus=0` para todos, ou seja, nenhum limite Docker explícito. Os healthchecks observados incluem SolrCloud, OpenSearch, API e Lab, bancos Langfuse/LiteLLM; Qdrant e MCP não têm healthcheck. Dependências Compose: `operational-mcp` Lab depende de API saudável; Langfuse depende de seus bancos/serviços conforme o Compose externo. Relações de rede não são dependências de execução comprovadas.

## 3. Volumes, consumidores e persistência

Tamanho conforme `docker system df -v` (medição lógica do Engine; não equivale necessariamente a espaço físico recuperado do VHDX do Docker Desktop). `0` links indica ausência de container associado, não ausência de dados. Dois volumes anônimos mostram um link no `docker system df -v`, mas seu consumidor não aparece em `docker inspect` dos 27 containers visíveis; não foram classificados como órfãos. Labels `io.fetchcore.owner` e `com.docker.compose.project` servem para ownership; volumes anônimos e de terceiros exigem identificação independente.

| Volume | Tamanho | Projeto/owner das labels | Consumidores | Classe |
|---|---:|---|---|---|
| 10eb732694e5fe12625ea886bb20e0db761604ae8f1507376330014ac5da9e54 | 29.25kB | -/- | nenhum | INVESTIGATE |
| 2dc35f0dba477ab9ad975b3142c667ba097de137b023a19de950f20927889296 | 67.13MB | -/- | nenhum | INVESTIGATE |
| 4238c2f68dec4bad236990a242abd31e431a813b4f33137fbffaaf21a9c13191 | 67.13MB | -/- | nenhum | INVESTIGATE |
| 483067ab1d189710fec68a8e5b7dff9f9a38e4499f4515d80ce61df95a880b40 | 67.13MB | -/- | nenhum | INVESTIGATE |
| 5278330bea0fd54b022810c46e4e9494e13f762043f3d4cfe90bac0bbd374bc7 | 1.547GB | -/- | consumidor não identificado (1 link no df) | INVESTIGATE |
| 69e62d49aa02fc3d41d91ae432e2e3cf99efd975a206be1914c97d9c76c55b65 | 67.13MB | -/- | nenhum | INVESTIGATE |
| ai-memory-data | 0B | -/- | nenhum | INVESTIGATE |
| bdddb85c15115f126e499b61bcdfa7c71fdab6118719b6bf9cefa85d667cb650 | 172.8kB | -/- | dagger-engine-v0.21.11 (ativo) | INVESTIGATE |
| c652015cf2c449dc0c3ad57d79cbaf9b25d132e25f1d3c1adfbb3402fd4956fb | 67.13MB | -/- | nenhum | INVESTIGATE |
| cbc4571f73412efc3bdd20e786eb70819bc8283f2089faab229e4cd53735efe3 | 67.15MB | -/- | nenhum | INVESTIGATE |
| db39a9fb5a049329c26fcd5cf39e83802708a164eba8b6211a67d9f453881300 | 0B | -/- | consumidor não identificado (1 link no df) | INVESTIGATE |
| eebff55c261bf3763bc081e5ffbb8d411d71c88f0456431129250123716e926f | 67.14MB | -/- | nenhum | INVESTIGATE |
| fetchcore-bench-a20261021a01_opensearch-data | 156.4kB | fetchcore-bench-a20261021a01/fetchcore-shared-infra-v1 | nenhum | INVESTIGATE |
| fetchcore-bench-a20261021a01_qdrant-data | 357B | fetchcore-bench-a20261021a01/fetchcore-shared-infra-v1 | nenhum | INVESTIGATE |
| fetchcore-bench-a20261021a01_solr-data | 79.35MB | fetchcore-bench-a20261021a01/fetchcore-shared-infra-v1 | nenhum | INVESTIGATE |
| fetchcore-bench-b20261021b01_opensearch-data | 156.9kB | fetchcore-bench-b20261021b01/fetchcore-shared-infra-v1 | nenhum | INVESTIGATE |
| fetchcore-bench-b20261021b01_qdrant-data | 357B | fetchcore-bench-b20261021b01/fetchcore-shared-infra-v1 | nenhum | INVESTIGATE |
| fetchcore-bench-b20261021b01_solr-data | 71.35MB | fetchcore-bench-b20261021b01/fetchcore-shared-infra-v1 | nenhum | INVESTIGATE |
| fetchcore-bench-dedicated_opensearch-data | 140.8MB | fetchcore-bench-dedicated/fetchcore-shared-infra-v1 | fetchcore-bench-dedicated-opensearch-1 (parado) | INVESTIGATE |
| fetchcore-bench-dedicated_qdrant-data | 16.61GB | fetchcore-bench-dedicated/fetchcore-shared-infra-v1 | fetchcore-bench-dedicated-qdrant-1 (parado) | INVESTIGATE |
| fetchcore-bench-dedicated_solr-data | 294.7MB | fetchcore-bench-dedicated/fetchcore-shared-infra-v1 | fetchcore-bench-dedicated-solr-1 (parado) | INVESTIGATE |
| fetchcore-operational-cloud-validation_solrcloud-data | 959.3MB | fetchcore-operational-cloud-validation/- | fetchcore-operational-cloud-validation-solr-1 (ativo) | INVESTIGATE |
| fetchcore-operational-cloud_solrcloud-data | 1.354GB | fetchcore-operational-cloud/- | fetchcore-operational-cloud-solr-1 (ativo) | KEEP |
| fetchcore-operational_opensearch-data | 773MB | fetchcore-operational/fetchcore-shared-infra-v1 | fetchcore-operational-opensearch-1 (parado) | INVESTIGATE |
| fetchcore-operational_qdrant-data | 18.35GB | fetchcore-operational/fetchcore-shared-infra-v1 | fetchcore-operational-qdrant-1 (parado) | INVESTIGATE |
| fetchcore-operational_solr-data | 171.3MB | fetchcore-operational/fetchcore-shared-infra-v1 | fetchcore-operational-solr-1 (parado) | INVESTIGATE |
| fetchcore-p02-codex_opensearch-data | 605kB | fetchcore-p02-codex/codex-p02-20261019 | nenhum | INVESTIGATE |
| fetchcore-p02-codex_qdrant-data | 872.9MB | fetchcore-p02-codex/codex-p02-20261019 | nenhum | INVESTIGATE |
| fetchcore-p02-codex_solr-data | 482.2kB | fetchcore-p02-codex/codex-p02-20261019 | nenhum | INVESTIGATE |
| fetchcore-shared_opensearch-data | 288.5kB | fetchcore-shared/fetchcore-shared-infra-v1 | fetchcore-shared-opensearch-1 (ativo) | INVESTIGATE |
| fetchcore-shared_qdrant-data | 705.2MB | fetchcore-shared/fetchcore-shared-infra-v1 | fetchcore-shared-qdrant-1 (ativo) | INVESTIGATE |
| fetchcore-shared_solr-data | 258.3MB | fetchcore-shared/fetchcore-shared-infra-v1 | nenhum | MIGRATE |
| fetchcore-shared_solrcloud-data | 67.18MB | fetchcore-shared/fetchcore-shared-infra-v1 | fetchcore-shared-solr-1 (ativo) | KEEP |
| langfuse-coders_clickhouse_data | 263.8MB | langfuse-coders/- | langfuse-coders-clickhouse-1 (ativo) | KEEP |
| langfuse-coders_clickhouse_logs | 21.56MB | langfuse-coders/- | langfuse-coders-clickhouse-1 (ativo) | KEEP |
| langfuse-coders_minio_data | 589kB | langfuse-coders/- | langfuse-coders-minio-1 (ativo) | KEEP |
| langfuse-coders_postgres_data | 65.64MB | langfuse-coders/- | langfuse-coders-postgres-1 (ativo) | KEEP |
| langfuse-coders_redis_data | 39.78kB | langfuse-coders/- | langfuse-coders-redis-1 (ativo) | KEEP |
| langfuse-local_langfuse_minio_data | 70.83kB | langfuse-local/- | nenhum | INVESTIGATE |
| langfuse-local_langfuse_postgres_data | 64.83MB | langfuse-local/- | nenhum | INVESTIGATE |
| langfuse-local_langfuse_redis_data | 267.7kB | langfuse-local/- | nenhum | INVESTIGATE |
| litellm-local_postgres_data | 72.17MB | litellm-local/- | litellm-local-db-1 (ativo) | KEEP |

Conteúdo comprovado via APIs GET: `fetchcore-shared` Qdrant tem duas coleções `fc-dev-*`/`fc-bench-*`, ambas com 0 pontos; OpenSearch tem dois índices FetchCore com 0 documentos, além de índices internos `.plugins-ml-config` e `top_queries-*` (não equivalem a dados FetchCore). DEV SolrCloud não tem coleções. OPERATIONAL Cloud tem alias `fc-operational-fetchcore-635ab7352c2850f2-cloud` para geração com 5.708 documentos; validação tem dois aliases e gerações com dados. Conteúdo dos volumes ligados apenas a containers parados, dos volumes sem links e dos anônimos **não foi lido nem descartabilidade comprovada**.

## 4. RAM, CPU, disco e imagens

| Categoria `docker system df` | Total | Ativo | Tamanho | Reclamável informado |
|---|---:|---:|---:|---:|
| Imagens | 47 | 22 | 26,87 GB | 11,91 GB |
| Containers | 30 | 20 | 113,7 MB | 24,24 MB |
| Volumes | 42 | 20 | 43,26 GB | 1,818 GB |
| Build cache | 104 entradas | 0 | 2,565 GB | 1,425 GB |

O total de RAM dos containers é uma amostra `docker stats --no-stream --all`; CPU é percentual instantâneo, não média. A maior carga opcional observada foi OpenSearch compartilhado 1,338 GiB/3,43%, Qdrant compartilhado 94,07 MiB/0,13%, Solr de validação 813,4 MiB/0,42%. Langfuse web/worker/ClickHouse consumiam aproximadamente 788/582/761 MiB e LiteLLM aproximadamente 740 MiB. Esses serviços pertencem a outros projetos e não entram na economia FetchCore.

### Imagens instaladas

| Imagem:tag | Digest local | Tamanho exibido | Containers associados |
|---|---|---:|---:|
| docker.langfuse.com/langfuse/langfuse:4.83.0 | sha256:a91445265d1f5a13d666ef2f1491a437255fb9e5b7a76525d5543e4a4dc030f3 | 1.06GB | 1 |
| docker.langfuse.com/langfuse/langfuse-worker:4.83.0 | sha256:a6eac4bb99df27dd01a090e63d1a54660ba14044ae5e4c1ef1c883cec5d68ee4 | 821MB | 1 |
| cgr.dev/chainguard/minio:<none> | sha256:e9cf6b99c226e9539ed0f14b6f00e37b07da153c7ba1f4f8709c807574b471ec | 236MB | 1 |
| fetchcore-operational-lab:local | sha256:1f818041682c5dace10fdbbbc1d5d84e015cf294b19ef987a1f7ebdc95b71047 | 648MB | 1 |
| fetchcore-operational-mcp:local | sha256:7b296ac7a0f0942995d93183cd95b7bf127b973adb9b8ddaeaed8bd5a49c2441 | 648MB | 1 |
| docker.langfuse.com/langfuse/langfuse-worker:4 | sha256:27e8b49d619a013bca38ed9b0853456e19f5667a23e8e26e3a10d4e28485f49c | 803MB | 0 |
| docker.langfuse.com/langfuse/langfuse:4 | sha256:59abb54faacf45b63d09f4076d45683dae26a6661974d497836543d350b16575 | 1.03GB | 0 |
| docker.langfuse.com/langfuse/ai-gateway:4 | sha256:a011845b1a503cd29145ad0f27d19359ca1e0615cf65d2029407dffbfb3ccbd6 | 95.4MB | 0 |
| cgr.dev/chainguard/minio:latest | sha256:aea6c163749e1caca37f907976dcf337177ddf5f558808f2aa591618fd3a6eef | 236MB | 0 |
| alpine:3.22 | sha256:49340b346798875b5592e5d420241f2f9cfba5f47e62ebb1446a1b34734e898f | 12.9MB | 0 |
| redis:7 | sha256:2e3eb11462c4e3759e6972678dd051d150ff54b1e60714b3c33b701260172a90 | 170MB | 0 |
| postgres:17 | sha256:748a18a54d3842d43bc156f0840d78029b8d349e0feca98c12b9c4ce3404a6b3 | 646MB | 0 |
| postgres:16 | sha256:933463a6c8eba2b9f5cd414138738cd8d5519cd2c471214c222652f547a66a32 | 642MB | 1 |
| docker.litellm.ai/berriai/litellm:main-stable | sha256:c560d317136880ea92d62334d4367ab10955b1af189dd3ea24f6551290b1d6c1 | 1.81GB | 1 |
| akitaonrails/ai-memory:latest | sha256:db5103a11dbdd4b668d05ebc90c13832bc3881f719d5113ad8a0a86447a6b74e | 205MB | 0 |
| clickhouse/clickhouse-server:26.3.62.3 | sha256:de3e47c64078aa196dae2349e048fc98ba81947f651f5c20e06e46baea8ae40a | 1.19GB | 1 |
| fetchcore:v0.1.0.dev0 | sha256:d5620544bd368f4327c701ef41b7d94080d75c4e94d576d72f8029bd7de97c09 | 259MB | 0 |
| fetchcore:issue-132 | sha256:b4bcd6db680a648ced0bdef703b302a29673d5e4e1ac37f0df20b1e4ae4ef638 | 259MB | 0 |
| registry.dagger.io/engine:v0.21.11 | sha256:6b557cc8b5fdfee0e2e8c82d451ac922097a1255482143e1b96c0be3ff2a303c | 1.05GB | 1 |
| fetchcore-mcp-eval:05ba57a | sha256:dbaddfcefce7b64465f5ff02da32c1c51f2a802756336647f8f0915c13973bfb | 423MB | 0 |
| fetchcore-operational-lab-web:local | sha256:cd004a9e25e52eb37dce747c086634782b854efbd18d1002870ee0b56ad3e6f0 | 74.7MB | 1 |
| solr:9.10.1 | sha256:81465ca1a7089fe2cb77f0e211f9b83c808ae283567b7543d986ce6bcaa7e282 | 1.21GB | 0 |
| fetchcore-lab-api:latest | sha256:0832d57c302fc72cc428d4dd2d076e67329720c4028cd7686a360f1a1cb7393b | 421MB | 1 |
| fetchcore-lab-mcp:latest | sha256:234a6b99e0cb6668b87ac0b65b1c3a8e5faa08f3cb916d9e1bfe5def44bf575d | 421MB | 1 |
| fetchcore-lab-web:latest | sha256:04c4558640f50a89611c6747c91faae010cd93444ce22b28d6411d758db93b8b | 74.7MB | 1 |
| fetchcore-lab-bench-mcp:latest | sha256:9fe3f629e51d49fc6eb0192e4c53dfb94eaaf5be4c28a630c5d3ba1d70a75630 | 421MB | 0 |
| fetchcore-lab-dev-mcp:latest | sha256:d0adff60f0a24db637da5a4274d763be6c3de800b407c380ded2c466a7eabd25 | 421MB | 0 |
| fetchcore-lab-dev-api:latest | sha256:a5dde2e8f793db507711115a4d78c52b7d997273f04df2ec11cd28cf45a361e8 | 421MB | 0 |
| fetchcore-lab-operational-mcp:latest | sha256:4495e852622933147e7417857c3a89388163a75f19ec049583c3d4c81440a9aa | 421MB | 0 |
| fetchcore-lab-bench-api:latest | sha256:2168d928e6dacc84f046b8c907dc2fc829cd2a2146a7ae2187027b092d071507 | 421MB | 0 |
| fetchcore-lab-operational-api:latest | sha256:2177894bf0a03e24858852f83d49318f9362b81f40efdd0e061114ed0a340953 | 421MB | 0 |
| fetchcore-lab-bench-web:latest | sha256:30513232e2472a8ae945dd72db0a1b76f29b1107eb0470527ebc78842f0d9e0c | 74.7MB | 0 |
| fetchcore-lab-operational-web:latest | sha256:e006b9d21bb2e2bbc51de225fc4e7a5b2002de516a6e2a4d294e011c159b2114 | 74.7MB | 0 |
| fetchcore-lab-dev-web:latest | sha256:52b5e01c9b875da02358311dc6693eed8d28c9dec694006270b1589cb42c9c73 | 74.7MB | 0 |
| solr:<none> | sha256:5ac29f32d689cef9722ec3b9f2c612ed11dce35d9e279ac0f7bd554ad76aa8cb | 1.21GB | 5 |
| akitaonrails/ai-memory:2.4.2 | sha256:21a747084ccffc7f49ecfccccc8dc69c6a2e9e740be0bcaa7498b3e130417337 | 184MB | 0 |
| opensearchproject/opensearch:3.8.0 | sha256:fafe3fc3587088674669235575aa166228c48bdb940294a8cdbbc1da75236a40 | 2.83GB | 3 |
| binwiederhier/ntfy:v2.28.0 | sha256:6ef4b819f722fccdc036af611c4774cfdc2de821ab74fdd48bbf4c9d6f8973da | 120MB | 1 |
| registry.dagger.io/engine:v0.21.9 | sha256:3c4e0cedaea400d7e30155e050f94910ea5f381b6f5942dd2172382657628cb4 | 1.03GB | 0 |
| docker/desktop-cloud-provider-kind:v0.7.0 | sha256:f078b9ccf4780425ada337bfa7b5e364106ceccdb90006c87f357daa420d3da3 | 628MB | 1 |
| qdrant/qdrant:v1.19.0 | sha256:057ee3a8da769fe7310dd3537b4dc7583bf87a95ce8ac43c0af5a46bc580d1fc | 275MB | 3 |
| docker/desktop-containerd-registry-mirror:v0.0.4 | sha256:1de142695bb068e49b4c8abb7f1eab5b215f23ba93f69b2ed44a5d75b55d5cc1 | 52.9MB | 1 |
| envoyproxy/envoy:v1.36.7 | sha256:29d778ba078e0404d18fa45ffb834c062071b0f81dc0a6a41501ce8e23a0059f | 253MB | 0 |
| kindest/node:v1.36.1 | sha256:3489c7674813ba5d8b1a9977baea8a6e553784dab7b84759d1014dbd78f7ebd5 | 1.31GB | 1 |
| redis:7.4.7 | sha256:976ab0f0939b78c06d802bcd3bea44c5142ea9a756a57b861e08819817f768c8 | 175MB | 1 |
| postgres:17.7 | sha256:2006493727bd5277eece187319af1ef82b4cf82cf4fc1ed00da0775b646ac2a4 | 645MB | 1 |
| ollama/ollama:0.13.5 | sha256:2c9595c555fd70a28363489ac03bd5bf9e7c5bdf2890373c3a830ffd7252ce6d | 6.14GB | 0 |

Tamanhos de imagens compartilham camadas; somar linhas superestima economia. `solr:9.10.1` (digest 81465…) não é o digest pinado 5ac29… usado por cinco containers. A imagem sem tag 5ac29… **não é órfã**. Imagem Ollama 6,14 GB sem containers exige confirmação do proprietário antes de remoção. As 11,91 GB marcadas reclamáveis pelo Engine não foram tratadas como seguro para exclusão.

## 5. Redes, isolamento e portas

| Rede | Driver | Projeto label | Containers ativos conectados |
|---|---|---|---|
| bridge | bridge | - | dagger-engine-v0.21.11 |
| fetchcore-bench-dedicated_default | bridge | fetchcore-bench-dedicated | - |
| fetchcore-lab_default | bridge | fetchcore-lab | - |
| fetchcore-operational-cloud-validation_default | bridge | fetchcore-operational-cloud-validation | fetchcore-operational-cloud-validation-solr-1 |
| fetchcore-operational-cloud_default | bridge | fetchcore-operational-cloud | fetchcore-operational-cloud-solr-1 |
| fetchcore-operational_default | bridge | fetchcore-operational | fetchcore-operational-cloud-solr-1, fetchcore-operational-mcp-api-1, fetchcore-operational-mcp-lab-1, fetchcore-operational-mcp-mcp-1 |
| fetchcore-shared_default | bridge | fetchcore-shared | fetchcore-shared-qdrant-1, fetchcore-shared-opensearch-1, fetchcore-shared-solr-1 |
| host | host | - | - |
| kind | bridge | - | desktop-control-plane, kind-cloud-provider, kind-registry-mirror |
| langfuse-coders_default | bridge | langfuse-coders | langfuse-coders-langfuse-worker-1, langfuse-coders-langfuse-web-1, langfuse-coders-postgres-1, langfuse-coders-clickhouse-1, langfuse-coders-minio-1, langfuse-coders-redis-1 |
| litellm-local_default | bridge | litellm-local | litellm-local-litellm-1, litellm-local-db-1 |
| none | null | - | - |
| ntfy-local_default | bridge | ntfy-local | - |

- `18983`: DEV compartilhado SolrCloud; `18984`: OPERATIONAL Cloud **validation**; `28984`: OPERATIONAL Cloud com alias ativo; `38766`: MCP HTTP; `38767`: Search Lab web. Todos publicados em `127.0.0.1`, sem conflito entre essas portas. `19200/19600` são OpenSearch compartilhado; `16333/16334` são Qdrant compartilhado.
- A rede `fetchcore-operational_default` conecta Solr OPERATIONAL Cloud à API/MCP/Lab; o container Solr também participa da rede própria `fetchcore-operational-cloud_default`. `fetchcore-operational-cloud-validation_default` é isolada. `fetchcore-shared_default` conecta somente os três backends compartilhados ativos. Não inferir acesso cross-project apenas pelo nome da rede.

## 6. Problemas comprovados, dependências e riscos

1. **Configuração de origem frágil:** `fetchcore-shared` possui arquivos Compose de duas árvores; `fetchcore-lab` e os containers OPERATIONAL antigos têm binds para a worktree `copilot`. A remoção dessa worktree pode quebrar recriações. O problema é de proveniência/configuração, não evidência de falha atual do Solr.
2. **Divergência DEV histórica:** o relato inicial de Standalone e `--no-recreate` é consistente com o volume legado `fetchcore-shared_solr-data`; porém o container atual usa `solrcloud-data`, responde `solrcloud` e passou `ready`. `up` usa `--no-recreate`; portanto, se uma configuração futura mudar, `up` sozinho não aplica alterações ao container. Não conectar o volume Standalone ao Cloud nem recriar serviço persistente sem plano e backup.
3. **Dois OPERATIONALs:** o Cloud atual e o Standalone legado parado coexistem. O MCP/Lab lê `CONFIG_FETCHCORE/operational-cloud.yml`, com backend Solr, modo `solrcloud`, alias Cloud e URL de rede `http://solrcloud-fetchcore:8983/solr`; o `operational-mcp.runtime.yml` versionado contém URL/alias diferentes como valor de exemplo. `shared_infra.py ready --environment operational` roteia para o legado Standalone e não concluiu durante esta auditoria; foi interrompido sem alterações. Não usar esse comando como prova da saúde do Cloud.
4. **Reinício inesperado:** backends opcionais compartilhados e Solr de validação estão com `unless-stopped`; após reinício do Docker Desktop podem voltar a consumir RAM. Mudar essa política requer alteração revisada do Compose e aplicação controlada, não editar o container ad hoc.
5. **Índices persistidos sem backup verificado:** volumes Qdrant de 18,35/16,61 GB; OpenSearch OPERATIONAL 773 MB; Solr legado, BENCH e validação. A amostra read-only não comprova conteúdo completo, cópia restaurável ou que esses índices possam ser reconstruídos do mesmo revision/fingerprint/embedding. Risco alto para exclusão.
6. **Terceiros:** Langfuse, LiteLLM/PostgreSQL, Dagger, ntfy e kind/recursos Docker Desktop são de outros projetos. ntfy está parado; sua necessidade atual e o bind de dados externo não foram validados. Não incluí-los na limpeza FetchCore.

## 7. Recursos necessários, opcionais e candidatos

- **KEEP:** DEV SolrCloud; OPERATIONAL SolrCloud e volume de 1,354 GB; API/MCP/Search Lab operacional; volumes de dados Langfuse/LiteLLM e seus serviços ativos. Preservar o alias e as cinco gerações OPERATIONAL até política de retenção definida.
- **STOP, condicionado a confirmar consumidores:** `fetchcore-shared-qdrant-1`, `fetchcore-shared-opensearch-1`, `fetchcore-operational-cloud-validation-solr-1`. A parada é reversível por `docker start`; não altera volumes. Verificar experimentos e consumidores antes.
- **REMOVE CANDIDATE, containers somente, após aprovação:** os três `fetchcore-bench-dedicated-*` e três `fetchcore-lab-*` já parados. Remover esses containers não elimina os volumes; preservar todos os dados. Confirmar ausência de fluxo de benchmark e necessidade de reprodução histórica.
- **MIGRATE:** `fetchcore-shared_solr-data` é legado Standalone, sem montagem; uma eventual migração deve ser exportação/transformação explícita com validação de conteúdo e identidade, jamais montagem no Cloud. Pode também ser arquivado após confirmação humana.
- **INVESTIGATE:** três containers OPERATIONAL legados parados e seus volumes; Qdrant BENCH 16,61 GB; Qdrant OPERATIONAL 18,35 GB; OpenSearch OPERATIONAL; volumes sem links/anônimos; validação Cloud; ntfy. Nenhum volume é candidato à remoção com evidência suficiente nesta etapa.

## 8. Arquitetura recomendada e diferença frente ao estado atual

| Componente | Estado atual | Alvo mínimo | Mudança necessária |
|---|---|---|---|
| DEV | SolrCloud saudável em 18983, volume próprio; Qdrant/OpenSearch ativos | SolrCloud persistente; alternativos opt-in | Confirmar consumidores e parar alternativos; revisar restart/Compose da worktree |
| OPERATIONAL | Cloud 28984 com índice ativo; Standalone legado parado | Cloud isolado e índices preservados | Documentar rota Cloud; decidir retenção dos legados após backup |
| BENCH | três containers dedicados parados; volumes grandes | Início somente sob demanda | Preservar dados e revisar comando de operação/retention |
| Lab/MCP | operacional em 38767/38766 com config Cloud; Lab legado parado | Bindings atuais explícitos | Desacoplar Compose legado de worktree temporária quando houver necessidade de recriação |
| Outros projetos | Langfuse, LiteLLM, Dagger, kind ativos; ntfy parado | Preservar conforme proprietários | Nenhuma alteração FetchCore unilateral |

Não há razão observada para reescrever todos os Compose. O ponto de maior risco é a dependência de worktree na origem de serviços legados e nos backends compartilhados; corrigir com revisão pontual, mantendo identidades persistidas e verificando comportamento antes/depois.

## 9. Plano por etapas, rollback e preservação

1. **Congelar evidências antes de agir:** repetir `docker ps -a`, `docker inspect`, `docker compose ls --all`, `docker system df -v`, APIs GET; registrar IDs de container, digests, aliases, contagens, labels e volumes. Conferir experimentos/consumidores de Qdrant, OpenSearch e validação.
2. **Provar backup e reconstrução:** criar export/backup autorizado dos volumes candidatos e teste de restauração isolado; guardar checksums, revision, fingerprint e configuração de embedding. No estado atual, nenhum backup válido foi comprovado por esta auditoria.
3. **Parada reversível separada:** após confirmação, parar primeiro Qdrant/OpenSearch compartilhados; medir RAM e verificar DEV, MCP e Lab. Depois considerar parar a validação Cloud apenas se não houver teste ativo. Não tocar no Solr OPERATIONAL principal.
4. **Racionalização de configuração:** revisar Compose da worktree `copilot` contra o checkout principal e fixar uma origem durável para reinícios futuros; aplicar mudanças somente em janela aprovada. Verificar que `up --no-recreate` não mascara drift.
5. **Remoção posterior:** avaliar os seis containers parados; volumes e imagens apenas com ownership, conteúdo, backup/restauração e retenção aprovados. Não executar limpeza global.

**Rollback:** para qualquer parada autorizada, `docker start <nome>` no mesmo container, conferir healthcheck/API e contagens/aliases registrados. Se uma recriação futura for aprovada, preservar volume, digest, Compose/env não sensível e aliases; restauração de dados só com backup testado. Não usar `down -v`, `volume prune` ou montagem Standalone→Cloud.

## 10. Economia fundamentada e decisões humanas

- **Paradas possíveis após confirmação:** 3 containers (Qdrant/OpenSearch shared; Solr validation). **Remoções candidatas após aprovação:** 6 containers já parados, preservando volumes. **Volumes a preservar:** todos os 42 até a triagem de ownership/conteúdo/backup; especialmente 18,35 GB e 16,61 GB Qdrant, 1,354 GB OPERATIONAL Cloud, 959,2 MB validation Cloud, 773 MB OPERATIONAL OpenSearch e 258,3 MB Standalone legado.
- **RAM potencial:** 1,43 GiB ao parar os dois backends shared; até 2,23 GiB incluindo validation. **Disco comprovadamente recuperável agora:** 0 GB. `docker system df` aponta 1,425 GB de build cache reclamável e 11,91 GB de imagens reclamáveis, mas exclusão, propriedade e impacto ainda não foram aprovados; remover containers parados liberaria no máximo 24,24 MB informados pelo Engine para os dez parados em conjunto, sem economia relevante de volume.
- **Decisões humanas:** quais experimentos ainda precisam de Qdrant/OpenSearch local; retenção das gerações e volumes históricos; validação Cloud ainda necessária; owner de ntfy, kind, Dagger e imagens antigas; política de backups e restauração; quando substituir dependência de worktree. Ordem prioritária: confirmar proteção dos índices Cloud → identificar consumidores → validar backup → paradas reversíveis → medir → corrigir origem/restart → remoção seletiva aprovada.

### Comandos propostos para a próxima etapa (não executados)

```sh
# Depois de confirmar ausência de consumidores e registrar estado:
docker stop fetchcore-shared-qdrant-1 fetchcore-shared-opensearch-1
docker stats --no-stream --all
# Separadamente, após confirmar que a validação não está em uso:
docker stop fetchcore-operational-cloud-validation-solr-1
# Rollback imediato, se necessário:
docker start fetchcore-shared-qdrant-1 fetchcore-shared-opensearch-1 fetchcore-operational-cloud-validation-solr-1
# Somente após aprovação adicional para remover containers parados; volumes ficam intactos:
docker rm fetchcore-bench-dedicated-qdrant-1 fetchcore-bench-dedicated-opensearch-1 fetchcore-bench-dedicated-solr-1 fetchcore-lab-api-1 fetchcore-lab-mcp-1 fetchcore-lab-web-1
```

## 11. Comandos de inspeção executados e limites

```sh
git status --short --branch; git worktree list; git branch -r; git remote -v; gh pr list --state open
docker version; docker info; docker compose ls --all --format json
docker ps -a --no-trunc --format '{{json .}}'; docker inspect <ids>
docker volume ls; docker volume inspect <volumes>; docker network ls; docker network inspect <redes>
docker image ls --digests; docker system df -v; docker stats --no-stream --all
.venv/bin/python scripts/shared_infra.py ready --environment dev --repository oalangomes/fetchcore --backend solr
GET /solr/admin/info/system, /solr/admin/collections?action=LIST, LISTALIASES, /select?rows=0
GET /collections e /collections/<nome> no Qdrant; GET /_cat/indices no OpenSearch
rg e sed em infra/compose, scripts, docs e workflows; leitura seletiva do YAML operacional
```

Limites: APIs não inspecionaram dados de serviços parados; não foi possível provar backup válido nem tamanho físico recuperável do disco virtual Windows; RAM/CPU são amostras pontuais; `docker system df` e `docker ps -a` divergem por três containers não reconciliados. Não foram lidos segredos, conteúdo de documentos indexados ou dados de volumes. O estado de Git observado foi `master@00d6cdc` limpo, com quatro PRs abertos (#239, #238, #233, #232); o `.codex/task-state.md` descreve trabalho anterior e não foi usado como estado corrente.
