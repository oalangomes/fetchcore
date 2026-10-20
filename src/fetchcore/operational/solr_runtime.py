"""Reuse-first Solr service and resource preparation for CI dogfood.

This module owns only Solr process/resource observations. Repository indexing,
snapshot compatibility, currentness, and retrieval remain in the application
service and backend adapter contracts.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


_RESOURCE_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_OWNER_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_PINNED_IMAGE_PATTERN = re.compile(r"[^\s]+@sha256:[0-9a-f]{64}")


class DeploymentMode(str, Enum):
    AUTO = "auto"
    STANDALONE = "standalone"
    SOLRCLOUD = "solrcloud"


class ProvisionMode(str, Enum):
    REUSE_ONLY = "reuse_only"
    PROVISION_IF_MISSING = "provision_if_missing"


class SolrRuntimeError(RuntimeError):
    """Safe, typed operational failure; messages never include configured URLs."""

    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True)
class SolrRuntimeConfig:
    solr_url: str
    resource: str
    repository: str
    deployment_mode: DeploymentMode = DeploymentMode.AUTO
    create_if_missing: bool = False
    provision_mode: ProvisionMode = ProvisionMode.REUSE_ONLY
    configset: str = "fetchcore"
    solr_image: str = ""
    configset_dir: Path | None = None
    owner_token: str = ""
    timeout_seconds: float = 3.0
    startup_attempts: int = 60
    startup_interval_seconds: float = 2.0

    def __post_init__(self) -> None:
        _validated_endpoint(self.solr_url)
        for label, value in (("resource", self.resource), ("configset", self.configset)):
            if not _RESOURCE_PATTERN.fullmatch(value):
                raise SolrRuntimeError("invalid_configuration", f"Invalid Solr {label}")
        if not _REPOSITORY_PATTERN.fullmatch(self.repository):
            raise SolrRuntimeError("invalid_configuration", "Invalid repository identity")
        if self.timeout_seconds <= 0 or self.startup_attempts < 1:
            raise SolrRuntimeError("invalid_configuration", "Invalid Solr retry configuration")


@dataclass(frozen=True)
class ServiceObservation:
    endpoint: str
    deployment_mode: DeploymentMode
    solr_version: str | None


@dataclass(frozen=True)
class SolrRuntimeState:
    endpoint: str
    deployment_mode: str
    instance: str
    resource_kind: str
    resource: str
    resource_state: str
    repository_binding: str
    configset: str
    owned_by_run: bool
    owner_token: str | None = None
    container: str | None = None

    def to_mapping(self) -> dict[str, object]:
        return cast(dict[str, object], asdict(self))


class JsonClient(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        payload: Mapping[str, object] | None = None,
        timeout: float,
    ) -> Mapping[str, object]: ...


class DockerRunner(Protocol):
    def run(self, arguments: Sequence[str]) -> None: ...


class UrllibJsonClient:
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        payload: Mapping[str, object] | None = None,
        timeout: float,
    ) -> Mapping[str, object]:
        query = urlencode(params or {})
        target = f"{url}?{query}" if query else url
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif method == "POST":
            data = b""
        request = Request(target, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise SolrRuntimeError(
                    "authentication_required", "Solr endpoint rejected authentication",
                ) from exc
            if 500 <= exc.code <= 599:
                raise SolrRuntimeError(
                    "service_error", f"Solr endpoint returned HTTP {exc.code}",
                ) from exc
            raise SolrRuntimeError(
                "http_error", f"Solr endpoint returned HTTP {exc.code}",
            ) from exc
        except TimeoutError as exc:
            raise SolrRuntimeError("timeout", "Solr endpoint timed out") from exc
        except URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise SolrRuntimeError("timeout", "Solr endpoint timed out") from exc
            if isinstance(reason, ConnectionRefusedError):
                raise SolrRuntimeError("connection_refused", "Solr endpoint refused connection") from exc
            raise SolrRuntimeError("connection_failure", "Solr endpoint is unreachable") from exc
        except OSError as exc:
            if isinstance(exc, ConnectionRefusedError):
                raise SolrRuntimeError("connection_refused", "Solr endpoint refused connection") from exc
            raise SolrRuntimeError("connection_failure", "Solr endpoint is unreachable") from exc
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SolrRuntimeError("not_solr", "HTTP endpoint did not return Solr JSON") from exc
        if not isinstance(value, dict):
            raise SolrRuntimeError("not_solr", "HTTP endpoint did not return a Solr object")
        return cast(Mapping[str, object], value)


class SubprocessDockerRunner:
    def run(self, arguments: Sequence[str]) -> None:
        try:
            completed = subprocess.run(
                ["docker", *arguments], check=False, capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SolrRuntimeError("provisioning_failed", "Docker provisioning failed") from exc
        if completed.returncode != 0:
            if (
                tuple(arguments[:2]) == ("rm", "-f")
                and "No such container" in completed.stderr
            ):
                return
            raise SolrRuntimeError("provisioning_failed", "Docker provisioning failed")


def prepare_solr_runtime(
    config: SolrRuntimeConfig,
    *,
    client: JsonClient | None = None,
    docker: DockerRunner | None = None,
    choose_port: Callable[[], int] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> SolrRuntimeState:
    """Reuse a compatible service or provision one explicitly owned instance."""

    http = client or UrllibJsonClient()
    docker_runner = docker or SubprocessDockerRunner()
    try:
        service = probe_solr_service(
            config.solr_url,
            expected_mode=config.deployment_mode,
            timeout=config.timeout_seconds,
            client=http,
        )
    except SolrRuntimeError as exc:
        if exc.category not in {"connection_refused", "connection_failure", "timeout"}:
            raise
        if config.provision_mode is ProvisionMode.REUSE_ONLY:
            raise SolrRuntimeError(
                exc.category,
                f"Solr unavailable under reuse_only policy ({exc.category}); configure a reachable "
                "endpoint or explicitly select provision_if_missing",
            ) from exc
        return _provision_runtime(
            config,
            client=http,
            docker=docker_runner,
            choose_port=choose_port or _free_loopback_port,
            sleep=sleep,
        )

    existed = _resource_exists(service, config.resource, client=http, timeout=config.timeout_seconds)
    if not existed:
        if not config.create_if_missing:
            raise SolrRuntimeError(
                "resource_missing",
                "Dedicated Solr resource is absent; set create_if_missing explicitly to create it",
            )
        _create_resource(service, config, client=http)
        if not _resource_exists(
            service, config.resource, client=http, timeout=config.timeout_seconds,
        ):
            raise SolrRuntimeError("resource_creation_failed", "Solr resource was not created")
    binding = _repository_binding(
        service, config.resource, config.repository, client=http, timeout=config.timeout_seconds,
    )
    return SolrRuntimeState(
        endpoint=service.endpoint,
        deployment_mode=service.deployment_mode.value,
        instance="reused",
        resource_kind="core" if service.deployment_mode is DeploymentMode.STANDALONE else "collection",
        resource=config.resource,
        resource_state="existing" if existed else "created",
        repository_binding=binding,
        configset=config.configset,
        owned_by_run=False,
    )


def probe_solr_service(
    solr_url: str,
    *,
    expected_mode: DeploymentMode,
    timeout: float,
    client: JsonClient,
) -> ServiceObservation:
    endpoint = _validated_endpoint(solr_url)
    payload = client.request(
        "GET", f"{endpoint}/admin/info/system", params={"wt": "json"}, timeout=timeout,
    )
    response_header = payload.get("responseHeader")
    lucene = payload.get("lucene")
    raw_mode = payload.get("mode")
    if (
        not isinstance(response_header, dict)
        or response_header.get("status") != 0
        or not isinstance(lucene, dict)
        or not isinstance(lucene.get("solr-spec-version"), str)
    ):
        raise SolrRuntimeError("not_solr", "HTTP endpoint is not a compatible Solr service")
    if raw_mode == "std":
        actual_mode = DeploymentMode.STANDALONE
    elif raw_mode == "solrcloud":
        actual_mode = DeploymentMode.SOLRCLOUD
    else:
        raise SolrRuntimeError(
            "deployment_mode_unknown", "Solr deployment mode could not be determined safely",
        )
    if expected_mode is not DeploymentMode.AUTO and expected_mode is not actual_mode:
        raise SolrRuntimeError(
            "deployment_mode_mismatch", "Configured Solr deployment mode does not match the service",
        )
    version = lucene.get("solr-spec-version")
    return ServiceObservation(
        endpoint=endpoint,
        deployment_mode=actual_mode,
        solr_version=str(version) if version is not None else None,
    )


def cleanup_solr_runtime(state: Mapping[str, object], *, docker: DockerRunner | None = None) -> bool:
    """Remove only an exact container recorded as owned by this run."""

    if state.get("instance") != "provisioned" or state.get("owned_by_run") is not True:
        return False
    owner_token = state.get("owner_token")
    container = state.get("container")
    if not isinstance(owner_token, str) or not _OWNER_PATTERN.fullmatch(owner_token):
        raise SolrRuntimeError("invalid_cleanup_state", "Owned Solr state has an invalid owner token")
    expected = f"fetchcore-solr-{owner_token}"
    if container != expected:
        raise SolrRuntimeError("invalid_cleanup_state", "Owned Solr container identity does not match")
    (docker or SubprocessDockerRunner()).run(("rm", "-f", expected))
    return True


def _provision_runtime(
    config: SolrRuntimeConfig,
    *,
    client: JsonClient,
    docker: DockerRunner,
    choose_port: Callable[[], int],
    sleep: Callable[[float], None],
) -> SolrRuntimeState:
    if not config.create_if_missing:
        raise SolrRuntimeError(
            "resource_missing",
            "Owned provisioning also requires explicit create_if_missing authorization",
        )
    configured = urlsplit(_validated_endpoint(config.solr_url))
    if configured.scheme != "http" or configured.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise SolrRuntimeError(
            "unsafe_provisioning_target",
            "provision_if_missing is limited to an HTTP loopback Solr endpoint",
        )
    if not _PINNED_IMAGE_PATTERN.fullmatch(config.solr_image):
        raise SolrRuntimeError("invalid_configuration", "Solr image must be pinned by sha256")
    # The bundled Projection v3 configset keeps explicit owned provisioning
    # available to users who installed a wheel outside the source checkout.
    bundled_configset = Path(__file__).parent / "configset"
    selected_configset = config.configset_dir or bundled_configset
    try:
        configset_dir = selected_configset.expanduser().resolve(strict=True)
    except OSError as exc:
        raise SolrRuntimeError("invalid_configuration", "Solr configset directory is unavailable") from exc
    if not configset_dir.is_dir():
        raise SolrRuntimeError("invalid_configuration", "Solr configset path is not a directory")
    if not _OWNER_PATTERN.fullmatch(config.owner_token):
        raise SolrRuntimeError("invalid_configuration", "Provisioning requires a safe owner token")
    port = choose_port()
    if not 1 <= port <= 65535:
        raise SolrRuntimeError("provisioning_failed", "Free-port selection returned an invalid port")
    container = f"fetchcore-solr-{config.owner_token}"
    docker.run((
        "run", "-d", "--name", container, "--pull=missing",
        "-p", f"127.0.0.1:{port}:8983",
        "-v", f"{configset_dir}:/fetchcore-configset:ro",
        config.solr_image, "solr-precreate", config.resource, "/fetchcore-configset",
    ))
    endpoint = f"http://127.0.0.1:{port}/solr"
    try:
        service = _wait_for_service(config, endpoint=endpoint, client=client, sleep=sleep)
        if not _resource_exists(
            service, config.resource, client=client, timeout=config.timeout_seconds,
        ):
            raise SolrRuntimeError("resource_creation_failed", "Owned Solr resource was not created")
        binding = _repository_binding(
            service, config.resource, config.repository,
            client=client, timeout=config.timeout_seconds,
        )
    except Exception:
        docker.run(("rm", "-f", container))
        raise
    return SolrRuntimeState(
        endpoint=endpoint,
        deployment_mode=service.deployment_mode.value,
        instance="provisioned",
        resource_kind="core",
        resource=config.resource,
        resource_state="created",
        repository_binding=binding,
        configset=config.configset,
        owned_by_run=True,
        owner_token=config.owner_token,
        container=container,
    )


def _wait_for_service(
    config: SolrRuntimeConfig,
    *,
    endpoint: str,
    client: JsonClient,
    sleep: Callable[[float], None],
) -> ServiceObservation:
    last_error: SolrRuntimeError | None = None
    for attempt in range(config.startup_attempts):
        try:
            service = probe_solr_service(
                endpoint,
                expected_mode=DeploymentMode.STANDALONE,
                timeout=config.timeout_seconds,
                client=client,
            )
            if config.deployment_mode is DeploymentMode.SOLRCLOUD:
                raise SolrRuntimeError(
                    "deployment_mode_mismatch",
                    "Owned standalone provisioning cannot satisfy solrcloud mode",
                )
            return service
        except SolrRuntimeError as exc:
            last_error = exc
            if exc.category not in {"connection_refused", "connection_failure", "timeout"}:
                raise
            if attempt + 1 < config.startup_attempts:
                sleep(config.startup_interval_seconds)
    category = last_error.category if last_error is not None else "provisioning_failed"
    raise SolrRuntimeError(category, "Owned Solr container did not become ready")


def _resource_exists(
    service: ServiceObservation,
    resource: str,
    *,
    client: JsonClient,
    timeout: float,
) -> bool:
    if service.deployment_mode is DeploymentMode.STANDALONE:
        payload = client.request(
            "GET", f"{service.endpoint}/admin/cores",
            params={"action": "STATUS", "core": resource, "indexInfo": "false", "wt": "json"},
            timeout=timeout,
        )
        status = payload.get("status")
        if not isinstance(status, dict):
            raise SolrRuntimeError("not_solr", "CoreAdmin returned an invalid status response")
        details = status.get(resource)
        return isinstance(details, dict) and details.get("name") == resource
    payload = client.request(
        "GET", f"{service.endpoint}/admin/collections",
        params={"action": "LIST", "wt": "json"}, timeout=timeout,
    )
    collections = payload.get("collections")
    if not isinstance(collections, list) or not all(isinstance(item, str) for item in collections):
        raise SolrRuntimeError("not_solr", "Collections API returned an invalid list response")
    return resource in collections


def _create_resource(
    service: ServiceObservation,
    config: SolrRuntimeConfig,
    *,
    client: JsonClient,
) -> None:
    if service.deployment_mode is DeploymentMode.STANDALONE:
        try:
            client.request(
                "POST", f"{service.endpoint}/admin/cores",
                params={
                    "action": "CREATE", "name": config.resource,
                    "configSet": config.configset, "wt": "json",
                },
                timeout=config.timeout_seconds,
            )
        except SolrRuntimeError as exc:
            if exc.category == "http_error":
                raise SolrRuntimeError(
                    "configset_unavailable",
                    "Standalone Solr could not create the core from the configured server-side configset",
                ) from exc
            raise
        return
    configsets = client.request(
        "GET", f"{service.endpoint}/admin/configs",
        params={"action": "LIST", "omitHeader": "true", "wt": "json"},
        timeout=config.timeout_seconds,
    ).get("configSets")
    if not isinstance(configsets, list) or config.configset not in configsets:
        raise SolrRuntimeError(
            "configset_unavailable", "Required SolrCloud configset is not available server-side",
        )
    client.request(
        "POST", f"{service.endpoint}/api/collections",
        payload={"name": config.resource, "config": config.configset, "numShards": 1},
        timeout=config.timeout_seconds,
    )


def _repository_binding(
    service: ServiceObservation,
    resource: str,
    repository: str,
    *,
    client: JsonClient,
    timeout: float,
) -> str:
    def count(query: str) -> int:
        payload = client.request(
            "GET", f"{service.endpoint}/{resource}/select",
            params={"q": query, "rows": "0", "wt": "json"}, timeout=timeout,
        )
        response = payload.get("response")
        if (
            not isinstance(response, dict)
            or not isinstance(response.get("numFound"), int)
            or isinstance(response["numFound"], bool)
            or response.get("numFoundExact") is False
        ):
            raise SolrRuntimeError(
                "repository_binding_unknown", "Solr resource binding count is unavailable",
            )
        return cast(int, response["numFound"])

    total = count("*:*")
    if total == 0:
        return "unbound"
    matching = count(f'repository:"{repository}"')
    if matching == total:
        return "matching"
    raise SolrRuntimeError(
        "repository_resource_collision",
        "Dedicated Solr resource contains a snapshot for a different repository",
    )


def _validated_endpoint(value: str) -> str:
    if value != value.strip() or any(character.isspace() for character in value):
        raise SolrRuntimeError("invalid_configuration", "Invalid Solr endpoint")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise SolrRuntimeError("invalid_configuration", "Invalid Solr endpoint") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and port < 1)
        or parsed.path.rstrip("/") != "/solr"
    ):
        raise SolrRuntimeError(
            "invalid_configuration",
            "Solr endpoint must be an absolute credential-free URL ending in /solr",
        )
    return urlunsplit((parsed.scheme, parsed.netloc, "/solr", "", ""))


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_env(path: Path, state: SolrRuntimeState) -> None:
    values = {
        "FETCHCORE_SOLR_URL": state.endpoint,
        "FETCHCORE_SOLR_DEPLOYMENT_MODE": state.deployment_mode,
        "FETCHCORE_SOLR_INSTANCE": state.instance,
        "FETCHCORE_SOLR_RESOURCE_KIND": state.resource_kind,
        "FETCHCORE_SOLR_RESOURCE_STATE": state.resource_state,
        "FETCHCORE_SOLR_REPOSITORY_BINDING": state.repository_binding,
    }
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prepare a reuse-first Solr dogfood runtime")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--solr-url", required=True)
    prepare.add_argument("--deployment-mode", choices=[item.value for item in DeploymentMode], default="auto")
    prepare.add_argument("--resource", required=True)
    prepare.add_argument("--repository", required=True)
    prepare.add_argument("--create-if-missing", action="store_true")
    prepare.add_argument("--provision-mode", choices=[item.value for item in ProvisionMode], default="reuse_only")
    prepare.add_argument("--configset", default="fetchcore")
    prepare.add_argument("--solr-image", default="")
    prepare.add_argument("--configset-dir", type=Path)
    prepare.add_argument("--owner-token", default="")
    prepare.add_argument("--output-json", type=Path, required=True)
    prepare.add_argument("--output-env", type=Path, required=True)
    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--state", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    prepared_state: SolrRuntimeState | None = None
    try:
        if args.command == "cleanup":
            state = json.loads(args.state.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                raise SolrRuntimeError("invalid_cleanup_state", "Solr state must be an object")
            cleanup_solr_runtime(state)
            return 0
        config = SolrRuntimeConfig(
            solr_url=args.solr_url,
            resource=args.resource,
            repository=args.repository,
            deployment_mode=DeploymentMode(args.deployment_mode),
            create_if_missing=args.create_if_missing,
            provision_mode=ProvisionMode(args.provision_mode),
            configset=args.configset,
            solr_image=args.solr_image,
            configset_dir=args.configset_dir,
            owner_token=args.owner_token,
        )
        prepared_state = prepare_solr_runtime(config)
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(prepared_state.to_mapping(), sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_env(args.output_env, prepared_state)
        print(json.dumps(prepared_state.to_mapping(), sort_keys=True))
        return 0
    except (OSError, ValueError, json.JSONDecodeError, SolrRuntimeError) as exc:
        failure: dict[str, object] = {
            "diagnostic_version": 1,
            "stage": "solr_preflight",
            "status": "failed",
            "error": {
                "category": (
                    exc.category if isinstance(exc, SolrRuntimeError) else "solr_runtime_failure"
                ),
                "message": str(exc)[:500],
            },
        }
        if prepared_state is not None:
            failure.update(prepared_state.to_mapping())
        if prepared_state is not None and prepared_state.owned_by_run:
            try:
                cleanup_solr_runtime(prepared_state.to_mapping())
            except Exception as cleanup_exc:  # noqa: BLE001 - preserve both failures
                failure["cleanup_error"] = {
                    "category": (
                        cleanup_exc.category
                        if isinstance(cleanup_exc, SolrRuntimeError)
                        else "cleanup_failed"
                    ),
                    "message": (
                        str(cleanup_exc)[:500] if isinstance(cleanup_exc, SolrRuntimeError)
                        else "Owned Solr cleanup failed"
                    ),
                }
        if args.command == "prepare":
            try:
                args.output_json.parent.mkdir(parents=True, exist_ok=True)
                args.output_json.write_text(
                    json.dumps(failure, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8",
                )
            except OSError as diagnostic_exc:
                print(
                    f"Failed to write Solr preflight diagnostic: {diagnostic_exc}",
                    file=sys.stderr,
                )
        print(json.dumps(failure, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
