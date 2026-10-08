<!-- fetchcore-localization: {"body_sha256":"478200a1ff5d0b66ba4f6d14e10063a890e43bd7437ffea2df2c85c87b0615d5","fingerprint":"916162d4883d4da53f0591c5151c198e7bff7a312af9fe7dacadf0d7707bef3f","glossary_sha256":"5cb83196075b9648d2d58285161323dbb0f59eea97c728e83e111d6e9497d705","locale":"pt-BR","model":"reviewed-ptbr-v1","provider":"bootstrap","schema":1,"source":"README.md","source_sha256":"7a7050b2ba3d55825b9332b4dde0f6aea10d8b074bc161f6dcb00886268e2501"} -->
# FetchCore

[English](README.md) | [Português (Brasil)](README.pt-BR.md)

O FetchCore transforma repositórios de software em evidências técnicas pesquisáveis e tipadas, com proveniência da fonte. Ele adquire e indexa conhecimento dos repositórios e retorna candidatos de retrieval por meio de contratos independentes de backend. Os consumidores decidem se a evidência atende à tarefa e qual autoridade atribuir a ela.

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
    → KnowledgePacks and typed relations → projection → rebuildable search/vector stores
    → backend-neutral retrieval → application service → CLI / MCP / SDK consumers
```

O Repository Knowledge canônico mantém a identidade e a proveniência da fonte; os armazenamentos de backend são projeções substituíveis. Os adaptadores de retrieval atuais são local semantic, Solr, OpenSearch e Qdrant vector. O adaptador de embedding atual é Ollama. O retrieval oferece caminhos lexical, dense e hybrid quando o backend escolhido declara suporte; a indexação operacional pela CLI atualmente usa Solr. Projeção em grafo e busca federada continuam sendo direções futuras.

O FetchCore está na versão `0.1.0.dev0`. Os contratos e a baseline são versionados, enquanto integrações públicas e capacidades ainda evoluem. Consulte a [política de releases](docs/release-policy.md) para o bootstrap inicial de `v0.0.1` e releases automatizadas posteriores. Comece pelo [índice da documentação](docs/pt-BR/README.md) para arquitetura, contratos, guias, benchmarks, decisões, migrações e registros históricos de extração.
