# FetchCore Agent Instructions

These instructions apply to the entire FetchCore repository unless a more specific `AGENTS.md` exists in a subdirectory.

FetchCore is infrastructure for acquiring, structuring, indexing and retrieving technical knowledge from software repositories.

Its purpose is to transform repository content into **searchable, typed, traceable and backend-neutral technical evidence** for humans, coding agents, MCP clients and other tools.

FetchCore is **not** an agent framework.

---

# 1. Core architectural rule

The central rule of this project is:

```text
retrieval != satisfaction != authority
```

FetchCore retrieves evidence and candidates.

It does not decide:

- whether a consumer has enough context to complete a task;
- whether an answer is correct;
- whether a policy or document is authoritative;
- whether an artifact should be approved;
- whether execution should proceed;
- whether an agent has completed its work.

Backend scores, vector similarity, RRF scores, reranker scores, graph centrality and other retrieval signals are **observations**, not semantic truth or authority.

Never promote retrieval confidence into consumer authority.

---

# 2. Product boundary

FetchCore owns capabilities such as:

- repository discovery and materialization;
- safe scanning and corpus admission;
- canonical Repository Knowledge;
- typed source-derived evidence;
- provenance, revision, checksum and currentness;
- repository knowledge extraction;
- KnowledgePacks and typed relationships;
- incremental and multi-repository indexing;
- backend-neutral retrieval contracts;
- lexical/BM25 retrieval;
- dense and hybrid retrieval;
- embeddings;
- fusion such as RRF;
- bounded reranking;
- rebuildable search/vector/graph projections;
- structural code intelligence through provider-neutral contracts;
- CLI, SDK, API and MCP serving boundaries;
- retrieval/indexing telemetry.

FetchCore does **not** own:

- agent lifecycle;
- work planning;
- role-specific workflows;
- ContextNeed / ContextView or equivalent consumer satisfaction models;
- executor orchestration;
- artifact approval;
- canonical work state;
- autonomous task completion semantics;
- consumer-specific authority rules.

If a requested change requires these concepts, challenge whether it belongs in FetchCore before implementing it.

---

# 3. Canonical knowledge is the source of truth

The intended architecture is:

```text
Repository Sources
        ↓
Registry / Collections
        ↓
RepositoryIndexer
        ↓
Canonical Repository Knowledge
        ↓
KnowledgePacks / typed relationships
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

Search indexes, vector stores and graph stores are **rebuildable projections**.

They are not the canonical source of repository knowledge.

Do not design canonical domain behavior around the storage model of Solr, OpenSearch, Qdrant, a graph database or any future backend.

A backend may disappear and be replaced without changing the meaning of FetchCore contracts.

---

# 4. Backend neutrality is non-negotiable

Core contracts must remain independent from backend vendors.

Vendor-specific concepts belong inside adapters.

Examples of things that must not leak into canonical contracts:

- Solr field/query syntax;
- OpenSearch DSL;
- Qdrant collection/vector payload details;
- Cypher;
- Neo4j-specific identifiers;
- backend-specific scoring structures;
- provider-specific request/response DTOs.

Prefer:

```text
SearchIntent
SearchScope
SearchRequirement
SearchResult
SearchRelationship
EvidenceObservation
QueryMatchEvidence
RetrievalConfig
RetrievalBackendCapabilities
KnowledgeSearchBackend
```

over backend-native structures.

Adapters translate canonical FetchCore concepts into vendor-specific operations.

The dependency direction is:

```text
core contracts
      ↓
application/retrieval logic
      ↓
backend interface
      ↓
vendor adapter
```

Never reverse this dependency.

---

# 5. Do not design to the lowest common denominator

Backend neutrality does not mean every backend must support every capability.

Optional capabilities must be expressed through explicit capability negotiation.

For example, a backend may support:

```text
lexical
dense
hybrid
filters
incremental_updates
structured_lookup
```

while another supports only a subset.

Unsupported capabilities must be explicit.

Prefer:

```text
capability unavailable
```

over silently approximating behavior.

Do not weaken the core contract merely because one backend cannot represent a capability.

At the same time, do not place vendor-specific features into the canonical contract unless they represent a real FetchCore concept independent of that vendor.

---

# 6. No silent semantic loss

Translation from canonical retrieval intent into backend queries must preserve meaning as far as the backend allows.

When a backend cannot represent part of the intent, FetchCore must use an explicit policy such as:

```text
reject
explicitly broaden
explicitly degrade
```

Never silently:

- drop a required anchor;
- flatten alternatives into unrelated terms;
- convert a conjunction into a loose OR;
- upgrade a hint into a requirement;
- remove repository/scope constraints;
- substitute one retrieval mode for another after failure.

Any deliberate broadening must be observable through diagnostics or translation metadata.

---

# 7. Query semantics and execution policy are separate

Keep these concerns distinct:

```text
SearchIntent
= what the user means to retrieve

SearchScope
= where FetchCore may look

RetrievalConfig / policy
= how FetchCore performs retrieval
```

Do not turn `SearchIntent` into a generic configuration bag.

Canonical query meaning must remain distinguishable from:

- required anchors;
- alternatives;
- conjunctions;
- expansions;
- hints;
- structured signals;
- scope;
- candidate budgets;
- backend policy.

Expansions may increase recall but must not mutate canonical query identity.

Hints must never become implicit requirements.

---

# 8. Retrieval channels have different jobs

Treat retrieval as multiple possible acquisition paths.

## Targeted / structured

Use for strong identifiers such as:

- exact paths;
- filenames;
- qualified names;
- symbols;
- routes;
- explicit references;
- canonical structured identifiers.

Prefer this path when it can solve the request precisely.

Do not pay embedding/dense retrieval cost automatically when an exact structured path is available.

## Lexical / BM25

Use for textual and structured lexical relevance.

This remains an important baseline and should not be treated as a fallback for "AI retrieval".

## Dense

Use for semantic discovery where lexical or targeted retrieval is insufficient.

Dense similarity is a retrieval signal, not truth.

## Hybrid

The preferred baseline architecture is:

```text
lexical || dense
       ↓
      RRF
       ↓
optional bounded reranking
```

Lexical and dense acquisition should run independently/parallel where practical.

RRF is the baseline fusion strategy unless a controlled benchmark demonstrates a better alternative.

---

# 9. Reranking must remain bounded

Reranking operates on a limited candidate window.

Never rerank an unbounded corpus.

Any reranking change must specify:

- candidate window;
- latency/cost;
- baseline;
- measured quality change;
- regressions.

A reranker score does not replace provenance or match evidence.

---

# 10. Repository Knowledge includes more than source code

FetchCore is a repository knowledge system, not only a code search engine.

Repository knowledge may include:

- source code;
- tests;
- Markdown/documentation;
- ADRs;
- API contracts;
- schemas;
- configuration;
- workflows;
- infrastructure-as-code;
- policy-as-code;
- governance-as-code;
- repository-local technical standards.

These source kinds may contribute evidence through the same canonical Repository Knowledge model.

Do not introduce consumer-specific modes such as:

```text
GovernanceMode
ArchitectureAgentMode
DocumentationSearchMode
```

unless a genuine FetchCore domain concept independently justifies them.

A governance repository is simply another repository containing technical evidence.

Its contents do not automatically become authoritative because of repository name or source kind.

---

# 11. Preserve provenance everywhere

Evidence should preserve as much identity as applicable:

- repository;
- revision;
- path / source reference;
- source span;
- symbol identity;
- checksum;
- serializer/projection fingerprint;
- provider;
- relationship path;
- currentness;
- retrieval/match observations.

Never fabricate missing provenance.

If provenance is partial, represent it as partial.

Do not discard provenance during:

- fusion;
- deduplication;
- reranking;
- graph expansion;
- federated search;
- packaging.

When the same evidence is discovered through multiple channels, deduplicate the content while preserving the acquisition paths that found it.

---

# 12. Multi-repository retrieval is federation, not concatenation

Multi-repository support must preserve repository identity end-to-end.

Preferred conceptual flow:

```text
query
  ↓
repository routing
  ↓
bounded retrieval per repository
  ↓
per-repository ranking
  ↓
federated fusion
  ↓
global candidates
```

Do not implement multi-repository retrieval as:

```text
search every repository with the same top_k
→ concatenate everything
```

unless it is explicitly a benchmark baseline.

Repository routing and broadening must be observable.

A repository must not gain semantic authority merely because it contains documentation, policy or governance content.

---

# 13. Graph is optional

Graph capabilities are useful for:

- symbol relationships;
- imports/calls;
- implementations/inheritance;
- tests;
- bounded neighbor expansion;
- impact evidence;
- architecture exploration.

Graph must remain an optional projection.

Do not make graph traversal a mandatory hop for ordinary retrieval.

Preferred graph-aware flow:

```text
strong retrieval seed
        ↓
bounded structural expansion
        ↓
additional evidence
```

not:

```text
every query
   ↓
graph traversal
```

Graph expansion is acquisition, not automatic relevance.

Do not rank a graph neighbor above a strong direct match solely because it has more connections.

---

# 14. Structural intelligence must be provider-neutral

Structural code intelligence should be exposed through FetchCore-owned contracts.

External tools may act as providers.

The core must not depend on their schema or vocabulary.

Prefer small operations such as:

```text
find_symbol
get_related
```

with typed relationships and explicit capability negotiation.

Do not mirror every operation exposed by an external structural tool.

A native parser/indexer/runtime should only be introduced after measured evidence shows that external providers cannot satisfy FetchCore requirements economically or correctly.

"Having our own graph" is not sufficient justification.

---

# 15. Indexing must be incremental whenever safe

Avoid reprocessing unchanged repository knowledge.

Preferred direction:

```text
git diff
  ↓
changed files
  ↓
changed semantic regions/chunks
  ↓
projection diff
  ↓
changed embeddings only
  ↓
backend bulk update/delete
```

Use checksums and fingerprints to determine safe reuse.

Do not re-embed unchanged content.

Embedding cache identity must include every input that can change the vector, such as:

```text
content/query
+ serializer/projection identity
+ provider
+ model
+ dimensions
+ embedding-affecting configuration
```

On incompatible fingerprints, fail closed or rebuild explicitly.

Never silently reuse incompatible vectors.

---

# 16. Persisted identities are contracts

File moves, package reorganizations and renames must not accidentally change persisted semantics.

Treat these carefully:

- serializer IDs;
- projection fingerprints;
- schema versions;
- persisted index state;
- embedding identities;
- public import paths;
- MCP/CLI entry points.

Branding or package cleanup alone is not sufficient reason to mutate serialized identity.

When an identity must change:

1. make the change explicit;
2. version it where necessary;
3. add compatibility/migration tests;
4. document rebuild requirements.

---

# 17. Application and transport boundaries

MCP is a transport.

CLI is a transport.

HTTP, if introduced, is a transport.

They must not own retrieval semantics.

Preferred boundary:

```text
MCP / CLI / HTTP
       ↓
application service
       ↓
FetchCore contracts
       ↓
retrieval/backend abstractions
```

A transport adapter must not call Solr, OpenSearch, Qdrant or another backend directly.

Do not duplicate retrieval policy inside MCP tools.

---

# 18. Fail explicitly

FetchCore should prefer fail-closed behavior where correctness or reproducibility is at risk.

Do not silently recover from:

- incompatible schema;
- incompatible fingerprint;
- scorer errors;
- backend protocol errors;
- vector dimension mismatch;
- corrupt persisted state;
- unsupported semantic translation.

Fallbacks are allowed only when they are:

- intentional;
- typed/configured;
- observable;
- tested.

"Something returned results" is not sufficient correctness.

---

# 19. Performance is part of the product

Evaluate retrieval changes using relevant measurements such as:

- Recall@K;
- Ground Truth recovered;
- MRR/nDCG when appropriate;
- useful evidence density;
- p50/p95/p99;
- time-to-first-candidate;
- lexical latency;
- embedding latency;
- dense latency;
- fusion latency;
- reranking latency;
- candidates examined;
- chars/tokens returned;
- indexing throughput;
- incremental indexing latency;
- embedding throughput;
- embedding cache hit rate;
- backend writes/deletes;
- memory;
- cost.

The objective is:

```text
more useful evidence
per unit of
cost, time and context
```

Do not optimize one metric while hiding regressions in another.

---

# 20. Retrieval improvements require evidence

Do not promote significant retrieval changes based only on intuition.

Changes involving:

- BM25 weights/boosts;
- dense retrieval;
- embeddings;
- hybrid retrieval;
- fusion;
- RRF alternatives;
- reranking;
- query expansion;
- graph expansion;
- chunking;
- indexing;
- top-k;
- thresholds;
- candidate budgets;

should define, where practical:

```text
Hypothesis
Controlled variable
Corpus
Repository revision
Ground Truth
Baseline
Metrics
Result
Regressions
Conclusion
```

Prefer one experimental variable at a time.

Use external regression tooling such as retrievalgate where appropriate.

FetchCore owns retrieval behavior and deterministic metrics; it does not need to own the external regression harness.

---

# 21. Keep adapters thin

Backend adapters should primarily translate between:

```text
FetchCore contracts
↕
backend protocol/schema
```

Avoid placing product decisions inside backend adapters.

If multiple backends implement the same rule independently, that rule probably belongs above the adapters.

If a rule exists only because one vendor requires it, it probably belongs inside that vendor adapter.

Before adding a new abstraction, ask whether it represents:

1. a real FetchCore concept; or
2. an implementation workaround for one backend.

Do not generalize vendor quirks into the domain model.

---

# 22. Prefer small typed contracts

Prefer:

- explicit dataclasses/types;
- enums for stable semantic vocabularies;
- narrow interfaces;
- immutable/value-style objects where practical;
- deterministic serialization;
- explicit optionality;
- explicit capability metadata.

Avoid:

- large generic dictionaries;
- loosely typed `metadata` as the primary contract;
- "god" request objects;
- flags that combine unrelated concerns;
- hidden behavior based on backend type checks.

Backend-specific metadata may exist under an explicit extension field, but core semantics must not depend on undocumented metadata keys.

---

# 23. Dependency direction matters

The intended dependency direction is broadly:

```text
domain/contracts
      ↓
application/retrieval logic
      ↓
ports/interfaces
      ↓
adapters/infrastructure
      ↓
vendor libraries
```

Repository extraction may produce canonical knowledge, but retrieval backends must not redefine canonical knowledge.

Serving adapters depend inward.

Core/domain packages do not depend on MCP, CLI, Solr, OpenSearch, Qdrant or other infrastructure modules.

Avoid circular dependencies.

If a cycle appears, reconsider responsibility ownership before introducing import hacks.

---

# 24. Keep the architecture small

Do not introduce architecture ceremony without a demonstrated need.

Avoid adding:

- managers;
- coordinators;
- factories;
- registries;
- service layers;
- abstractions;
- interfaces;

merely because a pattern suggests them.

Introduce an abstraction when it provides a concrete benefit such as:

- multiple implementations;
- stable contract boundary;
- independent testing;
- optional capability;
- clear ownership;
- isolation from infrastructure.

Prefer fewer strong concepts over many weak layers.

---

# 25. Preserve proven behavior during refactors

Structural refactors must not become retrieval rewrites.

When moving/splitting modules:

- preserve behavior;
- preserve contracts;
- preserve persisted identities;
- preserve benchmark baselines;
- add compatibility imports/shims when genuinely required;
- validate equivalence before additional feature work.

Do not combine:

```text
large package reorganization
+
retrieval algorithm change
+
contract redesign
```

in the same change unless unavoidable and explicitly justified.

---

# 26. Tests follow architectural boundaries

Add tests at the boundary where behavior belongs.

Prefer coverage for:

- contract serialization/round trips;
- compatibility;
- capability negotiation;
- backend translation;
- fallback/failure semantics;
- provenance preservation;
- deterministic ranking/fusion;
- fingerprint invalidation;
- incremental equivalence;
- multi-repository identity;
- bounded traversal/budgets.

Integration tests should prove adapters comply with the same semantic contracts.

Do not make core tests require optional external services unless explicitly marked as integration tests.

---

# 27. Documentation is part of the contract

When changing a stable architectural boundary, public contract, configuration model, benchmark protocol or serialized identity, update the corresponding documentation.

Use documentation categories intentionally:

- architecture — boundaries and invariants;
- concepts — domain vocabulary;
- guides — task-oriented usage;
- reference — precise contracts/configuration;
- ADRs — meaningful long-lived architectural decisions;
- benchmarks/experiments — reproducible evidence;
- migrations/history — historical or compatibility material.

Do not duplicate the same canonical explanation across many documents.

Historical extraction documents are evidence of provenance, not current product architecture.

---

# 28. Current work source of truth

Do not infer current priorities from old plans, extraction documents or stale comments.

For current work order, consult:

1. current GitHub issue;
2. milestone;
3. issue dependencies;
4. current code/tests;
5. current benchmark artifacts;
6. current architecture documentation.

Roadmap information does not belong in this file unless it represents a durable architectural invariant.

---

# 29. Before implementing a feature

Before writing code, answer:

```text
Does this belong to FetchCore?
```

Then:

```text
Does it improve at least one of:
- recall
- precision
- latency
- cost
- usability
- reproducibility
- traceability
```

Then:

```text
Can it remain backend-neutral?
```

Then:

```text
Can its behavior be measured?
```

Then:

```text
Does it preserve provenance?
```

Then:

```text
Can it be tested against the existing baseline?
```

If the answer to several of these is "no", reconsider the feature before implementing it.

---

# 30. Before adding or changing a backend

Verify:

- the backend solves a demonstrated need;
- an existing adapter cannot already satisfy it;
- canonical contracts remain unchanged unless a real domain capability is missing;
- capability negotiation describes backend differences;
- no backend-specific type escapes the adapter;
- schema/index/vector differences are isolated;
- failure semantics are explicit;
- compatibility/fingerprint rules are defined;
- benchmark comparison is possible.

Do not add backends merely to increase backend count.

---

# 31. Before changing a public contract

Identify:

- current consumers;
- serialization format;
- persisted uses;
- round-trip behavior;
- backend translators;
- compatibility impact.

Contract evolution must be explicit.

Add a new field/type/version when semantics truly change.

Do not overload an existing field with a new meaning for convenience.

---

# 32. Before completing a change

At minimum verify:

```text
architecture boundary preserved
backend neutrality preserved
provenance preserved
no silent semantic degradation
tests pass
compatibility considered
documentation updated where necessary
benchmark impact evaluated where relevant
```

If retrieval behavior changed, record what changed and how it compares to the baseline.

If retrieval behavior was not intended to change, demonstrate equivalence through tests/benchmark checks appropriate to the change.

---

# 33. Anti-patterns

Do not introduce:

```python
if backend == "solr":
    core_semantics = ...
```

Do not make a backend response the canonical domain model.

Do not silently drop unsupported query semantics.

Do not make dense retrieval mandatory for every query.

Do not make graph traversal mandatory for every query.

Do not infer consumer satisfaction from retrieval scores.

Do not treat governance/document evidence as automatic authority.

Do not rebuild/re-embed unchanged content without reason.

Do not add a second abstraction before the first one has a concrete second implementation/use case.

Do not rewrite proven code merely because a different design looks cleaner.

Do not tune retrieval for one corpus and describe it as a general improvement.

Do not hide failures behind fallbacks that make tests green.

---

# 34. Preferred engineering style

Prefer:

- deterministic behavior;
- explicit contracts;
- small typed boundaries;
- thin adapters;
- immutable/reproducible artifacts;
- capability negotiation;
- provenance-first design;
- bounded operations;
- incremental computation;
- observable broadening/fallback;
- fail-closed compatibility;
- measurable changes;
- small reviewable commits.

Avoid:

- hidden heuristics;
- vendor leakage;
- broad mutable global state;
- implicit fallback;
- unbounded traversal/reranking;
- consumer-specific behavior in core;
- premature distributed architecture;
- architecture-by-fashion.

---

# 35. Final invariant

When uncertain, preserve this ordering:

```text
canonical repository knowledge
        ↓
typed evidence + provenance
        ↓
backend-neutral retrieval semantics
        ↓
replaceable projections/adapters
        ↓
consumers decide meaning and authority
```

FetchCore should remain a **small, strong, measurable and reusable repository knowledge and retrieval layer**.

The project succeeds when consumers can change retrieval technology underneath a stable contract without changing what repository evidence means.
