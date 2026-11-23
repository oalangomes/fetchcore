<!-- fetchcore-localization: {"body_sha256":"b3f61072aa5477215852518824d3889f2cb5566b63ec2a12bd2010b2ca4d56fe","fingerprint":"6e540895dcd11906b197735c4ad6e1eb3e336fc27f67c8857bdc1d4f9b36c51a","glossary_sha256":"5cb83196075b9648d2d58285161323dbb0f59eea97c728e83e111d6e9497d705","locale":"pt-BR","model":"issue-30-ptbr-v1","provider":"reviewed-manual","schema":1,"source":"README.md","source_sha256":"cdbfc072e09ac54d251234060c7de34490f293b0636b9b7bb90b292aa574c8b3"} -->
# FetchCore

[English](README.md) | [Português (Brasil)](README.pt-BR.md)

O FetchCore constrói automaticamente conhecimento técnico estruturado a partir de repositórios e permite compor mapas rastreáveis e navegáveis desse conhecimento para agentes e outros consumidores. As evidências preservam identidade e proveniência da fonte por meio de contratos independentes de backend. Os consumidores decidem se a evidência atende à tarefa e qual autoridade atribuir a ela.

O FetchCore é responsável pela varredura de repositórios, extração, conhecimento canônico, projeção, contratos e adaptadores de retrieval, indexação incremental e interfaces CLI/MCP. Ele não é responsável por planejamento de agentes, conclusão de tarefas, aprovação ou autoridade de execução.

## Início rápido

Requer Python 3.11 ou superior. Para configurar um ambiente local de desenvolvimento:

```bash
python -m pip install -e ".[dev]"
make check
```

Para servir um repositório local por MCP stdio:

```bash
export FETCHCORE_WORKSPACE="$PWD"
export FETCHCORE_REPOSITORY="owner/repo"
export FETCHCORE_BACKEND="local-semantic"
fetchcore-mcp
```

O [guia de introdução](docs/guides/getting-started.md) aborda contêineres e serviços opcionais de backend. Os guias de [indexação](docs/guides/self-hosted-indexing.md), [busca](docs/guides/searching.md) e [configuração de runtime](docs/guides/runtime-configuration.md) trazem instruções para tarefas específicas.

## Arquitetura em resumo

```text
Repository sources → registry / indexer → canonical Repository Knowledge
    → KnowledgePacks and typed relations → rebuildable search/vector/graph projections
    → backend-neutral retrieval → application service → CLI / MCP / SDK consumers
```

O Repository Knowledge canônico mantém a identidade e a proveniência da fonte; os armazenamentos de backend são projeções substituíveis. Os adaptadores de retrieval atuais são local semantic, Solr, OpenSearch e Qdrant vector. O adaptador de embedding atual é Ollama. O retrieval oferece caminhos lexical, dense e hybrid quando o backend escolhido declara suporte; a indexação operacional pela CLI atualmente usa Solr. `find_symbol` e `get_related` 1-hop já existem. A GraphProjection v1 opcional usa SolrCloud para mapas de até três hops com relações resolvidas e limites explícitos; extração de fluxos de negócio completos e busca federada ainda não são capacidades gerais.

O FetchCore está na versão `0.1.0.dev0`. Os contratos e a baseline são versionados, enquanto integrações públicas e capacidades ainda evoluem. Consulte a [política de releases](docs/release-policy.md) para o bootstrap inicial de `v0.0.1` e releases automatizadas posteriores. Comece pelo [índice da documentação](docs/pt-BR/README.md) para arquitetura, contratos, guias, benchmarks, decisões, migrações e registros históricos de extração.
