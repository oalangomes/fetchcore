# Investigação de latência Solr — baseline v1

Data da investigação: 2026-09-30/2026-10-01  
Baseline: `fetchcore.baseline-v1`  
Corpus: `oalangomes/NeuroTrack_MS@74ce3244956a65baed61f1dae6f498cb2be4f3b7`

## 1. Executive summary

- A latência anômala não está no Lucene/BM25/KNN: um snapshot `select` observado levou
  `1.017 s` no cliente com `QTime=6 ms` no Solr.
- O cliente `urllib.request.urlopen()` envia `Connection: close`; o adapter abria uma conexão
  para cada request e `SolrBackend.search()` também criava um transport novo em cada busca.
- No Solr real, o mesmo request levou `1.035 s` em uma conexão nova e, reutilizando o mesmo
  socket, `4.3 ms` e `1.0 ms`. A penalidade é, portanto, por conexão, não por query.
- O baseline fecha numericamente: lexical = snapshot/conexão (~939 ms) + lexical/conexão
  (~953 ms) + poucos ms locais; hybrid = snapshot (~948 ms) + dense (~952 ms) + lexical
  (~954 ms) + embedding (~16 ms) + fusion (~0 ms).
- Lexical executa 2 requests Solr por busca; hybrid executa 3. OpenSearch executa a mesma
  quantidade lógica. Não há ping, schema lookup ou request implícito no caminho de retrieval.
- `rows=80` e o `fl` explícito retornam conteúdo e campos de Projection v3; uma resposta lexical
  observada teve aproximadamente 245 KB. Isso merece benchmark próprio, mas o processamento
  local residual ficou em ~17 ms no smoke test e não explica atrasos de ~1 s.
- A menor correção comprovada é reutilizar a conexão HTTP. O snapshot continua sendo consultado
  remotamente em toda busca; nenhum cache, ranking, budget ou semântica foi alterado.
- A suíte completa passou com 149 testes. O ambiente Docker foi reiniciado antes do rerun
  end-to-end pós-patch; o before/after real disponível é o teste isolado do mesmo endpoint e da
  mesma query, não um novo baseline promovido.

## 2. Execution path

### Lexical

1. `BaselineRunner.run()` executa um warm-up por caso e depois chama
   `backend.search()` para cada repetição/caso.
2. `SolrBackend.search()` valida o budget, materializa `SolrSettings`, obtém o transport e cria
   `SolrIndexStore`.
3. `SolrIndexStore.load_snapshot(repository)` faz `GET select` com
   `record_kind:snapshot`, exige exatamente um registro e valida status, projection version e
   projection fingerprint.
4. O backend valida requested revision e `document_count > 0`.
5. `_search_params()` constrói a query e o backend faz o segundo `GET select`.
6. `_search_documents()` interpreta a resposta; Projection v3 calcula matches, scores,
   capabilities, observations, relationships e query-match diagnostics.
7. O backend materializa até 20 `SearchResult` e cria `RetrievalTelemetry`.
8. `BaselineRunner` transforma a resposta em qualidade, latência e custo observados.

Requests de rede: snapshot `GET select` → lexical `GET select`.

### Hybrid

Os passos 1–4 são idênticos. Depois:

1. `_prepare_dense_query()` valida a identidade de embedding persistida e solicita o embedding
   da query ao Ollama.
2. `_solr_dense_candidates()` faz `POST select` JSON com KNN, interpreta até 80 candidatos e
   preserva scores do provedor.
3. `_solr_lexical_candidates()` faz `GET select` lexical e executa diagnostics Projection v3.
4. `_hybrid_response()` executa RRF local, materializa resultados e telemetry.

Requests de rede FetchCore: snapshot `GET select` → embedding Ollama → dense `POST select` →
lexical `GET select`. Contando apenas Solr: 3 requests.

Dense e lexical são sequenciais. Não foram paralelizados nesta investigação.

## 3. Latency breakdown

Os percentis do baseline têm 400 observações por arm. Medianas de componentes são apresentadas
separadamente e podem diferir alguns ms da mediana da soma.

| componente | lexical p50 | hybrid p50 | evidência |
|---|---:|---:|---|
| settings/transport/store + snapshot lookup/validation | ~939 ms | ~948 ms | residual antes dos stages no baseline; smoke: snapshot request 1.066 ms, `QTime=7 ms` |
| query embedding | — | 16 ms | stage `embedding` |
| lexical total | 953 ms | 953.5 ms | stage `lexical`; smoke request 1.084 ms, `QTime=1 ms` |
| dense/KNN total | — | 952 ms | stage `dense` |
| fusion/RRF | — | 0 ms | stage `fusion` |
| parsing + Projection v3 + materialização local | contido em ~17 ms no smoke | pequeno e contido nos stages/residual | total smoke 2.167 ms menos os 2 requests (2.150 ms) |
| total | 1.893 ms | 2.867 ms | `aggregate.duration_ms.p50` |

O smoke instrumentado foi uma query real do Ground Truth e mediu o request do snapshot, o
request lexical, `QTime`, bytes JSON e o total do backend. Ele não deve substituir os percentis
de 400 amostras; sua função é atribuir o residual ao transporte.

### Separação Solr / transporte / FetchCore

| caminho | medição |
|---|---:|
| Solr `QTime`, snapshot | 6 ms |
| HTTP direto, conexão nova, mesmo snapshot | 1.034,9 ms |
| HTTP direto, mesmo socket, segunda chamada | 4,3 ms |
| HTTP direto, mesmo socket, terceira chamada | 1,0 ms |
| `UrllibSolrTransport`, snapshot no smoke | 1.066 ms (`QTime=7 ms`) |
| `UrllibSolrTransport`, lexical no smoke | 1.084 ms (`QTime=1 ms`) |
| `SolrBackend.search()` lexical no smoke | 2.167 ms |

O teste direto mostra redução de 99,6% no primeiro request reutilizado e 99,9% no seguinte,
sem alterar endpoint ou parâmetros. O patch usa essa mesma propriedade HTTP/1.1. Não foi
possível medir um novo p50/p95 end-to-end após o patch porque os containers e volumes do
ambiente desapareceram durante a continuação da sessão.

## 4. HTTP request count

| operação | Solr lexical | Solr hybrid | OpenSearch lexical | OpenSearch hybrid |
|---|---:|---:|---:|---:|
| snapshot/currentness | 1 | 1 | 1 | 1 |
| lexical retrieval | 1 | 1 | 1 | 1 |
| dense retrieval | 0 | 1 | 0 | 1 |
| ping/schema | 0 | 0 | 0 | 0 |
| total no backend | 2 | 3 | 2 | 3 |
| embedding externo | 0 | 1 | 0 | 1 |

Antes do patch, cada request `urllib` criava uma conexão TCP/HTTP separada. Depois do patch,
uma instância de backend mantém um transport e cada thread reutiliza sua conexão enquanto o
servidor a mantiver aberta. A quantidade e a ordem de requests não mudam.

## 5. Root causes

### Confirmed

- `urllib.request.urlopen()` fecha a conexão; o adapter não possuía pool/reuse.
- `SolrBackend.search()` recriava settings, transport e store em toda busca.
- O Solr deste ambiente cobra aproximadamente 1 s no primeiro request de cada conexão, embora
  `QTime` seja de poucos ms.
- O snapshot lookup remoto é responsável pelo overhead não atribuído a stages, e cada canal de
  retrieval paga outra conexão nova.

### Probable

- O atraso de ~1 s ocorre na camada HTTP/Jetty/Docker após o TCP connect e antes do primeiro
  byte. O TCP connect medido foi ~0,1 ms. A investigação não atribuiu esse atraso a uma opção
  específica do Jetty ou do host.
- O payload de ~245 KB adiciona alguns ms de transferência/parsing, mas não centenas de ms.

### Disproven

- Lucene/BM25/KNN como causa dominante: `QTime` observado ficou em 1–7 ms.
- Projection v3 diagnostics e materialização como causa dominante: todo o residual local do
  smoke ficou em ~17 ms.
- Ping, schema lookup, highlighting ou debug implícitos: não existem no execution path.
- `fl=*` no retrieval: lexical/dense usam uma lista explícita; apenas o pequeno snapshot usa
  `fl=*`.
- Indexing/commit como parte da latência medida: indexação termina antes do protocolo de
  retrieval e possui telemetry separada.

## 6. Comparação com OpenSearch

- OpenSearch executa o mesmo número lógico de requests e também valida snapshot a cada busca.
- Ambos os adapters antigos usam `urlopen`, mas o OpenSearch não apresentou a penalidade fixa de
  ~1 s por conexão: p50 lexical ~30 ms; hybrid ~66 ms.
- OpenSearch exclui apenas o vetor de `_source`; Solr usa `fl` explícito que também exclui o
  vetor. Ambos retornam `content` e os campos necessários para diagnostics/materialização.
- As diferenças de ranking/qualidade são pequenas e não explicam a diferença de latência.

## 7. Configuração e query shape Solr

Lexical usa `edismax`, cláusulas já fielded por tipo de sinal, `q.op=OR`, filtros de
`record_kind`, repository e knowledge types, `rows=80`, `sort=score desc,id asc` e `wt=json`.
Não há highlighting, debug ou explain.

O `fl` é explícito e inclui `content`, identidade, provenance e campos usados por Projection v3.
O conteúdo integral é necessário para o `SearchResult` atual. Reduzir janela ou campos seria uma
mudança separada e não foi feita.

KNN usa JSON Request API com `{!knn f=embedding_vector topK=80}`, vetor de 768 dimensões,
filtros equivalentes, `limit=80`, o mesmo conjunto de campos (sem o vetor) e ordenação estável.

## 8. Snapshot lookup

O lookup protege:

- existência e unicidade do snapshot por repository;
- `status=ready`;
- projection version e fingerprint;
- requested revision/currentness;
- corpus/document count;
- para dense/hybrid, provider, model, dimensions, serializer mode/version/fingerprint.

Um cache TTL silencioso poderia servir snapshot stale e enfraquecer fail-closed. Uma chave
adequada teria de incluir endpoint, collection, repository, requested revision, projection
identity e embedding identity; ainda seria necessária invalidação autoritativa entre processos
ou validação condicional remota. Como keep-alive reduz o lookup a poucos ms, cache de snapshot
não é recomendado agora.

## 9. Recomendações

### NOW

- Usar conexão HTTP/1.1 persistente no adapter Solr e reutilizar o transport na instância de
  `SolrBackend`.
- Manter `load_snapshot()` remoto em toda busca.
- Manter o harness `investigate_solr_latency.py` para repetir p50/p95 quando o ambiente congelado
  estiver disponível.

### NEXT

- Reexecutar os 20 casos lexical/hybrid com 1 warm-up e pelo menos 20 repetições após restaurar o
  mesmo Solr 9.10.1 e o índice congelado.
- Medir separadamente uma projeção de resposta menor (`fl`/content) somente como experimento de
  uma variável; não promover sem validar diagnostics, resultados e qualidade.
- Investigar o motivo específico do atraso Jetty/Docker no primeiro request por conexão.

### LATER

- Se snapshot continuar relevante após keep-alive, desenhar conditional validation/ETag ou uma
  geração autoritativa backend-neutral; não usar TTL silencioso.
- Avaliar pool limitado se o backend for usado simultaneamente por muitas threads. O patch atual
  usa uma conexão por thread para não serializar buscas concorrentes.

### DO NOT DO YET

- Não alterar boosts, BM25, RRF, KNN, top-k, candidate window, Projection v3 ou embedding.
- Não paralelizar dense e lexical sem benchmark controlado.
- Não cachear snapshot nem remover validações de currentness.
- Não trocar toda a stack HTTP ou adicionar dependência externa sem evidência adicional.

## 10. Patch e validação

O patch adiciona `PersistentSolrTransport`, preserva `UrllibSolrTransport`, torna o transporte
persistente o default de `SolrBackend` e reutiliza o transport enquanto os settings forem
iguais. A conexão é thread-local; erros fecham a conexão da thread e continuam fail-closed.

Testes adicionados provam:

- dois requests usam o mesmo socket/porta cliente;
- parâmetros multi-valued continuam codificados corretamente;
- o backend reutiliza o transport para settings equivalentes e o substitui quando mudam.

Validação executada:

- testes focados: `22 passed`;
- suíte completa com extras de desenvolvimento: `149 passed`;
- Ruff direcionado aos arquivos alterados: passou;
- Ruff/Mypy globais: ainda falham por débitos preexistentes em arquivos não alterados.

O patch preserva `intent → retrieval → evidence/candidates`, provenance, currentness,
determinismo, backend neutrality do core e schema compatibility.

## Referências de transporte

- [Python `urllib.request`](https://docs.python.org/3/library/urllib.request.html): `urlopen`
  usa HTTP/1.1, mas inclui `Connection: close`.
- [Python `http.client`](https://docs.python.org/3/library/http.client.html): uma
  `HTTPConnection` suporta requests sequenciais após a resposta anterior ser consumida.
- [Apache Solr client APIs](https://solr.apache.org/guide/solr/latest/deployment-guide/client-apis.html):
  clientes Solr usam requests HTTP e interpretam as respostas retornadas.
