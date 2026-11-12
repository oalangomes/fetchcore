"""Local routing and destructive-operation boundaries without Docker dependencies."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("shared_infra", ROOT / "scripts/shared_infra.py")
assert spec and spec.loader
infra = importlib.util.module_from_spec(spec)
spec.loader.exec_module(infra)


def test_routing_isolates_logical_and_physical_resources():
    dev = infra.route("dev", "oalangomes/fetchcore")
    bench = infra.route("bench", "oalangomes/fetchcore")
    operational = infra.route("operational", "oalangomes/fetchcore")
    dedicated = infra.route("bench", "oalangomes/fetchcore", dedicated=True)
    assert dev["FETCHCORE_SOLR_URL"] == bench["FETCHCORE_SOLR_URL"]
    assert dev["FETCHCORE_SOLR_COLLECTION"] != bench["FETCHCORE_SOLR_COLLECTION"]
    assert len({c["FETCHCORE_INFRA_PROJECT"] for c in (dev, operational, dedicated)}) == 3
    assert len({c["FETCHCORE_SOLR_URL"] for c in (dev, operational, dedicated)}) == 3
    assert all(c["FETCHCORE_OLLAMA_URL"] == "http://host.docker.internal:11434"
               for c in (dev, bench, operational, dedicated))
    assert dev["FETCHCORE_SOLR_DEPLOYMENT_MODE"] == "solrcloud"
    assert bench["FETCHCORE_SOLR_DEPLOYMENT_MODE"] == "solrcloud"
    assert operational["FETCHCORE_SOLR_DEPLOYMENT_MODE"] == "standalone"
    assert operational["FETCHCORE_SOLR_CONFIGSET"] == "fetchcore-projection-v3"
    assert json.loads(dev["FETCHCORE_INDEX_BACKEND_SETTINGS"])["projection_version"] == "3"
    assert infra.route("dev", "another/fetchcore")["FETCHCORE_SOLR_COLLECTION"] != dev[
        "FETCHCORE_SOLR_COLLECTION"
    ]
    mixed_case = infra.route("bench", "oalangomes/NeuroTrack_MS")
    assert mixed_case["FETCHCORE_REPOSITORY"] == "oalangomes/NeuroTrack_MS"
    assert mixed_case["FETCHCORE_SOLR_COLLECTION"].startswith("fc-bench-neurotrack_ms-")
    assert mixed_case["FETCHCORE_SOLR_COLLECTION"] != infra.route(
        "bench", "oalangomes/neurotrack_ms"
    )["FETCHCORE_SOLR_COLLECTION"]


@pytest.mark.parametrize(("backend", "expected"), [
    ("solr", "solr"),
    ("opensearch", "opensearch"),
    ("qdrant", "qdrant-vector"),
])
def test_backend_selection_targets_retrieval_and_indexing(backend, expected):
    config = infra.route("bench", "oalangomes/fetchcore", backend)
    assert config["FETCHCORE_BACKEND"] == expected
    assert config["FETCHCORE_INDEX_BACKEND"] == expected
    assert config["FETCHCORE_OPENSEARCH_INDEX"] == config["FETCHCORE_QDRANT_COLLECTION"]


@pytest.mark.parametrize("repository", ["../bad", "a/b\nx", "a/b/c"])
def test_invalid_repository(repository):
    with pytest.raises(ValueError):
        infra.route("dev", repository)


def invoke(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["shared_infra.py", *args])
    infra.main()


@pytest.mark.parametrize("backend", ["opensearch", "qdrant"])
def test_operational_rejects_alternate_backends(backend):
    with pytest.raises(ValueError, match="OPERATIONAL supports Solr only"):
        infra.route("operational", "oalangomes/fetchcore", backend)


def test_operational_alternate_backend_fails_before_running_command(monkeypatch):
    run = Mock(side_effect=AssertionError("no command should execute"))
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(ValueError, match="OPERATIONAL supports Solr only"):
        invoke(monkeypatch, "run", "--environment", "operational",
               "--repository", "oalangomes/fetchcore", "--backend", "qdrant",
               "--", "fetchcore", "index")
    run.assert_not_called()


def test_env_has_no_side_effects(monkeypatch, capsys):
    run = Mock(side_effect=AssertionError("must not provision"))
    monkeypatch.setattr(infra.subprocess, "run", run)
    invoke(monkeypatch, "env", "--environment", "dev", "--repository", "a/b")
    assert json.loads(capsys.readouterr().out)["FETCHCORE_ENVIRONMENT"] == "dev"
    run.assert_not_called()


def test_run_only_executes_requested_command(monkeypatch):
    run = Mock()
    monkeypatch.setattr(infra.subprocess, "run", run)
    invoke(monkeypatch, "run", "--environment", "bench", "--repository", "a/b",
           "--", "fetchcore", "readiness")
    assert run.call_count == 1
    assert run.call_args.args[0] == ["fetchcore", "readiness"]
    assert run.call_args.kwargs["env"]["FETCHCORE_SOLR_COLLECTION"].startswith("fc-bench-")


def test_run_passes_explicit_host_ollama_url_to_requested_command(monkeypatch):
    run = Mock()
    monkeypatch.setattr(infra.subprocess, "run", run)
    endpoint = "http://wsl-gateway:11434"
    invoke(
        monkeypatch,
        "run",
        "--environment",
        "bench",
        "--repository",
        "oalangomes/fetchcore",
        "--backend",
        "qdrant",
        "--ollama-url",
        endpoint,
        "--",
        "fetchcore",
        "index",
    )
    assert run.call_args.args[0] == ["fetchcore", "index"]
    assert run.call_args.kwargs["env"]["FETCHCORE_OLLAMA_URL"] == endpoint


def test_app_uses_existing_network_and_same_resource_without_starting_backend(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, ""))
    ready = Mock(return_value={"status": "ready"})
    monkeypatch.setattr(infra.subprocess, "run", run)
    monkeypatch.setattr(infra, "wait_ready", ready)
    invoke(monkeypatch, "app", "--environment", "dev", "--repository", "a/b")
    command = run.call_args.args[0]
    assert command[-4:] == ["--no-deps", "api", "mcp", "web"]
    assert command[command.index("-p") + 1] == "fetchcore-lab"
    assert "shared-app.yml" in " ".join(command)
    app_env = run.call_args.kwargs["env"]
    assert app_env["FETCHCORE_INFRA_PROJECT"] == "fetchcore-shared"
    assert app_env["FETCHCORE_CONTAINER_SOLR_URL"] == "http://solr:8983/solr"
    assert app_env["FETCHCORE_SOLR_COLLECTION"].startswith("fc-dev-")
    assert json.loads(app_env["FETCHCORE_CONTAINER_INDEX_BACKEND_SETTINGS"])["collection"] == app_env[
        "FETCHCORE_SOLR_COLLECTION"
    ]
    bench = json.loads(app_env["FETCHCORE_EXPLORER_TARGETS"])[0]
    assert bench["id"] == "bench"
    assert bench["environment"] == "bench"
    assert bench["repository"] == app_env["FETCHCORE_REPOSITORY"]
    assert bench["backend_settings"]["collection"] != app_env["FETCHCORE_SOLR_COLLECTION"]
    assert bench["backend_settings"]["url"] == app_env["FETCHCORE_CONTAINER_SOLR_URL"]
    ready.assert_called_once()


def test_one_lab_can_bind_another_bench_repository_and_backend(monkeypatch, tmp_path):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, ""))
    ready = Mock(return_value={"status": "ready"})
    monkeypatch.setattr(infra.subprocess, "run", run)
    monkeypatch.setattr(infra, "wait_ready", ready)
    invoke(monkeypatch, "app", "--environment", "dev", "--repository", "a/dev",
           "--bench-repository", "b/Bench", "--bench-backend", "opensearch",
           "--bench-source-path", str(tmp_path))
    app_env = run.call_args.kwargs["env"]
    bench = json.loads(app_env["FETCHCORE_EXPLORER_TARGETS"])[0]
    assert bench["repository"] == "b/Bench"
    assert bench["backend"] == "opensearch"
    assert bench["backend_settings"]["index"].startswith("fc-bench-bench-")
    assert app_env["FETCHCORE_BENCH_SOURCE_PATH"] == str(tmp_path)
    assert ready.call_count == 2


@pytest.mark.parametrize("environment", ["bench", "operational"])
def test_app_refuses_per_environment_duplicate_projects(monkeypatch, environment):
    run = Mock(side_effect=AssertionError("must not start duplicate Lab"))
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, "app", "--environment", environment, "--repository", "a/b")
    run.assert_not_called()


def test_external_app_requires_container_reachable_endpoint(monkeypatch, tmp_path):
    path = tmp_path / "external.json"
    path.write_text(json.dumps({"solr": "http://127.0.0.1:8983/solr"}))
    run = Mock(side_effect=AssertionError("must not start app"))
    monkeypatch.setattr(infra.subprocess, "run", run)
    monkeypatch.setattr(infra, "wait_ready", Mock(return_value={"status": "ready"}))
    with pytest.raises(ValueError, match="container-url"):
        invoke(monkeypatch, "app", "--environment", "dev", "--repository", "a/b",
               "--shared-endpoints", str(path))
    run.assert_not_called()


def test_external_shared_app_rejects_isolated_container_port():
    config = infra.route("bench", "a/b")
    with pytest.raises(ValueError, match="isolated-service port"):
        infra.container_routing(
            config, selected_backend="solr", external={"solr"},
            container_url="http://operational-host:38983/solr",
        )


@pytest.mark.parametrize("environment", ["dev", "operational", "bench"])
def test_cleanup_refuses_shared_or_persistent_instances(monkeypatch, environment):
    run = Mock()
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, "cleanup", "--environment", environment, "--repository", "a/b")
    run.assert_not_called()


def test_performance_refuses_shared_services(monkeypatch):
    with pytest.raises(SystemExit):
        invoke(monkeypatch, "env", "--environment", "bench", "--repository", "a/b",
               "--performance", "--exclusive-host")


def test_cleanup_checks_all_ownership_before_deleting(monkeypatch):
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, "abc\ndef\n"),
        subprocess.CompletedProcess([], 0, json.dumps([{"Config": {"Labels": {
            "io.fetchcore.owner": infra.OWNER,
            "com.docker.compose.project": "fetchcore-bench-dedicated",
        }}}])),
        subprocess.CompletedProcess([], 0, json.dumps([{"Config": {"Labels": {}}}])),
    ])
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(ValueError, match="unowned"):
        invoke(monkeypatch, "cleanup", "--environment", "bench", "--repository", "a/b",
               "--dedicated", "--confirm-project", "fetchcore-bench-dedicated")
    assert all(call.args[0][1] != "rm" for call in run.call_args_list)


def test_compose_has_no_implicit_resource_or_model_creation():
    compose = yaml.safe_load((ROOT / "infra/compose/shared.yml").read_text())
    assert compose["services"]["solr"]["command"] == ["sh", "/fetchcore-start-shared.sh"]
    assert compose["volumes"]["solrcloud-data"]["labels"]["io.fetchcore.owner"]
    ollama = yaml.safe_load((ROOT / "infra/compose/ollama.yml").read_text())
    assert "command" not in ollama["services"]["ollama"]
    assert "shared.yml" not in (ROOT / "compose.yml").read_text()


def test_app_projects_keep_api_dns_private_and_reuse_external_infra_network():
    overlay = yaml.safe_load((ROOT / "infra/compose/shared-app.yml").read_text())
    assert overlay["services"]["web"]["networks"] == ["default"]
    assert overlay["services"]["api"]["networks"] == ["default", "infra"]
    assert overlay["services"]["mcp"]["networks"] == ["default", "infra"]
    assert overlay["networks"]["infra"]["external"] is True


def test_up_rejects_unowned_persistent_volume(monkeypatch):
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, ""),
        subprocess.CompletedProcess([], 0, "fetchcore-operational_solr-data\n"),
        subprocess.CompletedProcess([], 0, json.dumps([{"Labels": {}}])),
    ])
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(ValueError, match="unowned volume"):
        invoke(monkeypatch, "up", "--environment", "operational", "--repository", "a/b")
    assert all("up" not in call.args[0] for call in run.call_args_list)


def test_cleanup_owned_containers_retains_volumes(monkeypatch):
    run = Mock(side_effect=[
        subprocess.CompletedProcess([], 0, "abc\n"),
        subprocess.CompletedProcess([], 0, json.dumps([{"Config": {"Labels": {
            "io.fetchcore.owner": infra.OWNER,
            "com.docker.compose.project": "fetchcore-bench-dedicated",
        }}}])),
        subprocess.CompletedProcess([], 0),
    ])
    monkeypatch.setattr(infra.subprocess, "run", run)
    invoke(monkeypatch, "cleanup", "--environment", "bench", "--repository", "a/b",
           "--dedicated", "--confirm-project", "fetchcore-bench-dedicated")
    assert run.call_args.args[0] == ["docker", "rm", "-f", "abc"]


def test_shared_endpoint_reuse_preserves_resource_and_isolation(tmp_path):
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps({"solr": "http://localhost:8983/solr/",
                                "ollama": "http://host.docker.internal:11434"}))
    for environment in ("dev", "bench"):
        config = infra.route(environment, "a/b")
        resource = config["FETCHCORE_SOLR_COLLECTION"]
        assert infra.shared_endpoints(config, path) == {"solr", "ollama"}
        assert config["FETCHCORE_SOLR_URL"] == "http://localhost:8983/solr"
        assert json.loads(config["FETCHCORE_INDEX_BACKEND_SETTINGS"])["url"] == config[
            "FETCHCORE_SOLR_URL"
        ]
        assert config["FETCHCORE_SOLR_COLLECTION"] == resource
    for environment, dedicated in (("operational", False), ("bench", True)):
        config = infra.route(environment, "a/b", dedicated=dedicated)
        before = config.copy()
        assert infra.shared_endpoints(config, path) == set()
        assert config == before


@pytest.mark.parametrize("endpoints", [
    [], {"unknown": "http://localhost"}, {"solr": 3},
    {"solr": "http://localhost:8983"}, {"solr": "http://user:pass@localhost/solr"},
    {"solr": "http://localhost:38983/solr"}, {"qdrant": "http://alias:26333"},
    {"solr": "http://localhost/solr?x=y"},
])
def test_invalid_shared_endpoints_fail_closed(tmp_path, endpoints):
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps(endpoints))
    with pytest.raises(ValueError):
        infra.shared_endpoints(infra.route("dev", "a/b"), path)


def test_up_never_adopts_external_service(monkeypatch, tmp_path):
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps({"solr": "http://localhost:8983/solr"}))
    run = Mock()
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, "up", "--environment", "dev", "--repository", "a/b",
               "--shared-endpoints", str(path))
    run.assert_not_called()


def test_up_never_recreates_existing_containers(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, ""))
    monkeypatch.setattr(infra.subprocess, "run", run)
    monkeypatch.setattr(infra, "wait_ready", Mock(return_value={"status": "ready"}))
    invoke(monkeypatch, "up", "--environment", "operational", "--repository", "a/b")
    assert run.call_args.args[0][-4:] == ["up", "-d", "--no-recreate", "solr"]


def test_up_starts_selected_backend_and_waits_for_readiness(monkeypatch):
    run = Mock(return_value=subprocess.CompletedProcess([], 0, ""))
    ready = Mock(return_value={"backend": "qdrant", "status": "ready"})
    monkeypatch.setattr(infra.subprocess, "run", run)
    monkeypatch.setattr(infra, "wait_ready", ready)
    invoke(monkeypatch, "up", "--environment", "bench", "--repository", "a/b",
           "--backend", "qdrant")
    assert run.call_args.args[0][-4:] == ["up", "-d", "--no-recreate", "qdrant"]
    ready.assert_called_once()


def test_ollama_cannot_be_started_by_shared_infra(monkeypatch):
    run = Mock()
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, "up", "--environment", "operational", "--repository", "a/b",
               "--backend", "ollama")
    run.assert_not_called()


def test_qdrant_prepare_requires_explicit_vector_dimensions(monkeypatch):
    run = Mock()
    monkeypatch.setattr(infra.subprocess, "run", run)
    with pytest.raises(SystemExit):
        invoke(monkeypatch, "prepare", "--environment", "bench", "--repository", "a/b",
               "--backend", "qdrant", "--create-if-missing")
    run.assert_not_called()


def _solr_schema_payload():
    schema_file = ROOT / "infra/solr/configset/conf/schema.xml"
    root = ET.parse(schema_file).getroot()

    def value(item):
        return {"true": True, "false": False}.get(item, item)

    schema = {
        "name": root.get("name"),
        "version": root.get("version"),
        "uniqueKey": root.findtext("uniqueKey"),
        "fields": [
            {key: value(item) for key, item in field.attrib.items()}
            for field in root.findall("field")
        ],
        "fieldTypes": [],
        "dynamicFields": [
            {key: value(item) for key, item in field.attrib.items()}
            for field in root.findall("dynamicField")
        ],
        "copyFields": [dict(field.attrib) for field in root.findall("copyField")],
    }
    for field_type in root.findall("fieldType"):
        observed = {
            key: value(item) for key, item in field_type.attrib.items()
        }
        for analyzer in field_type.findall("analyzer"):
            analyzer_value = {}
            tokenizer = analyzer.find("tokenizer")
            if tokenizer is not None:
                analyzer_value["tokenizer"] = dict(tokenizer.attrib)
            for tag, key in (("filter", "filters"), ("charFilter", "charFilters")):
                entries = [dict(element.attrib) for element in analyzer.findall(tag)]
                if entries:
                    analyzer_value[key] = entries
            observed[f"{analyzer.get('type')}Analyzer"] = analyzer_value
        schema["fieldTypes"].append(observed)
    similarity = root.find("similarity")
    assert similarity is not None
    schema["similarity"] = {"class": similarity.get("class")}
    schema["similarity"].update({
        element.get("name"): (element.text or "").strip()
        for element in similarity
    })
    return {"schema": schema}


def _ready_responses(mode, *, collection_name=None, collection_config=None, schema=None):
    def response(url):
        if url.endswith("/admin/info/system?wt=json"):
            return {"responseHeader": {"status": 0}, "mode": mode}
        if "action=CLUSTERSTATUS" in url:
            collections = {}
            if collection_config is not None:
                collections[collection_name] = {"configName": collection_config}
            return {
                "responseHeader": {"status": 0},
                "cluster": {
                    "live_nodes": ["127.0.0.1:8983_solr"],
                    "collections": collections,
                },
            }
        if url.endswith("/schema?wt=json"):
            return schema or _solr_schema_payload()
        raise AssertionError(f"Unexpected readiness endpoint: {url}")

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            return None

        def __init__(self, value):
            self.value = value

        def read(self):
            return json.dumps(self.value).encode()

    requests = []

    def open_url(request, timeout):
        del timeout
        url = request.full_url
        requests.append(url)
        return Response(response(url))

    return open_url, requests


def test_readiness_accepts_operational_solr_standalone(monkeypatch):
    open_url, requests = _ready_responses("std")
    monkeypatch.setattr(infra, "urlopen", open_url)
    config = infra.route("operational", "a/b")
    result = infra.wait_ready("solr", config)
    assert result["status"] == "ready"
    assert result["endpoint"] == "http://127.0.0.1:38983/solr/admin/info/system?wt=json"
    assert config["FETCHCORE_SOLR_DEPLOYMENT_MODE"] == "standalone"
    assert requests == [result["endpoint"]]


@pytest.mark.parametrize("environment", ["dev", "bench"])
def test_readiness_accepts_dev_bench_solrcloud(monkeypatch, environment):
    open_url, requests = _ready_responses("solrcloud")
    monkeypatch.setattr(infra, "urlopen", open_url)
    config = infra.route(environment, "a/b")
    result = infra.wait_ready("solr", config)
    assert result["status"] == "ready"
    assert config["FETCHCORE_SOLR_DEPLOYMENT_MODE"] == "solrcloud"
    assert len(requests) == 2
    assert requests[0].endswith("/admin/info/system?wt=json")
    assert "action=CLUSTERSTATUS" in requests[1]


@pytest.mark.parametrize(
    ("environment", "observed_mode"),
    [("operational", "solrcloud"), ("dev", "std"), ("bench", "std")],
)
def test_readiness_rejects_incompatible_solr_deployment_mode(
    monkeypatch, environment, observed_mode,
):
    open_url, requests = _ready_responses(observed_mode)
    monkeypatch.setattr(infra, "urlopen", open_url)
    with pytest.raises(infra.SolrDeploymentModeError, match="mode mismatch"):
        infra.wait_ready("solr", infra.route(environment, "a/b"), timeout=0.01)
    assert len(requests) == 1


def test_readiness_rejects_existing_cloud_collection_with_other_configset(monkeypatch):
    config = infra.route("dev", "a/b")
    open_url, requests = _ready_responses(
        "solrcloud",
        collection_name=config["FETCHCORE_SOLR_COLLECTION"],
        collection_config="different-configset",
    )
    monkeypatch.setattr(infra, "urlopen", open_url)
    with pytest.raises(infra.SolrConfigurationError, match="configset"):
        infra.wait_ready("solr", config)
    assert not any(url.endswith("/schema?wt=json") for url in requests)


def test_readiness_compares_full_existing_cloud_schema(monkeypatch):
    config = infra.route("dev", "a/b")
    schema = _solr_schema_payload()
    schema["schema"]["fields"] = [
        {**field, "stored": False} if field["name"] == "canonical_payload" else field
        for field in schema["schema"]["fields"]
    ]
    open_url, requests = _ready_responses(
        "solrcloud",
        collection_name=config["FETCHCORE_SOLR_COLLECTION"],
        collection_config=infra.SOLR_CONFIGSET,
        schema=schema,
    )
    monkeypatch.setattr(infra, "urlopen", open_url)
    with pytest.raises(infra.SolrConfigurationError, match="fields definition"):
        infra.wait_ready("solr", config)
    assert any(url.endswith("/schema?wt=json") for url in requests)


def test_readiness_checks_existing_cloud_collection_schema(monkeypatch):
    config = infra.route("bench", "a/b")
    open_url, requests = _ready_responses(
        "solrcloud",
        collection_name=config["FETCHCORE_SOLR_COLLECTION"],
        collection_config=infra.SOLR_CONFIGSET,
    )
    monkeypatch.setattr(infra, "urlopen", open_url)
    result = infra.wait_ready("solr", config)
    assert result["status"] == "ready"
    assert any(url.endswith("/schema?wt=json") for url in requests)

@pytest.mark.parametrize("environment", ["dev", "bench"])
@pytest.mark.parametrize("backend", ["solr", "opensearch", "qdrant"])
def test_indexing_never_routes_to_another_backend(tmp_path, environment, backend):
    config = infra.route(environment, "a/b", backend)
    path = tmp_path / "endpoints.json"
    path.write_text(json.dumps({"solr": "http://localhost:8983/solr",
                                "opensearch": "http://localhost:9200",
                                "qdrant": "http://localhost:6333"}))
    infra.shared_endpoints(config, path)
    settings = json.loads(config["FETCHCORE_INDEX_BACKEND_SETTINGS"])
    assert config["FETCHCORE_INDEX_BACKEND"] == config["FETCHCORE_BACKEND"]
    assert settings["url"] == config[f"FETCHCORE_{backend.upper()}_URL"]
    field = "index" if backend == "opensearch" else "collection"
    assert settings[field] == config[f"FETCHCORE_{backend.upper()}_{field.upper()}"]


@pytest.mark.parametrize("backend", ["qdrant"])
def test_routed_qdrant_requires_vector_dimensions_before_backend_access(monkeypatch, backend):
    from click.testing import CliRunner
    from fetchcore.cli import main
    from fetchcore.application import indexing

    config = infra.route("bench", "a/b", backend)
    build_solr = Mock(side_effect=AssertionError("must never compose a Solr writer"))
    monkeypatch.setattr(indexing, "SolrIndexStore", build_solr)
    result = CliRunner().invoke(
        main, ["index", "--repository", "a/b", "sync"], env=config,
    )
    assert result.exit_code == 1, result.output
    assert "schema_mismatch" in result.output
    assert "requires explicit vector_dimensions" in result.output
    build_solr.assert_not_called()


def test_qdrant_readiness_does_not_accept_liveness(monkeypatch):
    from urllib.error import HTTPError

    opened = Mock(side_effect=HTTPError("http://localhost/readyz", 503, "not ready", {}, None))
    monkeypatch.setattr(infra, "urlopen", opened)
    with pytest.raises(RuntimeError, match="readiness timed out"):
        infra.wait_ready("qdrant", infra.route("bench", "a/b", "qdrant"), timeout=0.01)
    assert opened.call_args.args[0].full_url.endswith("/readyz")


@pytest.mark.parametrize("distance,modifier", [("Dot", "idf"), ("Cosine", "none")])
def test_qdrant_prepare_rejects_incompatible_vector_schema(monkeypatch, distance, modifier):
    from fetchcore.adapters.retrieval.qdrant import backend

    config = infra.route("bench", "a/b", "qdrant")
    alias = backend._repository_alias(backend.QdrantVectorSettings.from_mapping({
        "collection": config["FETCHCORE_QDRANT_COLLECTION"],
    }), "a/b")
    request = Mock(side_effect=[
        {"result": {"collections": []}},
        {"result": {"aliases": [{"alias_name": alias, "collection_name": alias + "-existing"}]}},
        {"result": {"config": {"params": {
            "vectors": {"size": 768, "distance": distance},
            "sparse_vectors": {"lexical": {"modifier": modifier}},
        }}}},
    ])
    monkeypatch.setattr(backend.UrllibQdrantTransport, "request", request)
    with pytest.raises(ValueError, match="incompatible"):
        infra._qdrant_resource(config, "a/b", True, 768)
    assert all(call.args[0] == "GET" for call in request.call_args_list)


def test_qdrant_prepare_rejects_malformed_inventory_before_creation(monkeypatch):
    from fetchcore.adapters.retrieval.qdrant import backend

    request = Mock(side_effect=[
        {"result": {"collections": []}},
        {"result": {"aliases": []}},
        {"result": {}},
    ])
    monkeypatch.setattr(backend.UrllibQdrantTransport, "request", request)
    with pytest.raises(ValueError, match="invalid collection inventory"):
        infra._qdrant_resource(infra.route("bench", "a/b", "qdrant"), "a/b", True, 768)
    assert all(call.args[0] == "GET" for call in request.call_args_list)
