# Baseline v1 artifacts

Only promoted retrieval artifacts generated with:

- artifact schema v2;
- Ground Truth schema v2;
- metrics schema v1;
- matching Ground Truth and metrics fingerprints;
- ranked result spans (`source_ref`, `line_start`, `line_end`);

belong directly in this directory.

The earlier source-only retrieval run was intentionally removed from the
promotable tree after the evidence-region protocol was defined. It remains
available in Git history as pre-freeze evidence and must not be compared as a
frozen baseline.

Indexing telemetry under `indexing/` is retained because its measurement
contract is independent from retrieval Ground Truth.
