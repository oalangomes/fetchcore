"""Compatibility exports for the former mixed indexing contract module."""

from fetchcore.repository.index_fingerprint import (
    INDEXING_CONTRACT_VERSION,
    indexing_contract_fingerprint,
)
from fetchcore.adapters.retrieval.solr.index_contract import (
    IndexContractSolrIndexStore,
    IndexContractSolrSnapshot,
    bind_indexing_contract_store,
    snapshot_indexing_fingerprint,
    snapshot_mapping,
)

__all__ = [
    "INDEXING_CONTRACT_VERSION",
    "IndexContractSolrIndexStore",
    "IndexContractSolrSnapshot",
    "bind_indexing_contract_store",
    "indexing_contract_fingerprint",
    "snapshot_indexing_fingerprint",
    "snapshot_mapping",
]
