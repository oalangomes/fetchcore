"""Native acquisition smoke; run both source trees against one fixed projection.

Use only disposable services. --index creates the fixture projection once; omit
it on the second arm. No tuning, model changes or corpus changes between arms.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from fetchcore.adapters.embedding.ollama.provider import OllamaEmbeddingProvider
from fetchcore.adapters.retrieval.opensearch.backend import (
    OpenSearchBackend, OpenSearchIndexStore, OpenSearchSettings, UrllibOpenSearchTransport,
)
from fetchcore.adapters.retrieval.qdrant.backend import (
    QdrantVectorBackend, QdrantVectorIndexStore, QdrantVectorSettings, UrllibQdrantTransport,
)
from fetchcore.adapters.retrieval.solr.backend import (
    SolrBackend, SolrIndexStore, SolrSettings, PersistentSolrTransport,
)
from fetchcore.repository.indexing import RepositoryIndexConfig, RepositoryIndexer
from fetchcore.retrieval.config import RetrievalConfig
from fetchcore.retrieval.contracts import (
    AllOf, Anchor, KnowledgeSearchError, QueryExpansion, SearchIntent, SearchScope, Term,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ollama-url", required=True)
    parser.add_argument("--solr-url", required=True)
    parser.add_argument("--opensearch-url", required=True)
    parser.add_argument("--qdrant-url", required=True)
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--expect-optional-rejection", action="store_true",
                        help="Assert the pre-correction expansion rejection on the baseline arm.")
    args = parser.parse_args()
    provider = OllamaEmbeddingProvider(url=args.ollama_url, model="embeddinggemma")
    workspace = args.workspace.resolve()
    repository = "fixture/intent-corrective"
    if args.index:
        workspace.mkdir(parents=True, exist_ok=False)
        for name, content in {
            "auth.py": 'def authentication():\n    """authentication flow validates login"""\n',
            "session.py": 'def auth():\n    """auth session checks credentials"""\n',
            "cart.py": 'class ProductService:\n    """checkout implementation"""\n',
        }.items():
            (workspace / name).write_text(content)
    result = RepositoryIndexer(RepositoryIndexConfig(
        repository=repository, revision="fixture-v1", branch="smoke",
    )).index(workspace)
    solr = SolrSettings(url=args.solr_url, collection="corrective", projection_version="3")
    opensearch = OpenSearchSettings(url=args.opensearch_url, index="corrective-smoke")
    qdrant = QdrantVectorSettings(url=args.qdrant_url, collection="corrective-smoke")
    if args.index:
        SolrIndexStore(PersistentSolrTransport(solr), solr, embedding_provider=provider).full_rebuild(result)
        OpenSearchIndexStore(UrllibOpenSearchTransport(opensearch), opensearch,
                            clock=lambda: "fixture-v1", embedding_provider=provider).full_rebuild(result)
        QdrantVectorIndexStore(UrllibQdrantTransport(qdrant), qdrant, provider,
                              clock=lambda: "fixture-v1").full_rebuild(result)
    intents = {
        "canonical": SearchIntent(canonical_query="authentication"),
        "expansion": SearchIntent(canonical_query="authentication",
                                  expansions=(QueryExpansion("auth", "caller"),)),
        "hint": SearchIntent(canonical_query="authentication", hints=("auth",)),
        "anchor": SearchIntent(canonical_query="checkout",
                               anchors=(Anchor("identifier", "ProductService"),)),
        "expression": SearchIntent(canonical_query="authentication",
                                   expression=AllOf((Term("authentication"), Term("login")))),
    }
    observations = []
    for name, backend, settings in (
        ("solr", SolrBackend(embedding_factory=lambda config: provider),
         {"url": solr.url, "collection": solr.collection, "projection_version": "3"}),
        ("opensearch", OpenSearchBackend(embedding_factory=lambda config: provider),
         {"url": opensearch.url, "index": opensearch.index}),
        ("qdrant", QdrantVectorBackend(embedding_factory=lambda settings, config: provider),
         {"url": qdrant.url, "collection": qdrant.collection}),
    ):
        for mode in (("dense", "hybrid") if name == "qdrant" else ("lexical", "dense", "hybrid")):
            retrieval = RetrievalConfig.from_mapping({
                "dense": {"enabled": mode != "lexical"},
                "hybrid": {"enabled": mode == "hybrid"},
            })
            scope = SearchScope(workspace, "fixture-v1", repository, (), 10,
                                settings={**settings, "diagnostic_candidate_window": 10},
                                retrieval=retrieval)
            for label, intent in intents.items():
                observation = {"backend": name, "mode": mode, "intent": label}
                try:
                    response = backend.search(intent, scope, ())
                    translations = response.metadata["intent_translation"]
                    observation.update(status=response.status,
                                       paths=[item.source for item in response.results],
                                       translation=translations)
                    assert response.results, observation
                    assert all(item.source_ref.startswith("repository://" + repository + "/") and item.checksum
                               for item in response.results)
                except KnowledgeSearchError as exc:
                    observation.update(error=exc.category, metadata=exc.metadata)
                rejected = mode != "lexical" and (
                    label in ("anchor", "expression")
                    or (args.expect_optional_rejection and label == "expansion")
                )
                assert observation.get("error") == (
                    "unsupported_semantics" if rejected else None
                ), observation
                observations.append(observation)
    args.output.write_text(json.dumps({
        "model": provider.model_id, "corpus_checksum": hashlib.sha256(
            "".join(doc.checksum for doc in result.documents).encode()
        ).hexdigest(),
        "documents": len(result.documents), "observations": observations,
    }, indent=2) + "\n")
    print(json.dumps({"attempts": len(observations),
                      "succeeded": sum("error" not in x for x in observations)}))


if __name__ == "__main__":
    main()
