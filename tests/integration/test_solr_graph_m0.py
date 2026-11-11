"""Issue #30 M0 live spike against an explicitly disposable SolrCloud instance."""

from __future__ import annotations

import os

import pytest

from scripts.experiments.solr_graph_m0 import run


@pytest.mark.skipif(
    "FETCHCORE_GRAPH_M0_SOLR_URL" not in os.environ,
    reason="requires an explicitly isolated SolrCloud endpoint",
)
def test_solr_9101_edge_only_graph_spike() -> None:
    result = run(os.environ["FETCHCORE_GRAPH_M0_SOLR_URL"])
    assert result["solr_version"] == "9.10.1"
    assert result["levels"] == {"1": 2, "2": 3, "3": 4}
    assert result["hydrated_edge_documents"] == 4
    assert all(result["scoped_checks"].values())
