# FetchCore v0.0.1 — initial technical preview

The first FetchCore release establishes a standalone, backend-neutral repository knowledge and retrieval baseline. It consolidates work merged **before the first tag**; it is not a reconstruction of earlier releases.

## Included capabilities

- **Canonical repository knowledge and provenance:** repository identity, revision, paths, typed evidence and traceable source references. Extracted in [#2](https://github.com/oalangomes/fetchcore/pull/2) and made standalone in [#20](https://github.com/oalangomes/fetchcore/pull/20).
- **Backend-neutral retrieval contracts and projection:** typed search contracts, Projection v3 and ranking primitives ([#4](https://github.com/oalangomes/fetchcore/pull/4), [#6](https://github.com/oalangomes/fetchcore/pull/6)); retrieval adapters for local semantic, Solr, OpenSearch and Qdrant vector ([#9](https://github.com/oalangomes/fetchcore/pull/9)).
- **Explicit query semantics:** SearchIntent v2, required anchors, observable translation and fail-closed handling of unsupported semantics ([#83](https://github.com/oalangomes/fetchcore/pull/83)); targeted path/source_ref retrieval for supported backends ([#85](https://github.com/oalangomes/fetchcore/pull/85)).
- **Incremental indexing and repository operations:** operational incremental indexing ([#10](https://github.com/oalangomes/fetchcore/pull/10)), canonical repository registration and inspection ([#87](https://github.com/oalangomes/fetchcore/pull/87)), and repository-targeted indexing lifecycle ([#91](https://github.com/oalangomes/fetchcore/pull/91)).
- **Headless serving and developer workflow:** MCP search/source access ([#14](https://github.com/oalangomes/fetchcore/pull/14)), reproducible developer runtime ([#59](https://github.com/oalangomes/fetchcore/pull/59)), and self-dogfood indexing/MCP evaluation ([#63](https://github.com/oalangomes/fetchcore/pull/63)).
- **Measurable baseline:** retrieval telemetry and frozen benchmark contracts ([#46](https://github.com/oalangomes/fetchcore/pull/46), [#47](https://github.com/oalangomes/fetchcore/pull/47), [#82](https://github.com/oalangomes/fetchcore/pull/82)).

## Architectural boundaries

FetchCore retrieves technical **evidence and candidates**. It does not decide whether a downstream task is satisfied or which evidence has execution authority: **retrieval ≠ satisfaction ≠ authority**. Repository knowledge is canonical and backend-neutral; lexical, vector and other stores are replaceable projections. Graph and agent orchestration are not required for this release.

## Scope and limitations

- This is an initial technical preview, not a stability or performance superiority claim.
- Operational indexing through the CLI currently targets Solr; support depends on the selected backend's advertised capabilities.
- Federated multi-repository search and graph expansion are not part of this baseline.
- Qdrant exact path/source_ref parity remains a follow-up ([#86](https://github.com/oalangomes/fetchcore/issues/86)).
- CI, dogfood and the release gate must be validated against the final release commit before publication.

The release automation appends GitHub-generated changes for the **entire pre-tag history**, including PRs merged after this overview was written. No historical tags are backfilled.
