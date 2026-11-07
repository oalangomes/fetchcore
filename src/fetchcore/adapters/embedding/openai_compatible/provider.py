"""OpenAI-compatible embedding transport for external gateways.

LiteLLM and Bifrost expose /v1/embeddings; gateway selection is an
operator/runtime concern, not a retrieval backend concern.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fetchcore.retrieval.contracts import KnowledgeSearchError
from fetchcore.retrieval.embedding import validate_vector_dimensions


_RETRY_DELAYS = (0.25, 1.0)
_RETRYABLE_HTTP_STATUSES = frozenset({408, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class GatewayEmbeddingSettings:
    """Validated input for a named, OpenAI-compatible embedding gateway."""

    provider_id: str
    base_url: str
    model: str
    api_key: str | None
    allow_source_upload: bool
    timeout_seconds: float = 30.0
    request_batch_size: int = 32
    dimensions: int | None = None

    def __post_init__(self) -> None:
        if self.provider_id not in {"litellm", "bifrost"}:
            raise KnowledgeSearchError("unsupported embedding gateway", category="unsupported_capability")
        if not self.base_url.startswith(("http://", "https://")) or not self.base_url.endswith("/v1"):
            raise KnowledgeSearchError("embedding gateway must use an absolute /v1 URL", category="schema_mismatch")
        if not self.model.strip():
            raise KnowledgeSearchError("embedding gateway model must not be empty", category="schema_mismatch")
        if not self.allow_source_upload:
            raise KnowledgeSearchError(
                "external embedding gateway requires explicit source-upload opt-in",
                category="unsupported_capability",
                metadata={"capability": "embedding_source_upload"},
            )
        if self.timeout_seconds <= 0 or not math.isfinite(self.timeout_seconds):
            raise KnowledgeSearchError("invalid embedding gateway timeout", category="schema_mismatch")
        if self.request_batch_size < 1:
            raise KnowledgeSearchError("invalid embedding gateway batch size", category="schema_mismatch")
        if self.dimensions is not None and self.dimensions < 1:
            raise KnowledgeSearchError("invalid embedding gateway dimensions", category="schema_mismatch")


class GatewayEmbeddingProvider:
    """Bounded, fail-closed OpenAI embeddings adapter without SDK dependency."""

    def __init__(self, settings: GatewayEmbeddingSettings) -> None:
        self.settings = settings
        self.provider_id = settings.provider_id
        self.model_id = settings.model
        self.url = settings.base_url.rstrip("/") + "/embeddings"

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        vectors: list[tuple[float, ...]] = []
        for start in range(0, len(texts), self.settings.request_batch_size):
            batch = texts[start : start + self.settings.request_batch_size]
            vectors.extend(self._embed_batch(batch))
        result = tuple(vectors)
        if len(result) != len(texts):
            raise KnowledgeSearchError("embedding gateway returned an incomplete batch", category="embedding_failed")
        return result

    def _embed_batch(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        payload = json.dumps({
            "model": self.model_id,
            "input": list(texts),
            "encoding_format": "float",
        }).encode("utf-8")
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        request = Request(self.url, data=payload, headers=headers, method="POST")
        response_bytes: bytes | None = None
        for attempt, delay in enumerate((*_RETRY_DELAYS, None), start=1):
            try:
                with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                    response_bytes = response.read()
                break
            except HTTPError as exc:
                if delay is not None and exc.code in _RETRYABLE_HTTP_STATUSES:
                    sleep(delay)
                    continue
                raise KnowledgeSearchError(
                    "embedding gateway HTTP request failed",
                    category="embedding_failed",
                    metadata={"provider": self.provider_id, "http_status": exc.code, "attempt": attempt},
                ) from None
            except (TimeoutError, URLError, OSError) as exc:
                if delay is not None:
                    sleep(delay)
                    continue
                category = "timeout" if isinstance(exc, TimeoutError) else "unavailable"
                raise KnowledgeSearchError(
                    "embedding gateway connection failed",
                    category=category,
                    metadata={"provider": self.provider_id, "attempt": attempt},
                ) from None

        if response_bytes is None:
            raise KnowledgeSearchError("embedding gateway returned no response", category="embedding_failed")
        try:
            decoded = json.loads(response_bytes)
        except (ValueError, UnicodeError):
            raise KnowledgeSearchError("embedding gateway returned invalid JSON", category="response_parsing_failure") from None
        if not isinstance(decoded, dict) or not isinstance(decoded.get("data"), list):
            raise KnowledgeSearchError("embedding gateway returned invalid data", category="response_parsing_failure")
        rows = decoded["data"]
        if len(rows) != len(texts):
            raise KnowledgeSearchError("embedding gateway returned an incomplete batch", category="embedding_failed")
        ordered: list[tuple[float, ...] | None] = [None] * len(texts)
        for item in rows:
            if not isinstance(item, dict):
                raise KnowledgeSearchError("embedding gateway returned invalid vector", category="response_parsing_failure")
            index = item.get("index")
            embedding = item.get("embedding")
            if type(index) is not int or index < 0 or index >= len(texts) or ordered[index] is not None:
                raise KnowledgeSearchError("embedding gateway returned invalid vector index", category="response_parsing_failure")
            if not isinstance(embedding, list) or not embedding or any(
                type(number) not in (int, float) or not math.isfinite(number) for number in embedding
            ):
                raise KnowledgeSearchError("embedding gateway returned invalid vector values", category="response_parsing_failure")
            ordered[index] = tuple(float(number) for number in embedding)
        if any(item is None for item in ordered):
            raise KnowledgeSearchError("embedding gateway returned missing vector indexes", category="response_parsing_failure")
        vectors = tuple(item for item in ordered if item is not None)
        dimensions = validate_vector_dimensions(vectors)
        if self.settings.dimensions is not None and dimensions != self.settings.dimensions:
            raise KnowledgeSearchError(
                "embedding gateway vector dimensions mismatch",
                category="schema_mismatch",
                metadata={"expected_dimensions": self.settings.dimensions, "actual_dimensions": dimensions, "rebuild_required": True},
            )
        return vectors
