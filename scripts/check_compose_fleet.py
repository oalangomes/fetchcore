"""Validate the exactly-three supported local FetchCore Compose environments.

Static contract: no Docker daemon or operator credentials required. The full
merged Compose model is additionally validated by `make compose-config`.
"""
from __future__ import annotations

from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]

ENVIRONMENTS = {
    "compose.yml": ("fetchcore", {"solr": 28984, "mcp": 38766, "lab": 38767}),
    "compose.dev.yml": ("fetchcore-dev", {"solr": 18983, "mcp": 18766, "lab": 18767}),
    "compose.benchmark.yml": (
        "fetchcore-benchmark", {"solr": 48983, "mcp": 48766, "lab": 48767},
    ),
}
SERVICES = {"solr", "api", "mcp", "lab"}
STACK_FILE = "infra/compose/fetchcore-stack.yml"


def verify() -> None:
    base = yaml.safe_load((ROOT / STACK_FILE).read_text(encoding="utf-8"))
    assert set(base["services"]) == SERVICES
    assert base["services"]["solr"]["environment"]["FETCHCORE_SOLR_CREATE_COLLECTION"] == "false"
    assert "solrcloud-data:/var/solr" in base["services"]["solr"]["volumes"]
    assert base["x-python-service"]["environment"]["FETCHCORE_SOLR_URL"] == (
        "http://solr:8983/solr"
    )
    assert not any("ports" in base["services"][name] for name in SERVICES)

    seen_ports: set[str] = set()
    seen_names: set[str] = set()
    for filename, (project, expected_ports) in ENVIRONMENTS.items():
        cfg = yaml.safe_load((ROOT / filename).read_text(encoding="utf-8"))
        assert cfg["name"] == project, filename
        seen_names.add(project)
        assert set(cfg["services"]) == SERVICES, filename
        assert "networks" not in cfg, filename
        for service, spec in cfg["services"].items():
            assert spec["extends"] == {"file": STACK_FILE, "service": service}, (
                filename, service,
            )
            if service in expected_ports:
                port = expected_ports[service]
                assert len(spec["ports"]) == 1
                exposed = spec["ports"][0]
                assert exposed.startswith(f"127.0.0.1:{port}:")
                assert exposed not in seen_ports
                seen_ports.add(exposed)
            else:
                assert "ports" not in spec, (filename, service)
    assert seen_names == {"fetchcore", "fetchcore-dev", "fetchcore-benchmark"}

    prod = yaml.safe_load((ROOT / "compose.yml").read_text(encoding="utf-8"))
    dev = yaml.safe_load((ROOT / "compose.dev.yml").read_text(encoding="utf-8"))
    bench = yaml.safe_load((ROOT / "compose.benchmark.yml").read_text(encoding="utf-8"))
    assert prod["volumes"]["solrcloud-data"] == {
        "external": True, "name": "fetchcore-operational-cloud_solrcloud-data",
    }
    assert dev["volumes"]["solrcloud-data"] == {
        "external": True, "name": "fetchcore-shared_solrcloud-data",
    }
    assert bench["volumes"]["solrcloud-data"] == {}
    for service in ("api", "mcp"):
        text = "\n".join(prod["services"][service]["volumes"])
        assert "FETCHCORE_OPERATIONAL_SOURCE_PATH:?" in text
        assert "FETCHCORE_OPERATIONAL_RUNTIME_CONFIG:?" in text
        assert "operational-mcp.yml:ro" in text


if __name__ == "__main__":
    verify()
    print("Three FetchCore Compose environments validated.")
