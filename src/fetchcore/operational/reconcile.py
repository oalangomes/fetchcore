"""Explicit OPERATIONAL Compose serving binding reconciliation.

This operator command reads the active snapshot through application readiness.
It never indexes, changes repository policy, or mutates a backend resource.
"""

from __future__ import annotations

import argparse
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

import yaml

from fetchcore.application.readiness import ReadinessReport, inspect_readiness
from fetchcore.application.runtime_config import (
    OllamaRuntimeSettings, load_runtime_config,
)
from fetchcore.application.runtime_inspection import RuntimeInspectionService
from fetchcore.retrieval.contracts import KnowledgeSearchError


_FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")


def materialized_head(workspace: Path) -> str:
    """Return the authorized workspace's exact HEAD, with no fetch or checkout."""
    toplevel = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=workspace,
        capture_output=True, text=True, check=True, timeout=10,
    ).stdout.strip()
    if Path(toplevel).resolve(strict=True) != workspace.resolve(strict=True):
        raise ValueError("materialization_unavailable: workspace must be the Git root")
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=workspace,
        capture_output=True, text=True, check=True, timeout=10,
    )
    head = result.stdout.strip()
    if not _FULL_SHA.fullmatch(head):
        raise ValueError("materialization_unavailable: workspace HEAD is not a full commit SHA")
    return head


def validate_candidate(
    config_path: Path, *, workspace: Path, solr_url: str,
    ollama_url: str | None = None,
) -> tuple[str, ReadinessReport]:
    """Prove candidate snapshot/revision/fingerprint compatibility without writes."""
    config = load_runtime_config(path=config_path, environ={})
    if (config.backend != "solr" or config.repository is None
            or config.retrieval is None or config.retrieval.mode != "hybrid"):
        raise ValueError("unsupported_serving_profile: expected a repository-bound Solr hybrid runtime")
    root = workspace.resolve(strict=True)
    head = materialized_head(root)
    settings = dict(config.backend_settings)
    settings["url"] = solr_url
    ollama = (config.ollama if ollama_url is None else
              OllamaRuntimeSettings.from_mapping({"url": ollama_url}))
    candidate = replace(
        config, workspace=root, revision=head,
        backend_settings=settings, ollama=ollama,
    )
    report = inspect_readiness(RuntimeInspectionService(candidate))
    if (not report.ready or report.indexed_revision != head
            or report.repository != config.repository):
        failed = {
            name: fact.to_mapping() for name, fact in report.checks.items()
            if fact.state not in {"ready", "not_required"}
        }
        category = (
            "repository_binding_mismatch" if report.repository != config.repository
            else "snapshot_revision_mismatch" if report.indexed_revision != head
            else next((fact.category or fact.state for fact in report.checks.values()
                       if fact.state not in {"ready", "not_required"}), "currentness_unproven")
        )
        raise ValueError(
            f"serving_reconciliation_rejected/{category}: "
            + json.dumps({
                "requested_revision": head,
                "indexed_revision": report.indexed_revision,
                "failed_checks": failed,
                "observations": report.observations,
            }, sort_keys=True)
        )
    return head, report


def write_binding(config_path: Path, output: Path, revision: str) -> None:
    """Atomically write an operator-owned config without changing corpus policy."""
    source = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(source, dict) or not isinstance(source.get("runtime"), dict):
        raise ValueError("invalid_runtime_configuration")
    source["runtime"]["revision"] = revision
    parent = output.parent.resolve(strict=True)
    if output.is_symlink() or not parent.is_dir():
        raise ValueError("invalid_output_path")
    content = yaml.safe_dump(source, sort_keys=False)
    fd, temporary = tempfile.mkstemp(prefix=".fetchcore-serving-", suffix=".yml", dir=parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def activate_serving(compose_file: Path, output: Path) -> None:
    """Recreate only owned serving processes to refresh file bind mounts."""
    environment = dict(os.environ)
    environment["FETCHCORE_OPERATIONAL_RUNTIME_CONFIG"] = str(output.resolve(strict=True))
    subprocess.run(
        ["docker", "compose", "-f", str(compose_file.resolve(strict=True)),
         "up", "-d", "--no-deps", "--force-recreate", "mcp", "api"],
        env=environment, check=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True,
                        help="Existing OPERATIONAL serving runtime YAML")
    parser.add_argument("--workspace", type=Path, required=True,
                        help="Authorized materialized repository on the host")
    parser.add_argument("--solr-url", required=True,
                        help="Host-reachable URL for the existing Solr service")
    parser.add_argument("--ollama-url", help="Host-reachable Ollama URL")
    parser.add_argument("--output", type=Path, required=True,
                        help="Operator-owned runtime YAML outside the repository")
    parser.add_argument("--compose-file", type=Path,
                        default=Path("infra/compose/operational-mcp.yml"))
    parser.add_argument("--activate", action="store_true",
                        help="Write binding and recreate only MCP/API serving containers")
    args = parser.parse_args(argv)
    try:
        workspace = args.workspace.resolve(strict=True)
        output = args.output.expanduser().absolute()
        if output.resolve(strict=False).is_relative_to(workspace):
            raise ValueError("invalid_output_path: use an operator-owned path outside the workspace")
        revision, report = validate_candidate(
            args.config, workspace=workspace, solr_url=args.solr_url,
            ollama_url=args.ollama_url,
        )
        if args.activate:
            write_binding(args.config, output, revision)
            activate_serving(args.compose_file, output)
        print(json.dumps({
            "status": "activated" if args.activate else "compatible",
            "repository": report.repository,
            "revision": revision,
            "output": str(output) if args.activate else None,
            "readiness": report.to_mapping(),
        }, sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError, yaml.YAMLError,
            KnowledgeSearchError) as exc:
        print(json.dumps({"status": "failed", "reason": str(exc)}, sort_keys=True),
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
