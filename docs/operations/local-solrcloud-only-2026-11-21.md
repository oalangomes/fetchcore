# Transição local para SolrCloud-only — 2026-11-21

Escopo: Docker Desktop local, DEV, OPERATIONAL e BENCH do FetchCore. Esta é a
continuação da [auditoria somente de leitura](local-docker-audit.md), revalidada
antes das paradas. Não altera adapters, contratos, índice publicado ou produção.

## Estado revalidado antes das mudanças

- 27 containers visíveis (17 ativos), 42 volumes, 13 redes e 47 imagens. O
  `docker system df` contou três containers adicionais não visíveis no inventário
  do FetchCore; não foram tocados. Todas as ações tiveram o projeto e os mounts
  conferidos por `docker inspect`.
- DEV `fetchcore-shared-solr-1`: SolrCloud saudável em `127.0.0.1:18983`,
  volume `fetchcore-shared_solrcloud-data`, zero coleções e aliases. Nenhuma
  indexação DEV foi executada.
- OPERATIONAL `fetchcore-operational-cloud-solr-1`: SolrCloud saudável em
  `127.0.0.1:28984`, volume `fetchcore-operational-cloud_solrcloud-data`. Alias
  `fc-operational-fetchcore-635ab7352c2850f2-cloud` apontava e continua
  apontando para `fcg-0226c0ce8a-10724d819911`: 5.708 documentos, sendo
  5.707 registros de conhecimento e um snapshot. O snapshot informa revisão
  `00d6cdc21643bce5a8ce060d9cfbae7f467725da` e checksum do corpus
  `8f033bd335f6a6361655956d4652fc45a0e3ebc5dfc4ad1d415769028516c6e1`.
  As outras quatro gerações permaneceram intactas (5.609, 5.609, 5.666 e
  5.609 documentos); a coleção seed estava vazia.
- O SolrCloud de validação em `127.0.0.1:18984` guardava duas gerações
  (5.585 e 5.609 documentos) e dois aliases. Não havia experimento/processo
  benchmark ativo, sessão TCP estabelecida nem outro container na rede isolada
  de validação. Seus dados foram preservados.
- Qdrant compartilhado em `16333/16334` tinha duas coleções FetchCore sem
  pontos; OpenSearch em `19200/19600` tinha dois índices FetchCore sem documentos.
  Não havia sessões TCP estabelecidas ou consumidores FetchCore ativos nessas
  redes além do Solr compartilhado. Serviços de outros projetos não foram
  usados como evidência de ausência de consumidores futuros.
- MCP em `127.0.0.1:38766`; Search Lab em `127.0.0.1:38767` com API interna.
  Ambos montavam o checkout ativo como `/workspace:ro`. O runtime servia a
  revisão antiga `32a288977fc03bf7b903ef38d258c91ac975fe42`; a busca
  falhava com `snapshot_revision_mismatch`. O provider Ollama configurado não
  respondia nem no WSL nem em `host.docker.internal:11434` dentro do container.
- RAM pontual antes da parada: OpenSearch 1,338 GiB, Qdrant 93,27 MiB,
  validação Solr 820,6 MiB; soma aproximada de **2,23 GiB**. É memória desses
  processos no instante da medição, não medição de RAM livre do host.

## Ações e estado posterior

Depois das checagens acima, `docker stop` parou, sem remoção, exatamente:

1. `fetchcore-shared-opensearch-1`;
2. `fetchcore-shared-qdrant-1`;
3. `fetchcore-operational-cloud-validation-solr-1`.

Os três mantêm política `restart: unless-stopped` e seus volumes. A parada
manual deve persistir após reinício do daemon nessa política; **não foi feito
um reinício real do Docker Desktop**, pois afetaria LiteLLM, Langfuse e Dagger.
Os healthchecks de Solr DEV/OPERATIONAL, API e Lab estavam saudáveis depois.
O Compose compartilhado agora inicia só Solr por padrão; OpenSearch/Qdrant
exigem seleção explícita ou profile `adapter-tests`. O BENCH dedicado não foi
iniciado. Seu contêiner Solr antigo usa `solr-data` Standalone: a CLI rejeita
reutilizá-lo como Cloud sem a opção explícita `--replace-legacy-bench-solr`.
Essa opção recria somente o contêiner e mantém o volume antigo separado.

Permaneceram ativos os 14 containers visíveis: `fetchcore-shared-solr-1`,
`fetchcore-operational-cloud-solr-1`, `fetchcore-operational-mcp-api-1`,
`fetchcore-operational-mcp-mcp-1`, `fetchcore-operational-mcp-lab-1`, os seis
`langfuse-coders-*`, os dois `litellm-local-*` e
`dagger-engine-v0.21.11`. Todos os 42 volumes e 13 redes permaneceram; nenhum
volume, imagem ou rede foi removido. A economia observada nos três containers
parados foi aproximadamente **2,23 GiB de RAM de processos**; espaço em disco
recuperado: **0 GB**. A amostra posterior de containers FetchCore ativos mediu
DEV Solr 747,7 MiB, OP Solr 813,9 MiB, MCP 66,96 MiB, API 46,64 MiB e Lab
9,05 MiB. Os valores oscilam com a carga.

## Binding e smoke de evidências

Um clone Git independente, limpo e destacado na revisão do snapshot foi
preparado em diretório persistente do operador fora do checkout. API e MCP o
montam em `/workspace:ro` por `FETCHCORE_OPERATIONAL_SOURCE_PATH`; o arquivo
de runtime também é externo e montado read-only. A configuração original hybrid
foi preservada. Como Ollama estava indisponível, a configuração ativa foi
explicitamente definida como `lexical`, com dense/hybrid desativados. A
reconciliação validou revisão, coleção, schema, fingerprint, workspace e mounts
antes de recriar apenas API/MCP. `embedding` foi marcado `not_required`, não
simulado como sucesso. O Lab precisou de um rebuild da imagem web existente
para aplicar o proxy com resolução dinâmica do DNS da API após essa recriação.

Verificações observadas:

| Superfície | Resultado |
| --- | --- |
| DEV SolrCloud | `ready`; lista de coleções vazia em 18983. |
| OP SolrCloud | Alias acima com 5.708 documentos; nenhuma geração alterada. |
| CLI `search` | `found`, três fontes reais com revisão do snapshot e checksums. |
| CLI `source`, `find-symbol`, `get-related` | Saída real: 178 bytes de fonte, um símbolo (`partial` pela cobertura estrutural) e três relações. |
| MCP `search`, `get_source`, `find_symbol`, `get_related` | 3 hits, conteúdo de `src/fetchcore/repository/sources.py`, um símbolo e 3 relações, todos vinculados a `oalangomes/fetchcore` e à revisão `00d6cdc…`. |
| Search Lab | `/` e `/api/config` HTTP 200; `/api/readiness` `ready`; `/api/search` `found` com três hits; `/api/source` devolveu conteúdo da fonte e revisão correta. |

O `source_ref` testado foi
`repository://oalangomes/fetchcore/src/fetchcore/repository/sources.py`.
O resultado estrutural `partial` é explícito: a cobertura de símbolos não é
completa. Dense/hybrid não foram exercitados porque o provider estava
indisponível; não houve fallback silencioso nem reindexação. Uma consulta V2
sem `schema_version: 2` recebeu `schema_mismatch` HTTP 422, como previsto pelo
contrato. O GET inicial do Lab retornou 502 antes do rebuild da imagem antiga;
após o rebuild, as chamadas acima passaram.

DEV ainda precisa de criação de coleção e indexação **explícitas** para
recuperar suas próprias evidências. Primeiro selecione o recurso com
`python scripts/shared_infra.py env --environment dev --repository oalangomes/fetchcore`,
confira o configset Cloud e use o fluxo de [indexação self-hosted](../guides/self-hosted-indexing.md)
com workspace/revisão e destino DEV declarados; depois confira snapshot,
alias e revisão antes de servir. `shared_infra.py up` apenas verifica o serviço;
não cria coleção nem executa indexação. Não reutilize o alias OPERATIONAL para
preencher DEV.

## Inventário de limpeza posterior

Tamanhos são de `docker system df -v`; links a containers parados ainda contam.
Classificação condicionada a nova revisão de consumidores e retenção no momento
de qualquer remoção:

| Classe | Recursos e tamanho | Condição / risco |
| --- | --- | --- |
| A. Parados, sem consumidores ativos observados | Três containers parados acima. Outros parados: `fetchcore-bench-dedicated-{solr,opensearch,qdrant}-1`, `fetchcore-lab-{api,mcp,web}-1`, `fetchcore-operational-{solr,opensearch,qdrant}-1`, `ntfy-local-ntfy-1`. | A ausência de consumidor foi verificada apenas para os três parados nesta transição. Os demais exigem reconciliação própria. |
| B. Containers candidatos à remoção, preservando volumes | Três parados nesta transição; seis `fetchcore-lab-*`/BENCH legados conforme linhas acima; três `fetchcore-operational-*` legados. | Remoção de container exige nova checagem de ownership e rollback; BENCH Solr exige transição explícita para Cloud. Não executada. |
| C. Volumes potencialmente reconstruíveis | `fetchcore-shared_opensearch-data` 240,4 kB e `fetchcore-shared_qdrant-data` 705,2 MB: índices FetchCore vazios observados. Volumes de benchmarks antigos `fetchcore-bench-{a20261021a01,b20261021b01}_{opensearch,qdrant,solr}-data` somam cerca de 151 MB. | Qdrant compartilhado pode conter cache/metadata; benchmark histórico pode carregar evidência de medição. Confirmar conteúdo, origem e reprodutibilidade antes de excluir. |
| D. Backup/restauração necessários | `fetchcore-operational_qdrant-data` **18,35 GB**; `fetchcore-bench-dedicated_qdrant-data` **16,61 GB**; `fetchcore-operational-cloud-validation_solrcloud-data` 960,3 MB; OP Cloud ativo 1,355 GB; `fetchcore-bench-dedicated_solr-data` 294,7 MB; `fetchcore-operational_opensearch-data` 773 MB. | Conteúdo, backup e possibilidade de reconstrução não foram comprovados. Os dois grandes Qdrant não são descartáveis por inferência. OP Cloud ativo é essencial. |
| E. Outros projetos intocados | Langfuse, LiteLLM/PostgreSQL, Dagger, ntfy, kind e volumes/imagens anônimos ou sem ownership FetchCore. | Não pertencem à limpeza FetchCore. |

Imagens antigas `fetchcore-lab-{dev,bench,operational}-{mcp,web}` tinham zero
containers ativos associados no inventário, mas sua remoção e eventual cache
dependem de revisão separada. Volumes anônimos com links não conciliados não
foram classificados como órfãos. **Nenhum prune foi executado**. Não há plano
de exclusão automática; aprovação específica deve fixar nomes, backup, retenção,
tamanho e consumidores imediatamente antes de cada lote.

## Rollback e reinício

1. Reconfira consumidores, ownership e portas. Para restaurar os serviços
   opcionais exatamente parados, execute `docker start` com os três nomes da
   seção de ações. Seus volumes foram mantidos. Verifique healthchecks, aliases
   e índices depois da partida.
2. Para retornar ao modo hybrid, restaure o arquivo de configuração do operador
   original no Compose e uma fonte na revisão publicada, confirme Ollama/modelo
   768d e execute a reconciliação com `--activate`. A configuração lexical
   separada permanece disponível. Nunca edite o alias Solr para fazer rollback
   de serving.
3. Antes de reiniciar Docker Desktop, confira que a configuração persistida de
   Compose e os arquivos de runtime/source externos continuam acessíveis.
   `unless-stopped` mantém os três serviços opcionais manualmente parados;
   DEV/OP Solr, API/MCP e Lab devem subir com suas políticas atuais. Após o
   reinício, execute novamente readiness, alias/count, busca e leitura da fonte.
   Esta verificação pós-reinício é pendente porque não foi feito reboot do Engine.
