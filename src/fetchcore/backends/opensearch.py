"""Compatibility exports for :mod:`fetchcore.adapters.retrieval.opensearch.backend`."""

from fetchcore.adapters.retrieval.opensearch.backend import *
from fetchcore.application import composition as _composition

opensearch_embedding_provider = _composition.opensearch_embedding_provider
