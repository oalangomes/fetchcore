"""Local developer-only HTTP facade over the existing FetchCore application service.

No backend, score, authorization or retrieval semantics live in this module.
Launch on 127.0.0.1; never publish directly to an untrusted network.
"""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from functools import lru_cache
import json
import os
from pathlib import Path
import re
from collections.abc import Mapping
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from fetchcore.application import (
    BackendRegistry,
    FetchCoreRuntimeConfig,
    RetrievalApplicationService,
    SearchRequest,
    SourceReadRequest,
    load_runtime_config,
    service_from_runtime_config,
)
from fetchcore.application.runtime_inspection import RuntimeInspectionService
from fetchcore.retrieval import RetrievalConfig
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent


@dataclass(frozen=True)
class ExplorerContext:
    service: RetrievalApplicationService
    repository: str
    backend: str
    revision: str | None
    capabilities: dict[str, bool]
    retrieval: dict[str, object] | None
    targets: Mapping[str, ExplorerTarget] | None = None


@dataclass(frozen=True)
class ExplorerTarget:
    """Explicit operator-bound retrieval destination; never selected by raw endpoint."""
    id: str
    service: RetrievalApplicationService
    repository: str
    backend: str
    revision: str | None
    collection: str | None
    capabilities: dict[str, bool]
    retrieval: dict[str, object] | None
    runtime_config: FetchCoreRuntimeConfig | None = None

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "repository": self.repository,
            "backend": self.backend,
            "revision": self.revision,
            "collection": self.collection,
            "capabilities": self.capabilities,
            "retrieval": self.retrieval,
        }


def _collection(backend: str, settings: Mapping[str, object]) -> str | None:
    field = "index" if backend == "opensearch" else "collection"
    value = settings.get(field)
    return value if isinstance(value, str) and value else None


def _operator_targets(base: FetchCoreRuntimeConfig) -> dict[str, ExplorerTarget]:
    """Operator-owned allowlist. Do not enumerate arbitrary backend collections."""
    default_service = service_from_runtime_config(base)
    default = ExplorerTarget(
        id="default", service=default_service,
        repository=base.repository or base.workspace.name,
        backend=base.backend, revision=base.revision,
        collection=_collection(base.backend, base.backend_settings),
        capabilities=BackendRegistry().create(base.backend).retrieval_capabilities.to_mapping(),
        retrieval=base.retrieval.to_mapping() if base.retrieval else None,
        runtime_config=base,
    )
    targets = {"default": default}
    raw = os.environ.get("FETCHCORE_EXPLORER_TARGETS", "").strip()
    if not raw:
        return targets
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("FETCHCORE_EXPLORER_TARGETS must be valid JSON") from exc
    if not isinstance(entries, list) or len(entries) > 16:
        raise ValueError("FETCHCORE_EXPLORER_TARGETS must be an array of up to 16 targets")
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) - {
            "id", "backend", "backend_settings", "repository", "workspace", "revision", "retrieval"
        }:
            raise ValueError("Explorer targets contain unsupported fields")
        target_id = entry.get("id")
        if (not isinstance(target_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", target_id)
                or target_id in targets):
            raise ValueError("Explorer target id is invalid or duplicated")
        repository = entry.get("repository")
        workspace = entry.get("workspace")
        if not isinstance(repository, str) or not repository.strip():
            raise ValueError("Explorer target repository is required")
        if not isinstance(workspace, str) or not Path(workspace).is_absolute():
            raise ValueError("Explorer target workspace must be an absolute mounted path")
        if not Path(workspace).is_dir():
            raise ValueError("Explorer target workspace does not exist")
        settings = entry.get("backend_settings")
        if not isinstance(settings, dict):
            raise ValueError("Explorer target backend_settings must be an object")
        backend = entry.get("backend", base.backend)
        if not isinstance(backend, str):
            raise ValueError("Explorer target backend must be a string")
        revision = entry.get("revision")
        if revision is not None and (not isinstance(revision, str) or not revision.strip()):
            raise ValueError("Explorer target revision must be null or a nonempty string")
        retrieval = base.retrieval
        if "retrieval" in entry:
            if not isinstance(entry["retrieval"], dict):
                raise ValueError("Explorer target retrieval must be an object")
            retrieval = RetrievalConfig.from_mapping(entry["retrieval"])
        configured = dataclass_replace(
            base, backend=backend, backend_settings=settings,
            workspace=Path(workspace).resolve(strict=True), repository=repository,
            revision=revision, retrieval=retrieval, sources={},
        )
        targets[target_id] = ExplorerTarget(
            id=target_id, service=service_from_runtime_config(configured),
            repository=repository, backend=backend, revision=revision,
            collection=_collection(backend, settings),
            capabilities=BackendRegistry().create(backend).retrieval_capabilities.to_mapping(),
            retrieval=retrieval.to_mapping() if retrieval else None,
            runtime_config=configured,
        )
    return targets


def _available_targets(ctx: ExplorerContext) -> Mapping[str, ExplorerTarget]:
    if ctx.targets is not None:
        return ctx.targets
    return {"default": ExplorerTarget(
        "default", ctx.service, ctx.repository, ctx.backend, ctx.revision,
        None, ctx.capabilities, ctx.retrieval,
    )}


def _selected_target(ctx: ExplorerContext, target_id: str | None) -> ExplorerTarget:
    value = (target_id or "default").strip()
    selected = _available_targets(ctx).get(value)
    if selected is None:
        raise KnowledgeSearchError(
            "target is not configured or authorized", category="unauthorized_target",
        )
    return selected


class SearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    repository: str | None = None
    target_id: str | None = Field(default=None, min_length=1, max_length=64)
    limit: int = Field(default=10, ge=1, le=100)
    mode: Literal["discovery", "targeted", "repository_source"] = "discovery"
    knowledge_types: list[str] = Field(default_factory=list, max_length=30)
    intent: dict[str, Any]


class SourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_ref: str = Field(min_length=1, max_length=4096)
    target_id: str | None = Field(default=None, min_length=1, max_length=64)
    repository: str | None = None
    revision: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    context_lines: int = Field(default=4, ge=0, le=100)


def _explorer_runtime_config():
    """Default only this optional devtool to Solr; operator selections still win.

    A bare FetchCore runtime defaults to local-semantic. The Search Lab instead
    uses Solr, by explicitly selecting it before composing the runtime. We do
    not change FetchCore defaults, reconfigure explicit YAML/ENV choices, or
    replace an unavailable Solr backend with local scanning.
    """
    config = load_runtime_config()
    if config.sources.get("runtime.backend") in (None, "default"):
        return load_runtime_config(environ={**os.environ, "FETCHCORE_BACKEND": "solr"})
    return config


@lru_cache(maxsize=1)
def _runtime() -> ExplorerContext:
    config = _explorer_runtime_config()
    targets = _operator_targets(config)
    default = targets["default"]
    return ExplorerContext(
        service=default.service,
        repository=default.repository,
        backend=default.backend,
        revision=default.revision,
        capabilities=default.capabilities,
        retrieval=default.retrieval,
        targets=targets,
    )


def _error(exc: KnowledgeSearchError) -> HTTPException:
    status = 400
    if exc.category.startswith("unauthorized"):
        status = 403
    elif "mismatch" in exc.category or exc.category == "snapshot_revision_mismatch":
        status = 409
    return HTTPException(
        status_code=status,
        detail={"category": exc.category, "message": str(exc), "metadata": exc.metadata},
    )


class IndexArtifactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    artifacts: list[str] = Field(min_length=1, max_length=2)


def create_app(context: ExplorerContext | None = None) -> FastAPI:
    app = FastAPI(
        title="FetchCore Search Lab (local development only)",
        version="0.1",
        docs_url=None,
        redoc_url=None,
    )

    def resolved() -> ExplorerContext:
        return context if context is not None else _runtime()

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/indexing/artifacts")
    def indexing_artifacts(payload: IndexArtifactPayload) -> dict[str, object]:
        from fetchcore.application.index_artifacts import (
            compare_index_artifacts, load_index_artifact,
        )
        try:
            artifacts = [load_index_artifact(raw) for raw in payload.artifacts]
            identities = [(a.repository, a.backend, a.target, a.run_id) for a in artifacts]
            if len(set(identities)) != len(identities):
                raise ValueError("Duplicate run identity")
            return {"artifacts": [a.to_mapping() for a in artifacts],
                    "comparison": compare_index_artifacts(*artifacts) if len(artifacts) == 2 else None}
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={
                "category": "invalid_index_artifact", "message": str(exc),
            }) from exc

    @app.get("/api/config")
    def configuration() -> dict[str, object]:
        ctx = resolved()
        return {
            "repository": ctx.repository,
            "backend": ctx.backend,
            "revision": ctx.revision,
            "capabilities": ctx.capabilities,
            "retrieval": ctx.retrieval,
            "search_modes": ["discovery", "targeted", "repository_source"],
            "max_results": 100,
            "default_target": "default",
            "targets": [target.public() for target in _available_targets(ctx).values()],
        }

    @app.get("/api/readiness")
    def readiness(target_id: str = "default") -> dict[str, object]:
        """On-demand diagnostic: never mark service health as index readiness."""
        ctx = resolved()
        try:
            target = _selected_target(ctx, target_id)
        except KnowledgeSearchError as exc:
            raise _error(exc) from exc
        if target.runtime_config is None:
            raise HTTPException(
                status_code=409,
                detail={"category": "readiness_unavailable", "message": "Target has no runtime readiness binding"},
            )
        return RuntimeInspectionService(target.runtime_config).readiness()

    @app.post("/api/search")
    def search(payload: SearchPayload) -> dict[str, object]:
        ctx = resolved()
        try:
            target = _selected_target(ctx, payload.target_id)
            intent = SearchIntent.from_mapping(payload.intent)
            response = target.service.search(
                SearchRequest(
                    repository=payload.repository or target.repository,
                    limit=payload.limit,
                    mode=payload.mode,
                    knowledge_types=tuple(payload.knowledge_types),
                    intent=intent,
                )
            )
            return response.to_mapping()
        except KnowledgeSearchError as exc:
            raise _error(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"category": "schema_mismatch", "message": str(exc)},
            ) from exc

    @app.post("/api/source")
    def source(payload: SourcePayload) -> dict[str, object]:
        ctx = resolved()
        try:
            target = _selected_target(ctx, payload.target_id)
            result = target.service.get_source(
                SourceReadRequest(
                    source_ref=payload.source_ref,
                    repository=payload.repository or target.repository,
                    revision=payload.revision,
                    line_start=payload.line_start,
                    line_end=payload.line_end,
                    context_lines=payload.context_lines,
                )
            )
            return result.to_mapping()
        except KnowledgeSearchError as exc:
            raise _error(exc) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"category": "schema_mismatch", "message": str(exc)},
            ) from exc

    @app.exception_handler(HTTPException)
    async def http_error(_request: object, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        if not isinstance(detail, dict):
            detail = {"category": "http_error", "message": str(detail)}
        return JSONResponse(status_code=exc.status_code, content={"error": detail})

    return app


app = create_app()
