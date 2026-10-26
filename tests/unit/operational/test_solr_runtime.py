from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Mapping, Sequence
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError

import pytest

from fetchcore.operational import solr_runtime
from fetchcore.operational.solr_runtime import (
    DeploymentMode,
    ProvisionMode,
    SolrRuntimeConfig,
    SolrRuntimeError,
    cleanup_solr_runtime,
    prepare_solr_runtime,
)


IMAGE = "solr:9.10.1@sha256:" + ("a" * 64)


class FakeDocker:
    def __init__(self, *, forbidden: bool = False) -> None:
        self.forbidden = forbidden
        self.calls: list[tuple[str, ...]] = []

    def run(self, arguments: Sequence[str]) -> None:
        if self.forbidden:
            raise AssertionError("docker must not be called while reusing Solr")
        self.calls.append(tuple(arguments))


class FakeSolr:
    def __init__(
        self,
        *,
        mode: str = "std",
        resources: set[str] | None = None,
        repositories: set[str] | None = None,
        unbound_documents: int = 0,
        unavailable_calls: int = 0,
    ) -> None:
        self.mode = mode
        self.resources = set(resources or ())
        self.repositories = set(repositories or ())
        self.unbound_documents = unbound_documents
        self.unavailable_calls = unavailable_calls
        self.calls: list[tuple[str, str, Mapping[str, str], Mapping[str, object] | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        payload: Mapping[str, object] | None = None,
        timeout: float,
    ) -> Mapping[str, object]:
        del timeout
        values = dict(params or {})
        self.calls.append((method, url, values, payload))
        if url.endswith("/admin/info/system"):
            if self.unavailable_calls:
                self.unavailable_calls -= 1
                raise SolrRuntimeError("connection_refused", "unavailable")
            return {
                "responseHeader": {"status": 0},
                "mode": self.mode,
                "lucene": {"solr-spec-version": "9.10.1"},
            }
        if url.endswith("/admin/cores"):
            if values["action"] == "CREATE":
                self.resources.add(values["name"])
                return {"responseHeader": {"status": 0}}
            core = values["core"]
            # Solr 9.10 includes the requested missing core as an empty object.
            return {"status": {core: {"name": core} if core in self.resources else {}}}
        if url.endswith("/admin/collections"):
            return {"collections": sorted(self.resources)}
        if url.endswith("/admin/configs"):
            return {"configSets": ["fetchcore"]}
        if url.endswith("/api/collections"):
            assert payload is not None
            self.resources.add(str(payload["name"]))
            return {"responseHeader": {"status": 0}}
        if url.endswith("/select"):
            query = values["q"]
            if query == "*:*":
                count = len(self.repositories) + self.unbound_documents
            elif query.startswith('repository:"') and query.endswith('"'):
                count = int(query[len('repository:"'):-1] in self.repositories)
            else:
                raise AssertionError(f"unexpected binding query: {query}")
            return {"response": {"numFound": count, "numFoundExact": True, "docs": []}}
        raise AssertionError(f"unexpected request: {method} {url} {values}")


def config(tmp_path: Path, **changes: Any) -> SolrRuntimeConfig:
    values: dict[str, object] = {
        "solr_url": "http://127.0.0.1:8983/solr",
        "resource": "fetchcore-self-dogfood",
        "repository": "oalangomes/fetchcore",
        "configset_dir": tmp_path,
        "solr_image": IMAGE,
        "owner_token": "123-1",
        "startup_attempts": 2,
        "startup_interval_seconds": 0,
    }
    values.update(changes)
    return SolrRuntimeConfig(**values)  # type: ignore[arg-type]


def test_healthy_existing_solr_is_reused_without_docker(tmp_path: Path) -> None:
    solr = FakeSolr(
        resources={"fetchcore-self-dogfood"}, repositories={"oalangomes/fetchcore"},
    )
    state = prepare_solr_runtime(config(tmp_path), client=solr, docker=FakeDocker(forbidden=True))
    assert state.instance == "reused"
    assert state.resource_state == "existing"
    assert state.repository_binding == "matching"
    assert state.deployment_mode == "standalone"


def test_non_solr_http_endpoint_fails_without_cleanup_or_provisioning(tmp_path: Path) -> None:
    class NotSolr(FakeSolr):
        def request(self, *args: Any, **kwargs: Any) -> Mapping[str, object]:
            return {"status": "ok"}

    docker = FakeDocker()
    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(config(tmp_path), client=NotSolr(), docker=docker)
    assert failure.value.category == "not_solr"
    assert docker.calls == []


@pytest.mark.parametrize("category", ["connection_refused", "connection_failure", "timeout"])
def test_reuse_only_unavailable_endpoint_never_provisions(tmp_path: Path, category: str) -> None:
    class Unavailable(FakeSolr):
        def request(self, *args: Any, **kwargs: Any) -> Mapping[str, object]:
            raise SolrRuntimeError(category, "unavailable")

    docker = FakeDocker()
    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(config(tmp_path), client=Unavailable(), docker=docker)
    assert failure.value.category == category
    assert "reuse_only" in str(failure.value)
    assert docker.calls == []


def test_provision_if_missing_uses_free_owned_port_and_exact_cleanup(tmp_path: Path) -> None:
    solr = FakeSolr(resources={"fetchcore-self-dogfood"}, unavailable_calls=1)
    docker = FakeDocker()
    state = prepare_solr_runtime(
        config(
            tmp_path,
            provision_mode=ProvisionMode.PROVISION_IF_MISSING,
            create_if_missing=True,
        ),
        client=solr,
        docker=docker,
        choose_port=lambda: 49123,
        sleep=lambda _: None,
    )
    assert state.instance == "provisioned"
    assert state.endpoint == "http://127.0.0.1:49123/solr"
    assert state.container == "fetchcore-solr-123-1"
    run = docker.calls[0]
    assert run[:4] == ("run", "-d", "--name", "fetchcore-solr-123-1")
    assert "127.0.0.1:49123:8983" in run
    assert cleanup_solr_runtime(state.to_mapping(), docker=docker) is True
    assert docker.calls[-1] == ("rm", "-f", "fetchcore-solr-123-1")


def test_owned_provisioning_defaults_to_bundled_configset(tmp_path: Path) -> None:
    source_configset = Path(__file__).parents[3] / "infra" / "solr" / "configset" / "conf"
    bundled = Path(solr_runtime.__file__).parent / "configset" / "conf"
    for filename in ("schema.xml", "solrconfig.xml"):
        assert (bundled / filename).read_bytes() == (source_configset / filename).read_bytes()

    docker = FakeDocker()
    prepare_solr_runtime(
        config(
            tmp_path,
            configset_dir=None,
            provision_mode=ProvisionMode.PROVISION_IF_MISSING,
            create_if_missing=True,
        ),
        client=FakeSolr(resources={"fetchcore-self-dogfood"}, unavailable_calls=1),
        docker=docker,
        choose_port=lambda: 49124,
        sleep=lambda _: None,
    )
    assert f"{bundled.parent}:/fetchcore-configset:ro" in docker.calls[0]


def test_cleanup_never_touches_reused_or_mismatched_resources() -> None:
    docker = FakeDocker()
    assert cleanup_solr_runtime({"instance": "reused"}, docker=docker) is False
    with pytest.raises(SolrRuntimeError, match="identity"):
        cleanup_solr_runtime({
            "instance": "provisioned", "owned_by_run": True,
            "owner_token": "123-1", "container": "external-solr",
        }, docker=docker)
    assert docker.calls == []


def test_exact_owned_cleanup_is_idempotent_when_container_is_already_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args[0], 1, "", "Error response from daemon: No such container: fetchcore-solr-123-1",
        )

    monkeypatch.setattr(solr_runtime.subprocess, "run", missing)
    state = {
        "instance": "provisioned", "owned_by_run": True,
        "owner_token": "123-1", "container": "fetchcore-solr-123-1",
    }
    assert cleanup_solr_runtime(state) is True
    assert cleanup_solr_runtime(state) is True


def test_missing_resource_requires_explicit_creation(tmp_path: Path) -> None:
    solr = FakeSolr()
    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(config(tmp_path), client=solr, docker=FakeDocker(forbidden=True))
    assert failure.value.category == "resource_missing"
    assert not any(call[2].get("action") == "CREATE" for call in solr.calls)


def test_missing_standalone_core_is_created_only_when_authorized(tmp_path: Path) -> None:
    solr = FakeSolr()
    state = prepare_solr_runtime(
        config(tmp_path, create_if_missing=True),
        client=solr,
        docker=FakeDocker(forbidden=True),
    )
    assert state.resource_state == "created"
    assert state.repository_binding == "unbound"
    create = next(call for call in solr.calls if call[2].get("action") == "CREATE")
    assert create[2]["configSet"] == "fetchcore"


def test_repeated_explicit_bootstrap_reuses_the_created_resource(tmp_path: Path) -> None:
    solr = FakeSolr()
    docker = FakeDocker(forbidden=True)
    bootstrap_config = config(tmp_path, create_if_missing=True)

    first = prepare_solr_runtime(bootstrap_config, client=solr, docker=docker)
    create_calls_after_first = sum(call[2].get("action") == "CREATE" for call in solr.calls)
    second = prepare_solr_runtime(bootstrap_config, client=solr, docker=docker)
    create_calls_after_second = sum(call[2].get("action") == "CREATE" for call in solr.calls)

    assert first.resource_state == "created"
    assert second.resource_state == "existing"
    assert create_calls_after_first == 1
    assert create_calls_after_second == 1
    assert docker.calls == []


def test_missing_server_side_configset_fails_explicitly(tmp_path: Path) -> None:
    class MissingConfigset(FakeSolr):
        def request(self, method: str, url: str, **kwargs: Any) -> Mapping[str, object]:
            params = kwargs.get("params") or {}
            if url.endswith("/admin/cores") and params.get("action") == "CREATE":
                raise SolrRuntimeError("http_error", "HTTP 400")
            return super().request(method, url, **kwargs)

    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(
            config(tmp_path, create_if_missing=True),
            client=MissingConfigset(),
            docker=FakeDocker(forbidden=True),
        )
    assert failure.value.category == "configset_unavailable"


@pytest.mark.parametrize(
    ("mode", "expected", "category"),
    [
        ("mystery", DeploymentMode.AUTO, "deployment_mode_unknown"),
        ("std", DeploymentMode.SOLRCLOUD, "deployment_mode_mismatch"),
    ],
)
def test_unknown_or_incompatible_deployment_mode_fails_closed(
    tmp_path: Path, mode: str, expected: DeploymentMode, category: str,
) -> None:
    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(
            config(tmp_path, deployment_mode=expected),
            client=FakeSolr(mode=mode),
            docker=FakeDocker(forbidden=True),
        )
    assert failure.value.category == category


def test_solrcloud_uses_collections_api_and_checks_configset(tmp_path: Path) -> None:
    solr = FakeSolr(mode="solrcloud")
    state = prepare_solr_runtime(
        config(
            tmp_path,
            deployment_mode=DeploymentMode.SOLRCLOUD,
            create_if_missing=True,
        ),
        client=solr,
        docker=FakeDocker(forbidden=True),
    )
    assert state.deployment_mode == "solrcloud"
    assert state.resource_kind == "collection"
    assert any(call[1].endswith("/admin/configs") for call in solr.calls)
    assert any(call[1].endswith("/api/collections") for call in solr.calls)
    assert not any(call[1].endswith("/admin/cores") for call in solr.calls)


def test_repository_resource_collision_fails_closed(tmp_path: Path) -> None:
    solr = FakeSolr(resources={"fetchcore-self-dogfood"}, repositories={"other/repository"})
    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(config(tmp_path), client=solr, docker=FakeDocker(forbidden=True))
    assert failure.value.category == "repository_resource_collision"
    assert all(method == "GET" for method, *_ in solr.calls)


def test_unbound_documents_cannot_be_silently_repurposed(tmp_path: Path) -> None:
    solr = FakeSolr(
        resources={"fetchcore-self-dogfood"},
        repositories={"oalangomes/fetchcore"},
        unbound_documents=1,
    )
    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(config(tmp_path), client=solr, docker=FakeDocker(forbidden=True))
    assert failure.value.category == "repository_resource_collision"
    assert all(method == "GET" for method, *_ in solr.calls)


def test_unknown_repository_binding_fails_closed(tmp_path: Path) -> None:
    class UnknownBinding(FakeSolr):
        def request(self, method: str, url: str, **kwargs: Any) -> Mapping[str, object]:
            if url.endswith("/select"):
                return {"response": {"numFound": 1, "numFoundExact": False}}
            return super().request(method, url, **kwargs)

    solr = UnknownBinding(resources={"fetchcore-self-dogfood"})
    with pytest.raises(SolrRuntimeError) as failure:
        prepare_solr_runtime(
            config(tmp_path),
            client=solr,
            docker=FakeDocker(forbidden=True),
        )
    assert failure.value.category == "repository_binding_unknown"
    assert all(method == "GET" for method, *_ in solr.calls)


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (HTTPError("https://redacted.invalid", 401, "", Message(), io.BytesIO()), "authentication_required"),
        (HTTPError("https://redacted.invalid", 403, "", Message(), io.BytesIO()), "authentication_required"),
        (HTTPError("https://redacted.invalid", 503, "", Message(), io.BytesIO()), "service_error"),
        (URLError(ConnectionRefusedError()), "connection_refused"),
        (URLError(TimeoutError()), "timeout"),
    ],
)
def test_http_client_classifies_service_failures(monkeypatch: pytest.MonkeyPatch, error: Exception,
                                                category: str) -> None:
    def fail(*args: Any, **kwargs: Any) -> Any:
        raise error

    monkeypatch.setattr(solr_runtime, "urlopen", fail)
    with pytest.raises(SolrRuntimeError) as failure:
        solr_runtime.UrllibJsonClient().request(
            "GET", "http://127.0.0.1:8983/solr/admin/info/system", timeout=1,
        )
    assert failure.value.category == category
    if isinstance(error, HTTPError) and error.code == 503:
        assert failure.value.http_status == 503


def test_credentials_and_unsafe_paths_are_rejected_without_disclosure(tmp_path: Path) -> None:
    with pytest.raises(SolrRuntimeError) as failure:
        config(tmp_path, solr_url="http://user:secret@localhost:8983/solr")
    assert failure.value.category == "invalid_configuration"
    assert "secret" not in str(failure.value)


def test_cli_failure_is_structured_and_redacted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    output_path = tmp_path / "diagnostics" / "state.json"
    result = solr_runtime.main([
        "prepare", "--solr-url", "http://user:secret@localhost:8983/solr",
        "--resource", "fetchcore", "--repository", "oalangomes/fetchcore",
        "--output-json", str(output_path),
        "--output-env", str(tmp_path / "state.env"),
    ])
    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert output_path.is_file()
    assert json.loads(output_path.read_text(encoding="utf-8")) == output
    assert output["diagnostic_version"] == 1
    assert output["stage"] == "solr_preflight"
    assert output["error"]["category"] == "invalid_configuration"
    assert "secret" not in json.dumps(output)


def test_missing_resource_writes_a_preflight_artifact_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    solr = FakeSolr()
    docker = FakeDocker(forbidden=True)
    monkeypatch.setattr(solr_runtime, "UrllibJsonClient", lambda: solr)
    monkeypatch.setattr(solr_runtime, "SubprocessDockerRunner", lambda: docker)
    output_path = tmp_path / "evidence" / "solr-preflight.json"
    result = solr_runtime.main([
        "prepare",
        "--solr-url", "http://127.0.0.1:8983/solr",
        "--resource", "fetchcore-self-dogfood",
        "--repository", "oalangomes/fetchcore",
        "--output-json", str(output_path),
        "--output-env", str(tmp_path / "state.env"),
    ])

    output = json.loads(capsys.readouterr().out)
    assert result == 1
    assert json.loads(output_path.read_text(encoding="utf-8")) == output
    assert output["status"] == "failed"
    assert output["stage"] == "solr_preflight"
    assert output["error"]["category"] == "resource_missing"
    assert len(output["error"]["message"]) <= 500
    assert not any(call[2].get("action") == "CREATE" for call in solr.calls)
    assert docker.calls == []


def _owned_prepare_args(tmp_path: Path) -> list[str]:
    return [
        "prepare", "--solr-url", "http://127.0.0.1:8983/solr",
        "--resource", "fetchcore-self-dogfood", "--repository", "oalangomes/fetchcore",
        "--create-if-missing", "--provision-mode", "provision_if_missing",
        "--solr-image", IMAGE, "--configset-dir", str(tmp_path),
        "--owner-token", "123-1", "--output-json", str(tmp_path / "state.json"),
        "--output-env", str(tmp_path / "state.env"),
    ]


def test_post_provision_output_failure_removes_exact_owned_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    docker = FakeDocker()
    solr = FakeSolr(resources={"fetchcore-self-dogfood"}, unavailable_calls=1)
    monkeypatch.setattr(solr_runtime, "SubprocessDockerRunner", lambda: docker)
    monkeypatch.setattr(solr_runtime, "UrllibJsonClient", lambda: solr)
    monkeypatch.setattr(solr_runtime, "_free_loopback_port", lambda: 49123)
    original_write = Path.write_text

    def fail_env_write(path: Path, data: str, *, encoding: str) -> int:
        if path == tmp_path / "state.env":
            raise OSError("state output unavailable")
        return original_write(path, data, encoding=encoding)

    monkeypatch.setattr(Path, "write_text", fail_env_write)
    result = solr_runtime.main(_owned_prepare_args(tmp_path))

    assert result == 1
    assert docker.calls[0][:4] == ("run", "-d", "--name", "fetchcore-solr-123-1")
    assert docker.calls[-1] == ("rm", "-f", "fetchcore-solr-123-1")
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["message"] == "state output unavailable"
    assert "cleanup_error" not in output
    diagnostic = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert diagnostic["status"] == "failed"
    assert diagnostic["instance"] == "provisioned"
    assert diagnostic["owned_by_run"] is True


def test_reused_runtime_output_failure_does_not_run_docker_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    docker = FakeDocker(forbidden=True)
    solr = FakeSolr(resources={"fetchcore-self-dogfood"})
    monkeypatch.setattr(solr_runtime, "SubprocessDockerRunner", lambda: docker)
    monkeypatch.setattr(solr_runtime, "UrllibJsonClient", lambda: solr)
    original_write = Path.write_text

    def fail_env_write(path: Path, data: str, *, encoding: str) -> int:
        if path == tmp_path / "state.env":
            raise OSError("state output unavailable")
        return original_write(path, data, encoding=encoding)

    monkeypatch.setattr(Path, "write_text", fail_env_write)
    args = _owned_prepare_args(tmp_path)
    args[args.index("provision_if_missing")] = "reuse_only"
    result = solr_runtime.main(args)

    assert result == 1
    assert docker.calls == []
    assert json.loads(capsys.readouterr().out)["error"]["message"] == "state output unavailable"


def test_cleanup_failure_reports_original_and_cleanup_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingCleanupDocker(FakeDocker):
        def run(self, arguments: Sequence[str]) -> None:
            super().run(arguments)
            if arguments[:2] == ("rm", "-f"):
                raise SolrRuntimeError("provisioning_failed", "Exact owned cleanup failed")

    docker = FailingCleanupDocker()
    solr = FakeSolr(resources={"fetchcore-self-dogfood"}, unavailable_calls=1)
    monkeypatch.setattr(solr_runtime, "SubprocessDockerRunner", lambda: docker)
    monkeypatch.setattr(solr_runtime, "UrllibJsonClient", lambda: solr)
    monkeypatch.setattr(solr_runtime, "_free_loopback_port", lambda: 49123)
    original_write = Path.write_text

    def fail_env_write(path: Path, data: str, *, encoding: str) -> int:
        if path == tmp_path / "state.env":
            raise OSError("state output unavailable")
        return original_write(path, data, encoding=encoding)

    monkeypatch.setattr(Path, "write_text", fail_env_write)
    result = solr_runtime.main(_owned_prepare_args(tmp_path))

    assert result == 1
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["message"] == "state output unavailable"
    assert output["cleanup_error"]["message"] == "Exact owned cleanup failed"
    assert docker.calls[-1] == ("rm", "-f", "fetchcore-solr-123-1")


def test_cleanup_command_failure_does_not_trigger_prepare_compensation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    class FailingCleanupDocker(FakeDocker):
        def run(self, arguments: Sequence[str]) -> None:
            super().run(arguments)
            raise SolrRuntimeError("provisioning_failed", "Exact owned cleanup failed")

    docker = FailingCleanupDocker()
    monkeypatch.setattr(solr_runtime, "SubprocessDockerRunner", lambda: docker)
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "instance": "provisioned", "owned_by_run": True,
        "owner_token": "123-1", "container": "fetchcore-solr-123-1",
    }), encoding="utf-8")

    result = solr_runtime.main(["cleanup", "--state", str(state_path)])

    assert result == 1
    assert docker.calls == [("rm", "-f", "fetchcore-solr-123-1")]
    output = json.loads(capsys.readouterr().out)
    assert output["error"]["message"] == "Exact owned cleanup failed"
    assert "cleanup_error" not in output


@pytest.mark.parametrize('status', [503, 500, 401, 403])
@pytest.mark.parametrize('persistent', [False, True])
def test_owned_startup_retries_only_503_with_bounded_failure_and_cleanup(
    tmp_path: Path, status: int, persistent: bool,
) -> None:
    class StartingSolr(FakeSolr):
        attempts = 0

        def request(self, method: str, url: str, **kwargs: Any) -> Mapping[str, object]:
            if url.endswith('/admin/info/system'):
                self.attempts += 1
                if self.attempts == 1:
                    raise SolrRuntimeError('connection_refused', 'initial missing service')
                if persistent or self.attempts == 2:
                    category = 'authentication_required' if status in (401, 403) else 'service_error'
                    raise SolrRuntimeError(category, f'HTTP {status}', http_status=status)
            return super().request(method, url, **kwargs)

    solr = StartingSolr(resources={'fetchcore-self-dogfood'})
    docker = FakeDocker()
    sleeps = []
    kwargs = {
        "client": solr, "docker": docker, "choose_port": lambda: 49123, "sleep": sleeps.append,
    }
    cfg = config(tmp_path, create_if_missing=True, provision_mode=ProvisionMode.PROVISION_IF_MISSING)
    if status == 503 and not persistent:
        state = prepare_solr_runtime(cfg, **kwargs)
        assert state.owned_by_run
        assert solr.attempts == 3
        assert len(sleeps) == 1
        assert len(docker.calls) == 1
    else:
        with pytest.raises(SolrRuntimeError) as failure:
            prepare_solr_runtime(cfg, **kwargs)
        assert failure.value.category == (
            'authentication_required' if status in (401, 403) else 'service_error'
        )
        assert solr.attempts == (3 if status == 503 else 2)
        assert len(sleeps) == (1 if status == 503 else 0)
        assert docker.calls[-1] == ('rm', '-f', 'fetchcore-solr-123-1')


def test_existing_503_never_authorizes_replacement_container(tmp_path: Path) -> None:
    class UnavailableSolr(FakeSolr):
        def request(self, *args: Any, **kwargs: Any) -> Mapping[str, object]:
            raise SolrRuntimeError('service_error', 'HTTP 503', http_status=503)

    docker = FakeDocker(forbidden=True)
    with pytest.raises(SolrRuntimeError, match='503'):
        prepare_solr_runtime(
            config(tmp_path, create_if_missing=True, provision_mode=ProvisionMode.PROVISION_IF_MISSING),
            client=UnavailableSolr(), docker=docker,
        )
    assert docker.calls == []
