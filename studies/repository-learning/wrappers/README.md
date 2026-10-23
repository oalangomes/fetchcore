# Codex benchmark wrapper

`codex_harness.py` adapts a fresh non-interactive Codex CLI invocation to the
Repository Learning Benchmark process contract.

Modes:

- `direct`: read-only repository exploration with audited listing/search/read commands;
- `fetchcore`: FetchCore MCP `search` and `get_source`, with the Codex shell disabled;
- `fetchcore-bounded`: the same MCP surface, failing closed above the scenario budgets;
- `evaluate`: no shell, plugins, MCP, browser, or repository workspace.

Both FetchCore arms use `fetchcore_mcp.py`, a benchmark-only adapter over the
canonical `RetrievalApplicationService`. It preserves ranked evidence and core
provenance fields while omitting large diagnostic/observation envelopes. The
bounded mode additionally stops admitting evidence when either scenario budget
is exhausted; it does not query Solr directly or change FetchCore ranking.

The wrapper parses Codex JSONL events and replaces model-reported acquisition
metrics with observed tool events and token usage. The bounded character budget
uses FetchCore's deterministic `telemetry.returned_chars` for search evidence
and source content length for `get_source`; diagnostic/observation envelope
overhead is reflected in Codex input tokens instead of being misclassified as
repository evidence. The budget is checked after each completed Codex process
because Codex CLI does not expose a per-tool callback to an external wrapper; a
run above budget fails closed.
