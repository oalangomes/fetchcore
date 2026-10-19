# Typed knowledge retrieval

The versioned [Ground Truth](ground-truth.json) pins a real repository and
revision in **corpus data**. It contains 20 acquisition cases (17 positive,
2 negative, one pending ambiguity), five relation cases, and a separate set of 17 positive ranking
queries. Source hashes and exact locators were checked directly against the
pinned checkout, independently of FetchCore output. Case rationales state why
each source was selected; these cases are not a representative statistical
sample of all repositories.

The acquisition check is part of the existing acceptance gate:

```bash
python tests/acceptance/acquisition/run.py --corpus /path/to/pinned/checkout
```

The source checkout must be clean and match the pinned revision and checksums.
The check is offline and backend free. Ranked retrieval, snapshot consistency,
latency and cost remain separate benchmark gates. The synthetic fixture checks
are behavioral acceptance tests and do not establish retrieval quality.

The API preview ceiling now matches the existing 1 MB scanner file ceiling.
This changes admission for eligible files between 256,001 and 1,000,000
characters; the indexing contract version is 27. Existing persisted typed
snapshots require an explicit rebuild before this corpus can be projected.
