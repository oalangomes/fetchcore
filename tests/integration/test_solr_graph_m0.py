"""Issue #30 M0 live spike against an explicitly disposable SolrCloud instance."""

from __future__ import annotations

import os

import pytest

from fetchcore.knowledge.structure import DeclarationIdentity
from scripts.experiments.solr_graph_m0 import node, run, standalone_preflight


@pytest.mark.skipif(
    "FETCHCORE_GRAPH_M0_SOLR_URL" not in os.environ,
    reason="requires an explicitly isolated SolrCloud endpoint",
)
def test_solr_9101_edge_only_graph_spike() -> None:
    result = run(os.environ["FETCHCORE_GRAPH_M0_SOLR_URL"])
    assert result["solr_version"] == "9.10.1"
    assert result["levels"] == {"1": 2, "2": 3, "3": 4}
    assert result["hydrated_edge_documents"] == 4
    assert result["batch_hydrated_edge_documents"] == 4
    assert result["batch_hydration_http_requests"] == 1
    assert all(result["scoped_checks"].values())
    assert result["high_fanout"]["fanout"] == 512
    assert result["high_fanout"]["unique_nodes_at_depth_2"] == 513
    assert result["high_fanout"]["cycle_root_deduplicated"]
    assert not result["high_fanout"]["server_hard_limit_proven"]


def test_traversal_key_is_injective_for_canonical_symbol_ids() -> None:
    identities = (
        DeclarationIdentity("repo-a", "a.py", "python", "function", "pkg.run"),
        DeclarationIdentity("repo-a", "b.py", "python", "function", "pkg.run"),
        DeclarationIdentity("repo-b", "a.py", "python", "function", "pkg.run"),
    )
    keys = [node(identity.key) for identity in identities]
    assert len(set(keys)) == len(identities)
    assert all(":" not in key and key.startswith("symbol_v1_sha256_") for key in keys)
    with pytest.raises(ValueError, match="unsupported canonical symbol identity"):
        node("symbol:v2:sha256:" + "a" * 64)


@pytest.mark.skipif(
    "FETCHCORE_GRAPH_STANDALONE_URL" not in os.environ
    or "FETCHCORE_GRAPH_STANDALONE_CORE" not in os.environ,
    reason="requires an explicitly disposable standalone Solr core",
)
def test_solr_standalone_preflight_records_traversal_and_schema_limits() -> None:
    result = standalone_preflight(
        os.environ["FETCHCORE_GRAPH_STANDALONE_URL"],
        os.environ["FETCHCORE_GRAPH_STANDALONE_CORE"],
    )
    assert result["traversal"] == "unsupported"
    assert set(result["traversal_errors"]) == {"nodes", "shortest_path"}
    assert result["m0_edge_schema"] == "incompatible"
