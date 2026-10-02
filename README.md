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


## Quick start

For contributor setup:

```bash
python -m pip install -e ".[dev]"
make check
```

Run the MCP stdio server with the local backend:

```bash
export FETCHCORE_MCP_WORKSPACE="$PWD"
export FETCHCORE_MCP_REPOSITORY="owner/repository"
export FETCHCORE_MCP_BACKEND="local-semantic"

fetchcore-mcp
```

FetchCore also ships a reproducible container contract without bundling any
retrieval backend:

```bash
make docker-build
make docker-smoke
```

Optional Solr, OpenSearch and Qdrant development services are available through
explicit Docker Compose profiles. See [Getting started](docs/guides/getting-started.md)
for the complete Python, container and Compose workflows.

The documentation index is available at [docs/README.md](docs/README.md).

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


## Retrieval benchmark

FetchCore keeps retrieval quality measurable through the evidence-aware
`baseline-v1` benchmark. The protocol freezes the corpus, Ground Truth, metric
semantics and backend/mode matrix independently from any consumer or agent.

Benchmark **protocol version** and **execution version** are separate concepts:

- `fetchcore.baseline-v1` identifies the frozen comparison contract;
- every invocation of `benchmarks/baseline-v1/run.py` automatically reserves the
  next run version: `V0.1`, `V0.2`, `V0.3`, and so on;
- artifacts are written under
  `benchmarks/baseline-v1/artifacts/V0.N/`;
- a successful execution writes `run.json` with the run identity, selected arms,
  FetchCore revision and corpus identity, plus a generated `README.md` report
  with retrieval and optional indexing metrics;
- the run version is provenance only and is deliberately excluded from retrieval
  configuration fingerprints.

This keeps repeated measurements traceable without silently changing the
scientific baseline contract. See `benchmarks/baseline-v1/README.md` and
`docs/benchmark/baseline-v1.md`.

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
