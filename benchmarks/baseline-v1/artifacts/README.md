# Baseline artifact status

The JSON retrieval artifacts currently present in this directory were measured on
2026-09-30 using the earlier source-level evaluator.

They are retained as **pre-baseline experimental evidence** only.

They must not be used as the promoted `fetchcore.baseline-v1` regression ruler
because they do not contain the evidence-region result coordinates required by
artifact schema v2:

```text
rank
id
source_ref
line_start
line_end
```

The per-backend indexing artifacts remain useful measurements of that run, but
their presence does not promote the retrieval artifacts.

The next complete run must use:

- Ground Truth schema v2;
- artifact schema v2;
- metrics schema v1;
- the exact frozen NeuroTrack_MS revision;
- all nine declared arms;
- evidence-aware metrics from `fetchcore.baseline`.

After review, the final evidence-aware artifacts replace the source-level
measurements as the promoted baseline-v1 evidence.
