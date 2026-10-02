"""Compatibility exports for :mod:`fetchcore.adapters.retrieval.solr.backend`."""

from fetchcore.adapters.retrieval.solr.backend import *
from fetchcore.application import composition as _composition

solr_embedding_provider = _composition.solr_embedding_provider
