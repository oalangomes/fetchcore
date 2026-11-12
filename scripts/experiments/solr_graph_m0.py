"""Disposable, live Solr 9.10.1 graph spike for issue #30 M0.

Run against an isolated SolrCloud endpoint. No FetchCore index is read or changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any, cast
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fetchcore.knowledge.structure import DeclarationIdentity
from fetchcore.repository.semantic import SemanticRelationEvidence


ADMIN_COLLECTIONS_PATH = "/admin/collections"
REPO_A_A_REF = "repository://repo-a/a.py"
REPO_A_C_REF = "repository://repo-a/c.py"


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
    match = re.fullmatch(r"symbol:v1:sha256:([0-9a-f]{64})", identity)
    if match is None:
        raise ValueError("unsupported canonical symbol identity for Solr traversal")
    # The prefix is parser-safe; validating the full canonical format makes this injective.
    return "symbol_v1_sha256_" + match.group(1)


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

    edge("A", "B", "calls", "repo-a", "r1", "s1", REPO_A_A_REF, 10)
    edge("A", "B", "calls", "repo-a", "r1", "s1", REPO_A_A_REF, 11)
    edge("B", "C", "calls", "repo-a", "r1", "s1", "repository://repo-a/b.py", 20)
    edge("C", "D", "calls", "repo-a", "r1", "s1", REPO_A_C_REF, 30)
    edge("C", "A", "calls", "repo-a", "r1", "s1", REPO_A_C_REF, 31)
    edge("A", "B_homonym", "imports", "repo-a", "r1", "s1", REPO_A_A_REF, 12)
    edge("B_homonym", "D", "imports", "repo-a", "r1", "s1", "repository://repo-a/other/b.py", 4)
    edge("B", "E", "calls", "repo-a", "r2", "s2", "repository://repo-a/b.py", 22)
    edge("A", "B", "calls", "repo-a", "r2", "s2", REPO_A_A_REF, 14)
    edge("C", "E", "calls", "repo-a", "r1", "s2", REPO_A_C_REF, 35)
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
    docs, _ = stream_timed(base, collection, expr)
    return docs


def stream_timed(base: str, collection: str, expr: str) -> tuple[list[dict[str, Any]], int]:
    result = request(base, f"/{collection}/stream", params={"expr": expr})
    docs = cast(list[dict[str, Any]], result["result-set"]["docs"])
    errors = [item for item in docs if "EXCEPTION" in item]
    if errors:
        raise AssertionError(errors)
    eof = next(item for item in docs if item.get("EOF"))
    return [item for item in docs if not item.get("EOF")], int(eof["RESPONSE_TIME"])


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


def exact_edges_for_pairs(
    base: str, collection: str, pairs: list[tuple[str, str]], fq: str,
) -> tuple[list[dict[str, Any]], int]:
    clauses = [f"(source_node_s:{source} AND target_node_s:{target})"
               for source, target in pairs]
    result = request(base, f"/{collection}/select", params={
        "q": " OR ".join(clauses), "fq": fq, "fl": "*", "rows": "2000",
        "sort": "id asc", "wt": "json",
    })
    response = cast(dict[str, Any], result["response"])
    if response["numFound"] > 2_000:
        raise AssertionError("M0 batch hydration result budget exceeded")
    header = cast(dict[str, Any], result["responseHeader"])
    return cast(list[dict[str, Any]], response["docs"]), int(header["QTime"])


def standalone_preflight(base: str, collection: str) -> dict[str, Any]:
    """Record explicit standalone traversal and M0 schema compatibility failures."""
    fq = scope()
    probes = {
        "nodes": nodes_expr(collection, "standalone-probe", 1, fq),
        "shortest_path": (
            f'shortestPath({collection},from="standalone-probe",to="target-probe",'
            f'edge="source_node_s=target_node_s",fq="{fq}",maxDepth="1")'
        ),
    }
    traversal_errors: dict[str, str] = {}
    for name, expr in probes.items():
        response = request(base, f"/{collection}/stream", params={"expr": expr})
        docs = cast(list[dict[str, Any]], response["result-set"]["docs"])
        error = next((str(doc["EXCEPTION"]) for doc in docs if "EXCEPTION" in doc), None)
        if error is None or "zkHost not found" not in error:
            raise AssertionError(f"unexpected standalone {name} result: {docs}")
        traversal_errors[name] = error

    edge = [{
        "id": "m0-standalone-schema-probe", "source_identity_s": "source",
        "target_identity_s": "target", "source_node_s": "source",
        "target_node_s": "target", "relation_type_s": "calls",
        "repository_s": "repo-a", "revision_s": "r1", "snapshot_s": "s1",
        "source_ref_s": "repository://repo-a/source.py",
        "source_checksum_s": "checksum", "line_i": 1,
        "resolution_s": "resolved",
    }]
    try:
        urlopen(Request(
            base.rstrip("/") + f"/{collection}/update?commit=true",
            data=json.dumps(edge).encode(),
            headers={"Content-Type": "application/json"},
        ), timeout=20)
    except HTTPError as exc:
        error_body = exc.read()
        error = cast(dict[str, Any], json.loads(error_body))
        message = str(cast(dict[str, Any], error["error"])["msg"])
        if "unknown field 'source_identity_s'" not in message:
            raise AssertionError(f"unexpected standalone schema result: {message}") from exc
    else:
        raise AssertionError("M0 edge fields unexpectedly accepted by the knowledge schema")
    return {
        "traversal": "unsupported",
        "traversal_errors": traversal_errors,
        "m0_edge_schema": "incompatible",
        "schema_error": "unknown field 'source_identity_s'",
    }


def high_fanout_probe(base: str, *, fanout: int = 512) -> dict[str, Any]:
    collection = "fetchcore_graph_fanout_" + uuid.uuid4().hex[:12]
    root = node(symbol("fanout-repo", "Root"))
    children = [node(symbol("fanout-repo", f"Child{index}")) for index in range(fanout)]
    fq = scope(repository="fanout-repo")
    rows = []
    for index, child in enumerate(children):
        for source, target, suffix in ((root, child, "out"), (child, root, "cycle")):
            identity = (source, target, index, suffix)
            rows.append({
                "id": "edge:v1:" + hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                "source_identity_s": source, "target_identity_s": target,
                "source_node_s": source, "target_node_s": target,
                "relation_type_s": "calls", "repository_s": "fanout-repo",
                "revision_s": "r1", "snapshot_s": "s1",
                "source_ref_s": "repository://fanout-repo/graph.py",
                "source_checksum_s": "fanout-checksum", "line_i": index + 1,
                "resolution_s": "resolved",
            })
    created = False
    try:
        request(base, ADMIN_COLLECTIONS_PATH, params={
            "action": "CREATE", "name": collection, "numShards": "1",
            "replicationFactor": "1", "collection.configName": "_default", "wt": "json",
        })
        created = True
        started = time.perf_counter()
        request(base, f"/{collection}/update", params={"commit": "true"}, body=rows)
        index_ms = (time.perf_counter() - started) * 1_000
        expr = nodes_expr(collection, root, 2, fq)
        timings: list[tuple[float, int]] = []
        found: list[dict[str, Any]] = []
        for _ in range(5):
            started = time.perf_counter()
            found, server_ms = stream_timed(base, collection, expr)
            timings.append(((time.perf_counter() - started) * 1_000, server_ms))
        reached = {item["node"] for item in found}
        if len(reached) != len(found) or reached != {root, *children}:
            raise AssertionError(f"high-fanout traversal mismatch: {len(reached)} nodes")
        path_expr = (
            f'shortestPath({collection},from="{root}",to="{children[0]}",'
            f'edge="source_node_s=target_node_s",fq="{fq}",maxDepth="1",'
            'threads="1",partitionSize="10")'
        )
        paths = stream(base, collection, path_expr)
        if [root, children[0]] not in [path["path"] for path in paths]:
            raise AssertionError(f"bounded shortest path mismatch: {paths}")
        return {
            "fanout": fanout, "edge_documents": len(rows),
            "unique_nodes_at_depth_2": len(reached), "cycle_root_deduplicated": True,
            "shortest_path_max_depth_1": True,
            "index_wall_ms": round(index_ms, 3),
            "nodes_client_wall_ms_median": round(statistics.median(x[0] for x in timings), 3),
            "nodes_server_response_time_ms_median": round(statistics.median(x[1] for x in timings), 3),
            "nodes_client_minus_server_ms_median": round(
                statistics.median(x[0] - x[1] for x in timings), 3,
            ),
            "server_hard_limit_proven": False,
        }
    finally:
        if created:
            request(base, ADMIN_COLLECTIONS_PATH, params={
                "action": "DELETE", "name": collection, "wt": "json",
            })


def run(base: str, *, samples: int = 1) -> dict[str, Any]:
    if not 1 <= samples <= 10:
        raise ValueError("samples must be between 1 and 10")
    collection = "fetchcore_graph_m0_" + uuid.uuid4().hex[:12]
    rows, ids, unresolved = fixture()
    assert unresolved.target_identity is None and unresolved.resolution == "unresolved"
    metrics: dict[str, list[float]] = defaultdict(list)
    created = False
    try:
        request(base, ADMIN_COLLECTIONS_PATH, params={
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
                found, server_ms = stream_timed(base, collection, expr)
                metrics[f"nodes_{depth}_ms"].append((time.perf_counter() - started) * 1000)
                metrics[f"nodes_{depth}_server_ms"].append(server_ms)
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
            paths, server_ms = stream_timed(base, collection, expr)
            metrics["shortest_path_ms"].append((time.perf_counter() - started) * 1000)
            metrics["shortest_path_server_ms"].append(server_ms)
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
        pairs = list(zip(paths[0]["path"], paths[0]["path"][1:]))
        started = time.perf_counter()
        batch_edges, batch_qtime = exact_edges_for_pairs(base, collection, pairs, fq)
        metrics["hydrate_batch_ms"].append((time.perf_counter() - started) * 1000)
        metrics["hydrate_batch_qtime_ms"].append(batch_qtime)
        assert {edge["id"] for edge in batch_edges} == {edge["id"] for edge in hydrated}
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
            "batch_hydrated_edge_documents": len(batch_edges),
            "batch_hydration_http_requests": 1,
            "duplicate_pair_evidence": 2, "scoped_checks": scoped_checks,
            "request_timeout_seconds": 20, "hydration_rows_cap": 20,
            "serialized_edge_payload_bytes": len(json.dumps(rows).encode()),
            "high_fanout": high_fanout_probe(base),
            "timings_ms": {k: {"samples": len(v), "median": round(statistics.median(v), 3)}
                           | {"min": round(min(v), 3), "max": round(max(v), 3)}
                           for k, v in metrics.items()},
        }
    finally:
        if created:
            request(base, ADMIN_COLLECTIONS_PATH, params={
                "action": "DELETE", "name": collection, "wt": "json",
            })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--solr-url", required=True, help="SolrCloud base URL ending in /solr")
    parser.add_argument("--standalone-url", help="Optional standalone base URL ending in /solr")
    parser.add_argument("--standalone-core", help="Precreated FetchCore-schema core to probe")
    parser.add_argument("--samples", type=int, default=5, help="1-10 timed query samples")
    parser.add_argument("--output", type=Path, help="Optional JSON evidence output path")
    args = parser.parse_args()
    if bool(args.standalone_url) != bool(args.standalone_core):
        parser.error("--standalone-url and --standalone-core must be supplied together")
    result = run(args.solr_url, samples=args.samples)
    if args.standalone_url and args.standalone_core:
        result["standalone_preflight"] = standalone_preflight(
            args.standalone_url, args.standalone_core,
        )
    payload = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
