# FetchCore documentation

This directory is the canonical navigation point for FetchCore technical documentation.

The root README remains the product entry point. Deeper material belongs here so architecture, usage, benchmarks and historical extraction provenance do not compete for the same page.

## Current product documentation

### Architecture

- [Module boundaries](architecture/module-boundaries.md)
- [Hardcode audit](architecture/hardcode-audit.md)
- [Issue #44 equivalence report](architecture/issue-44-equivalence.md)

Architecture documents describe current boundaries and invariants. Equivalence reports are evidence for specific refactors, not a replacement for the current architecture description.

### Guides

- [Getting started](guides/getting-started.md)
- [Self-hosted repository indexing and dogfood](guides/self-hosted-indexing.md)
- [MCP serving](serving/mcp.md)

Guides are task-oriented and should keep commands copyable.

### Benchmarks

- [Baseline v1 protocol](benchmark/baseline-v1.md)
- [Baseline v1 results](benchmark/baseline-v1-results.md)
- [Solr latency investigation](benchmark/solr-latency-investigation.md)

Benchmark documentation must preserve corpus/revision, controlled variables, configuration identity, metrics and regressions.

### Migrations

- [FetchCore namespace migration](migrations/fetchcore-namespace.md)

Migration documents remain current only while their compatibility path is operationally relevant.

## Historical extraction provenance

The documents under [`docs/extraction/`](extraction/) describe how proven functionality was extracted from AgentsOrchNext into FetchCore.

They are historical provenance. They must not be interpreted as the current product architecture when a current architecture or reference document exists.

See [the extraction history notice](extraction/README.md).

## Documentation rules

Use one canonical home per concept:

- architecture: boundaries, dependency direction and invariants;
- guides: executable workflows for users/contributors;
- reference: precise public contracts/configuration when introduced;
- benchmark: reproducible experiment protocol/results;
- migrations: active compatibility/migration guidance;
- extraction/history: provenance of the initial split.

Prefer lowercase kebab-case filenames, one H1 per document, relative internal links and FetchCore-native terminology.

Do not create empty taxonomy directories merely to make the tree look complete.
