"""Run inside an installed candidate, outside the source tree (also in containers)."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile


async def protocol_smoke(env: dict[str, str], workspace: Path) -> None:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    executable = Path(sys.executable).parent / "fetchcore-mcp"
    parameters = StdioServerParameters(command=str(executable), cwd=str(workspace), env=env)
    async with asyncio.timeout(30):
        async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            if not {"search", "get_source"} <= {tool.name for tool in tools.tools}:
                raise ValueError("Installed MCP entry point is missing required tools")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--require-non-root", action="store_true")
    args = parser.parse_args()
    import fetchcore

    version = importlib.metadata.version("fetchcore")
    if version != args.version:
        raise ValueError("Installed version differs from candidate version")
    if not Path(fetchcore.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        raise ValueError("FetchCore was imported from outside the installed environment")
    if args.require_non_root and os.getuid() == 0:
        raise ValueError("Candidate container runs as root")
    # Packaged resources are required by explicit Solr provisioning, even though
    # this packaging check does not connect to or provision a backend.
    from importlib.resources import files

    configset = files("fetchcore.operational").joinpath("configset", "conf")
    for name in ("schema.xml", "solrconfig.xml"):
        if not configset.joinpath(name).is_file():
            raise ValueError("Installed package is missing its Solr configset")
    with tempfile.TemporaryDirectory(prefix="fetchcore-smoke-") as directory:
        workspace = Path(directory)
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("FETCHCORE_", "PYTHON", "OLLAMA_"))}
        env.update(FETCHCORE_MCP_BACKEND="local-semantic", FETCHCORE_MCP_WORKSPACE=directory)
        executable = Path(sys.executable).parent / "fetchcore"
        subprocess.run([str(executable), "--help"], cwd=workspace, env=env,
                       check=True, capture_output=True, timeout=30)
        result = subprocess.run([str(executable), "capabilities", "--json"], cwd=workspace,
                                env=env, check=True, capture_output=True, text=True, timeout=30)
        if json.loads(result.stdout).get("status") != "succeeded":
            raise ValueError("Installed runtime inspection failed")
        asyncio.run(protocol_smoke(env, workspace))
    # Equivalent dependency inventory: names/versions only, no direct_url, paths,
    # credentials or environment variables from pip's installation metadata.
    inventory = sorted(
        ({"name": dist.metadata["Name"], "version": dist.version}
         for dist in importlib.metadata.distributions()),
        key=lambda item: (item["name"].lower(), item["version"]),
    )
    print(json.dumps({
        "version": version, "python": platform.python_version(),
        "checks": {"import": True, "cli": True, "runtime_inspection": True,
                   "mcp_initialize": True, "mcp_tools": True, "configset": True},
        "non_root": os.getuid() != 0, "dependencies": inventory,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
