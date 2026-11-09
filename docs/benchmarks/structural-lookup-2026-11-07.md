# Structural lookup acquisition check (2026-11-07)

Run `PYTHONPATH=. .venv/bin/python scripts/bench/structural_lookup.py` from the
repository root. The script uses 1,000 and 10,000 synthetic canonical chunks,
ten repeated samples per operation, the operational OpenSearch adapter's real
projection/reader code, and an in-memory keyword postings transport. It compares
the previous `load_documents` plus `corpus_checksum` path with exact keyword
candidate acquisition. Snapshot reads, network latency and server execution are
excluded. This is a client cost check, not a live OpenSearch latency benchmark.

| Scale | Operation | Path | p50/p95 ms | Backend reads | Documents transferred | Navigation candidates | Peak traced MiB |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: |
| 1,000 | find_symbol | Previous | 397.5 / 473.4 | 6 | 1,000 | 1,000 | 5.13 |
| 1,000 | find_symbol | Targeted | 2.0 / 6.3 | 2 | 2 | 1 | 0.05 |
| 1,000 | get_related | Previous | 385.6 / 405.0 | 6 | 1,000 | 1,000 | 5.06 |
| 1,000 | get_related | Targeted | 1.0 / 4.3 | 1 | 1 | 1 | 0.03 |
| 10,000 | find_symbol | Previous | 4,694.8 / 4,774.3 | 51 | 10,000 | 10,000 | 50.09 |
| 10,000 | find_symbol | Targeted | 1.8 / 36.3 | 2 | 2 | 1 | 0.05 |
| 10,000 | get_related | Previous | 4,643.1 / 5,273.0 | 51 | 10,000 | 10,000 | 49.97 |
| 10,000 | get_related | Targeted | 0.9 / 40.4 | 1 | 1 | 1 | 0.03 |

The projection build cost was 70.7 ms for 1,000 and 869.6 ms for 10,000 chunks;
building the simulated keyword postings took 1.0 and 87.9 ms respectively. A
single document projection update took 0.20 and 0.19 ms. These measurements do
not include backend persistence or real incremental transaction cost. The p95
values are exploratory maxima from ten samples under `tracemalloc`; they do not
establish production latency targets. Operational contract tests separately
verify that Solr, OpenSearch and Qdrant adapters issue indexed exact filters,
hydrate bounded candidates and avoid `load_documents` during navigation.
