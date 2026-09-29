# FetchCore

FetchCore turns software repositories into searchable, typed, provenance-aware technical knowledge across multiple repositories.

It is the standalone Repository Knowledge and retrieval subsystem extracted from AgentsOrchNext.

## Scope

FetchCore owns:

- repository sources and materialization;
- safe scanning and corpus admission;
- canonical repository knowledge;
- typed source-derived evidence;
- provenance, revision, checksum and currentness;
- incremental and multi-repository indexing;
- KnowledgePacks;
- backend-neutral retrieval contracts;
- search/vector projections;
- Solr, OpenSearch and Qdrant adapters;
- fusion/RRF and post-acquisition reranking;
- future graph projection and MCP serving adapters.

FetchCore does **not** own:

- agent lifecycle;
- Demand or Stage semantics;
- ContextNeed / ContextView;
- ContextResolver;
- executor orchestration;
- artifact approval or canonical work state.

Consumers such as AgentsOrch, GAC tooling, coders and MCP clients integrate through adapters.

## Extraction provenance

Initial extraction sources are frozen to:

```text
AgentsOrchNext main
2c85d455c1a8ceed53e128068d0c1da1a5e048cd

AgentsOrchNext PR #300 head
8655d4757f8668b3bc544e896ce0a1df645cc620
```

The main ref contains the finalized Benchmark 007 Codebase Memory study.

PR #300 is retained as a secondary extraction source for SAP Commerce retrieval assets and generic large-corpus/indexing fixes. Its SAP G0/G1 scientific run was blocked by provider/runtime infrastructure; FetchCore does not treat that blocked run as a product result.

## Target architecture

```text
Repository Sources
        ↓
Repository Registry / Collections
        ↓
RepositoryIndexer
        ↓
Canonical Repository Knowledge
        ↓
KnowledgePacks / typed relations
        ↓
Projection
   ┌────┼─────┐
   ↓    ↓     ↓
Search Vector Graph
   └────┼─────┘
        ↓
Retrieval API
   ┌────┼────┐
   ↓    ↓    ↓
 CLI   MCP  SDK/API
```

Search/vector/graph stores are rebuildable projections. Canonical repository knowledge remains backend-neutral.

## Extraction rule

The first milestone is an extraction, not a rewrite.

Behavior that is already proven in AgentsOrchNext should be transplanted with the smallest possible semantic change, then decoupled at the boundary and validated through equivalence benchmarks.

New retrieval features should be implemented here after the extraction gate passes.


## MCP serving

FetchCore can expose retrieval through a thin MCP v2 adapter without coupling
the transport to any specific retrieval backend.

```text
MCP -> RetrievalApplicationService -> KnowledgeSearchBackend
```

P0 exposes:

- `search` for backend-neutral technical retrieval;
- `get_source` for bounded, repository-scoped source access.

Install the optional serving dependency and run over stdio:

```bash
python -m pip install -e ".[mcp]"
fetchcore-mcp
```

See `docs/serving/mcp.md` for environment configuration, Solr hybrid setup and
MCP Inspector usage.
