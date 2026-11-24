"""Canonical FetchCore Docker Compose topology tests (no Docker daemon required)."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "check_compose_fleet", ROOT / "scripts/check_compose_fleet.py",
)
assert spec is not None and spec.loader is not None
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def test_exactly_three_full_compose_projects() -> None:
    checker.verify()


def test_no_duplicate_operational_project_from_compatibility_aliases() -> None:
    for path in (
        ROOT / "infra/compose/operational-mcp.yml",
        ROOT / "tools/explorer/compose.yaml",
    ):
        model = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert model["name"] == "fetchcore"
        assert "services" not in model
        assert any(item.endswith("compose.yml") for item in model["include"])


def test_canonical_core_has_only_internal_solr_url() -> None:
    model = yaml.safe_load(
        (ROOT / "infra/compose/fetchcore-stack.yml").read_text(encoding="utf-8")
    )
    assert model["x-python-service"]["environment"]["FETCHCORE_SOLR_URL"] == (
        "http://solr:8983/solr"
    )
    assert model["services"]["solr"]["environment"]["FETCHCORE_SOLR_CREATE_COLLECTION"] == (
        "false"
    )
    assert not any("ports" in service for service in model["services"].values())
