"""Disposable, live Solr 9.10.1 graph spike for issue #30 M0.

Run against an isolated SolrCloud endpoint. No FetchCore index is read or changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
import uuid
from collections import defaultdict
from typing import Any, cast
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fetchcore.knowledge.structure import DeclarationIdentity
from fetchcore.repository.semantic import SemanticRelationEvidence


def request(base: str, path: str, *, params: dict[str, str] | None = None,
            body: object | None = None) -> dict[str, Any]:
    url = base.rstrip("/") + path
    if params:
        url += "?" + urlencode(params)
    data = None if body is None else json.dumps(body).encode()
    req = Request(url, data=data, headers={"Content-Type": "application/json"})
    with urlopen(req, timeout=20) as response:
        result = cast(dict[str, Any], json.load(response))
    if "error" in result:
        raise AssertionError(result["error"])
    return result


def symbol(repository: str, name: str, *, path: str | None = None) -> str:
    return DeclarationIdentity(repository, path or f"{name.lower()}.py", "python",
                               "function", f"demo.{name}").key


def node(identity: str) -> str:
    # shortestPath parses a colon in the canonical key as a Lucene field separator.
    return identity.rsplit(":", 1)[-1]


def fixture() -> tuple[list[dict[str, Any]], dict[str, str], SemanticRelationEvidence]:
    """Resolved observations only; the unresolved source is intentionally absent."""
    ids = {name: symbol("repo-a", name) for name in "ABCDE"}
    ids["B_homonym"] = symbol("repo-a", "B", path="other/b.py")
    ids["A_foreign"] = symbol("repo-b", "A")
    ids["foreign"] = symbol("repo-b", "F")
    ids["unresolved"] = symbol("repo-a", "Missing")
    rows: list[dict[str, Any]] = []

    def edge(source: str, target: str, relation: str, repo: str, revision: str,
             snapshot: str, ref: str, line: int) -> None:
        evidence = (repo, revision, snapshot, source, target, relation, ref, line)
        edge_id = "edge:v1:" + hashlib.sha256(json.dumps(evidence).encode()).hexdigest()
        rows.append({
            "id": edge_id, "source_identity_s": ids[source],
            "target_identity_s": ids[target], "relation_type_s": relation,
            "source_node_s": node(ids[source]), "target_node_s": node(ids[target]),
            "repository_s": repo, "revision_s": revision, "snapshot_s": snapshot,
            "source_ref_s": ref, "source_checksum_s": f"checksum-{revision}-{ref}",
            "line_i": line, "resolution_s": "resolved",
        })

    edge("A", "B", "calls", "repo-a", "r1", "s1", "repository://repo-a/a.py", 10)
    edge("A", "B", "calls", "repo-a", "r1", "s1", "repository://repo-a/a.py", 11)
    edge("B", "C", "calls", "repo-a", "r1", "s1", "repository://repo-a/b.py", 20)
    edge("C", "D", "calls", "repo-a", "r1", "s1", "repository://repo-a/c.py", 30)
    edge("C", "A", "calls", "repo-a", "r1", "s1", "repository://repo-a/c.py", 31)
    edge("A", "B_homonym", "imports", "repo-a", "r1", "s1", "repository://repo-a/a.py", 12)
    edge("B_homonym", "D", "imports", "repo-a", "r1", "s1", "repository://repo-a/other/b.py", 4)
    edge("B", "E", "calls", "repo-a", "r2", "s2", "repository://repo-a/b.py", 22)
    edge("A", "B", "calls", "repo-a", "r2", "s2", "repository://repo-a/a.py", 14)
    edge("C", "E", "calls", "repo-a", "r1", "s2", "repository://repo-a/c.py", 35)
    edge("A_foreign", "foreign", "calls", "repo-b", "r1", "s1", "repository://repo-b/a.py", 8)
    unresolved = SemanticRelationEvidence(
        kind="calls", target="demo.Missing", resolution="unresolved",
        source_identity=ids["D"], line=40,
    )
    return rows, ids, unresolved


def scope(repository: str = "repo-a", revision: str = "r1", snapshot: str = "s1",
          relation: str = "calls") -> str:
    return " AND ".join((f"repository_s:{repository}", f"revision_s:{revision}",
                         f"snapshot_s:{snapshot}", f"relation_type_s:{relation}"))


def nodes_expr(collection: str, root: str, depth: int, fq: str) -> str:
    expr = f'nodes({collection},walk="{root}->source_node_s",gather="target_node_s",fq="{fq}",trackTraversal="true",scatter="branches,leaves")'
    for _ in range(1, depth):
        expr = f'nodes({collection},{expr},walk="node->source_node_s",gather="target_node_s",fq="{fq}",trackTraversal="true",scatter="branches,leaves")'
    return expr


def stream(base: str, collection: str, expr: str) -> list[dict[str, Any]]:
    result = request(base, f"/{collection}/stream", params={"expr": expr})
    docs = cast(list[dict[str, Any]], result["result-set"]["docs"])
    errors = [item for item in docs if "EXCEPTION" in item]
    if errors:
        raise AssertionError(errors)
    return [item for item in docs if not item.get("EOF")]


def exact_edges(base: str, collection: str, source: str, target: str,
                fq: str) -> list[dict[str, Any]]:
    # Exact pair lookup restores every observation discarded by node deduplication.
    result = request(base, f"/{collection}/select", params={
        "q": f"source_node_s:{source} AND target_node_s:{target}",
        "fq": fq, "fl": "*", "rows": "20", "sort": "id asc", "wt": "json",
    })["response"]
    if result["numFound"] > 20:
        raise AssertionError("M0 hydration result budget exceeded")
    return cast(list[dict[str, Any]], result["docs"])


def run(base: str, *, samples: int = 1) -> dict[str, Any]:
    if not 1 <= samples <= 10:
        raise ValueError("samples must be between 1 and 10")
    collection = "fetchcore_graph_m0_" + uuid.uuid4().hex[:12]
    rows, ids, unresolved = fixture()
    assert unresolved.target_identity is None and unresolved.resolution == "unresolved"
    metrics: dict[str, list[float]] = defaultdict(list)
    created = False
    try:
        request(base, "/admin/collections", params={
            "action": "CREATE", "name": collection, "numShards": "1",
            "replicationFactor": "1", "collection.configName": "_default", "wt": "json",
        })
        created = True
        started = time.perf_counter()
        request(base, f"/{collection}/update", params={"commit": "true"}, body=rows)
        index_ms = (time.perf_counter() - started) * 1000
        fq = scope()
        levels: dict[str, list[str]] = {}
        scoped_checks: dict[str, bool] = {}
        for depth in (1, 2, 3):
            expr = nodes_expr(collection, node(ids["A"]), depth, fq)
            for _ in range(samples):
                started = time.perf_counter()
                found = stream(base, collection, expr)
                metrics[f"nodes_{depth}_ms"].append((time.perf_counter() - started) * 1000)
            levels[str(depth)] = sorted({item["node"] for item in found})
            assert len(found) <= 5, found
            assert len(found) == len(levels[str(depth)]), found
            assert any("ancestors" in item for item in found if item.get("level", 0) > 0)
            assert node(ids["foreign"]) not in levels[str(depth)]
            assert node(ids["B_homonym"]) not in levels[str(depth)]
            assert node(ids["E"]) not in levels[str(depth)]
            assert node(ids["unresolved"]) not in levels[str(depth)]
        assert node(ids["B"]) in levels["1"]
        assert node(ids["C"]) in levels["2"]
        assert node(ids["D"]) in levels["3"]
        assert node(ids["D"]) not in levels["2"]
        assert levels["3"] == sorted(node(ids[name]) for name in "ABCD")
        # Every nested nodes() receives the same filter. These fixtures would
        # leak to a later hop if a filter were applied only at the root.
        scoped_cases = {
            "other_revision": (ids["A"], scope(revision="r2", snapshot="s2"),
                               {node(ids["B"]), node(ids["E"])}),
            "other_snapshot": (ids["A"], scope(snapshot="s2"), set()),
            "other_repository": (ids["A_foreign"], scope(repository="repo-b"),
                                 {node(ids["foreign"])}),
            "other_relation": (ids["A"], scope(relation="imports"),
                               {node(ids["B_homonym"]), node(ids["D"])}),
        }
        for label, (case_root, case_fq, expected) in scoped_cases.items():
            found = stream(base, collection, nodes_expr(collection, node(case_root), 3, case_fq))
            actual = {item["node"] for item in found if item.get("level", 0) > 0}
            assert actual == expected, (label, actual, expected)
            scoped_checks[label] = True
        expr = (f'shortestPath({collection},from="{node(ids["A"])}",to="{node(ids["D"])}",'
                f'edge="source_node_s=target_node_s",fq="{fq}",'
                'maxDepth="3",threads="1",partitionSize="10")')
        for _ in range(samples):
            started = time.perf_counter()
            paths = stream(base, collection, expr)
            metrics["shortest_path_ms"].append((time.perf_counter() - started) * 1000)
        assert [node(ids[name]) for name in "ABCD"] in [p["path"] for p in paths], paths
        hydrated = []
        for source, target in zip(paths[0]["path"], paths[0]["path"][1:]):
            started = time.perf_counter()
            edges = exact_edges(base, collection, source, target, fq)
            metrics["hydrate_pair_ms"].append((time.perf_counter() - started) * 1000)
            assert edges and all(e["source_ref_s"] and e["source_checksum_s"] and e["line_i"] for e in edges)
            assert all(node(e["source_identity_s"]) == source and
                       node(e["target_identity_s"]) == target for e in edges)
            hydrated.extend(edges)
        assert len(exact_edges(base, collection, node(ids["A"]), node(ids["B"]), fq)) == 2
        assert len({e["id"] for e in hydrated}) == len(hydrated)
        assert not stream(base, collection, expr.replace('maxDepth="3"', 'maxDepth="2"'))
        assert not stream(base, collection, expr.replace(f'fq="{fq}"',
                                                      f'fq="{scope(snapshot="s2")}"'))
        assert not stream(base, collection, expr.replace(f'fq="{fq}"',
                                                      f'fq="{scope(repository="repo-b")}"'))
        assert not stream(base, collection, expr.replace(f'fq="{fq}"',
                                                      f'fq="{scope(revision="r2", snapshot="s2")}"'))
        version = request(base, "/admin/info/system")["lucene"]["solr-spec-version"]
        return {
            "solr_version": version, "collection_shards": 1, "edge_documents": len(rows),
            "unresolved_observations_excluded": 1, "index_ms": round(index_ms, 3),
            "levels": {k: len(v) for k, v in levels.items()},
            "paths": len(paths), "hydrated_edge_documents": len(hydrated),
            "duplicate_pair_evidence": 2, "scoped_checks": scoped_checks,
            "request_timeout_seconds": 20, "hydration_rows_cap": 20,
            "serialized_edge_payload_bytes": len(json.dumps(rows).encode()),
            "timings_ms": {k: {"samples": len(v), "median": round(statistics.median(v), 3)}
                           | {"min": round(min(v), 3), "max": round(max(v), 3)}
                           for k, v in metrics.items()},
        }
    finally:
        if created:
            request(base, "/admin/collections", params={
                "action": "DELETE", "name": collection, "wt": "json",
            })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solr-url", required=True, help="SolrCloud base URL ending in /solr")
    parser.add_argument("--samples", type=int, default=5, help="1-10 timed query samples")
    args = parser.parse_args()
    print(json.dumps(run(args.solr_url, samples=args.samples), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
