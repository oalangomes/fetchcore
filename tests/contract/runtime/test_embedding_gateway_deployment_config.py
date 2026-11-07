"""Operator-owned embedding gateway config invariants (without live provider keys)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[3]
GATEWAYS = ROOT / "infra" / "embedding-gateways"
UPSTREAM = "text-embedding-3-small"
LITELLM_ALIAS = "fc-openai-small-1536-v1"


@pytest.mark.parametrize(("filename", "expected_deployments"), [
    ("litellm.yaml", 1),
    ("litellm-pool.yaml", 2),
])
def test_litellm_immutable_model_pool_and_bounded_limits(
    filename: str, expected_deployments: int,
) -> None:
    config = yaml.safe_load((GATEWAYS / filename).read_text(encoding="utf-8"))
    deployments = config["model_list"]
    assert len(deployments) == expected_deployments
    assert {d["model_name"] for d in deployments} == {LITELLM_ALIAS}
    assert {d["litellm_params"]["model"] for d in deployments} == {"openai/" + UPSTREAM}
    assert {d["model_info"]["mode"] for d in deployments} == {"embedding"}
    assert {d["litellm_params"]["api_key"] for d in deployments} == {
        "os.environ/OPENAI_API_KEY",
        *(
            ["os.environ/OPENAI_API_KEY_SECONDARY"]
            if expected_deployments > 1 else []
        ),
    }
    for deployment in deployments:
        limits = deployment["litellm_params"]
        assert 0 < limits["max_parallel_requests"] <= 8
        assert 0 < limits["rpm"] <= 60
        assert 0 < limits["tpm"] <= 60000
        assert 0 <= limits["num_retries"] <= 2
        assert 0 < limits["timeout"] <= 60

    router = config["router_settings"]
    assert router["routing_strategy"] == "least-busy"
    assert router["fallbacks"] == []
    assert router["context_window_fallbacks"] == []
    assert router["default_max_parallel_requests"] <= 8
    assert router["num_retries"] <= 2
    assert config["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"
    assert config["general_settings"]["disable_fallbacks_on_per_model_rate_limits"]


@pytest.mark.parametrize(("filename", "expected_keys"), [
    ("bifrost.json", 1),
    ("bifrost-pool.json", 2),
])
def test_bifrost_fixed_model_keys_bounded_queue_and_privacy(
    filename: str, expected_keys: int,
) -> None:
    config = json.loads((GATEWAYS / filename).read_text(encoding="utf-8"))
    assert config["config_store"]["enabled"] is False
    client = config["client"]
    assert client["drop_excess_requests"] is False
    assert client["enable_logging"] is False
    assert client["disable_content_logging"] is True
    assert client["allow_per_request_content_storage_override"] is False

    assert set(config["providers"]) == {"openai"}
    provider = config["providers"]["openai"]
    keys = provider["keys"]
    assert len(keys) == expected_keys
    assert {tuple(k["models"]) for k in keys} == {(UPSTREAM,)}
    assert {k["value"] for k in keys} == {
        "env.OPENAI_API_KEY",
        *(["env.OPENAI_API_KEY_SECONDARY"] if expected_keys > 1 else []),
    }
    assert len({k["name"] for k in keys}) == expected_keys
    assert all(k["weight"] > 0 for k in keys)

    capacity = provider["concurrency_and_buffer_size"]
    assert 0 < capacity["concurrency"] <= 8
    assert 0 < capacity["buffer_size"] <= 64
    network = provider["network_config"]
    assert 0 <= network["max_retries"] <= 2
    assert 0 < network["retry_backoff_initial"] <= network["retry_backoff_max"]
    assert 0 < network["default_request_timeout_in_seconds"] <= 60


def test_operator_profiles_match_fixed_gateway_model_and_dimensions() -> None:
    litellm = yaml.safe_load(
        (GATEWAYS / "fetchcore-litellm.yaml").read_text(encoding="utf-8")
    )
    bifrost = yaml.safe_load(
        (GATEWAYS / "fetchcore-bifrost.yaml").read_text(encoding="utf-8")
    )
    for config, provider, model in (
        (litellm, "litellm", LITELLM_ALIAS),
        (bifrost, "bifrost", "openai/" + UPSTREAM),
    ):
        embedding = config["retrieval"]["dense"]["embedding"]
        assert embedding["provider"] == provider
        assert embedding["model"] == model
        assert embedding["dimensions"] == 1536
        assert config["backends"]["qdrant-vector"]["vector_dimensions"] == 1536
        assert config["embedding"][provider]["allow_source_upload"] is True


def test_compose_exposes_only_loopback_and_pins_gateway_versions() -> None:
    compose = yaml.safe_load(
        (GATEWAYS / "docker-compose.yml").read_text(encoding="utf-8")
    )
    litellm = compose["services"]["litellm"]
    bifrost = compose["services"]["bifrost"]
    assert ":1.108.2" in litellm["image"]
    assert ":v2.3.2" in bifrost["image"]
    assert litellm["ports"] == ["127.0.0.1:4000:4000"]
    assert bifrost["ports"] == ["127.0.0.1:8080:8080"]
    assert any("LITELLM_CONFIG_FILE" in v for v in litellm["volumes"])
    assert any("BIFROST_CONFIG_FILE" in v for v in bifrost["volumes"])
