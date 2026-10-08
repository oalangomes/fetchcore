<!-- fetchcore-localization: {"body_sha256":"7182aa53010f46707de52dbe3d19ab8af4a6205b22627848ac4072a618737a1a","fingerprint":"a1c91f4e7d13575b4d530d23f1fc084eca2ff36172de2ede30eee853960b4248","glossary_sha256":"5cb83196075b9648d2d58285161323dbb0f59eea97c728e83e111d6e9497d705","locale":"pt-BR","model":"reviewed-ptbr-v1","provider":"bootstrap","schema":1,"source":"docs/README.md","source_sha256":"a3f300e81c37ac51b23d1ce97b42867aba63858a28b2827b78c04665690059d2"} -->
# Documentação do FetchCore

[English](../README.md) | [Português (Brasil)](README.md)

Use este índice para encontrar o contrato atual. Notas históricas de extração e resultados individuais de experimentos são evidências de trabalho anterior, não a arquitetura atual.

| Quero… | Comece aqui |
| --- | --- |
| Entender o produto e seus limites | [Visão geral da arquitetura](../architecture/overview.md) e [limites dos módulos](../architecture/module-boundaries.md) |
| Entender conhecimento canônico e projeções | [Repository Knowledge](../architecture/repository-knowledge.md) e [projeção](../architecture/projection.md) |
| Entender retrieval e adaptadores | [Retrieval](../architecture/retrieval.md) e [adaptadores/composição](../architecture/adapters-and-composition.md) |
| Entender as interfaces | [Limite de serving](../architecture/serving.md) |
| Consultar contratos e capacidades | [Contratos](../reference/contracts.md) e [capacidades](../reference/capabilities.md) |
| Instalar e indexar um repositório | [Introdução](../guides/getting-started.md) e [indexação self-hosted](../guides/self-hosted-indexing.md) |
| Registrar ou inspecionar uma fonte de repositório | [Registro de repositórios](../guides/repository-registry.md) |
| Planejar, sincronizar ou reconstruir um repositório registrado | [Ciclo de indexação](../guides/repository-indexing.md) |
| Buscar ou executar MCP | [Busca](../guides/searching.md) e [MCP](../guides/mcp.md) |
| Configurar ou adicionar um backend | [Configuração de runtime](../guides/runtime-configuration.md), [referência de configuração](../reference/configuration.md) e [adição de backend](../guides/adding-a-backend.md) |
| Entender manutenção da documentação PT-BR | [Localização da documentação](../guides/documentation-localization.md) |
| Entender releases | [Política de releases](../release-policy.md) e [visão técnica de v0.0.1](../releases/initial-release.md) |
| Reproduzir um benchmark | [Convenção de experimentos](../benchmarks/protocol.md), [protocolo baseline v1](../benchmarks/baseline-v1.md) e [experimento SearchIntent v2](../benchmarks/search-intent-v2.md) |
| Entender uma decisão durável | [Índice de ADRs](../adr/README.md) |
| Acompanhar uma migração ou verificar proveniência de extração | [Migração de namespace](../migrations/fetchcore-namespace.md) e [extração histórica](../history/extraction/README.md) |

## Vocabulário

[Evidência e proveniência](../concepts/evidence-and-provenance.md) explica observações de origem, correspondências de consulta e rastreabilidade. [Intenção e escopo de busca](../concepts/search-intent.md) distingue os contratos de aquisição atuais das mudanças planejadas. [Identidade do repositório](../concepts/repository-identity.md) explica o limite do repositório.

## Regras da documentação

Use nomes de arquivo em minúsculas e kebab-case (exceto ADRs numeradas), um H1 por página, links internos relativos, rótulos explícitos para comportamento histórico ou proposto, diagramas de texto portáveis e comandos copiáveis. Arquitetura registra limites; conceitos definem vocabulário; guias descrevem tarefas; referência declara contratos atuais; benchmarks preservam evidência reproduzível; ADRs registram decisões duráveis e alternativas. Evite linguagem de marketing na referência e não duplique explicações canônicas.

- [Retrieval exato direcionado](../concepts/targeted-retrieval.md)
