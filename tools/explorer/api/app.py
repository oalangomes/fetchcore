"""Local developer-only HTTP facade over the existing FetchCore application service.

No backend, score, authorization or retrieval semantics live in this module.
Launch on 127.0.0.1; never publish directly to an untrusted network.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from fetchcore.application import (
    BackendRegistry,
    RetrievalApplicationService,
    SearchRequest,
    SourceReadRequest,
    load_runtime_config,
    service_from_runtime_config,
)
from fetchcore.retrieval.contracts import KnowledgeSearchError, SearchIntent


@dataclass(frozen=True)
class ExplorerContext:
    service: RetrievalApplicationService
    repository: str
    backend: str
    revision: str | None
    capabilities: dict[str, bool]
    retrieval: dict[str, object] | None


class SearchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    repository: str | None = None
    limit: int = Field(default=10, ge=1, le=100)
    mode: Literal["discovery", "targeted", "repository_source"] = "discovery"
    knowledge_types: list[str] = Field(default_factory=list, max_length=30)
    intent: dict[str, Any]


class SourcePayload(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_ref: str = Field(min_length=1, max_length=4096)
    repository: str | None = None
    revision: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    context_lines: int = Field(default=4, ge=0, le=100)


@lru_cache(maxsize=1)
def _runtime() -> ExplorerContext:
    config = load_runtime_config()
    service = service_from_runtime_config(config)
    capabilities = BackendRegistry().create(config.backend).retrieval_capabilities.to_mapping()
    return ExplorerContext(
        service=service,
        repository=config.repository or config.workspace.name,
        backend=config.backend,
        revision=config.revision,
        capabilities=capabilities,
        retrieval=config.retrieval.to_mapping() if config.retrieval else None,
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
        }

    @app.post("/api/search")
    def search(payload: SearchPayload) -> dict[str, object]:
        ctx = resolved()
        try:
            intent = SearchIntent.from_mapping(payload.intent)
            response = ctx.service.search(
                SearchRequest(
                    repository=payload.repository,
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
            result = ctx.service.get_source(
                SourceReadRequest(
                    source_ref=payload.source_ref,
                    repository=payload.repository,
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
