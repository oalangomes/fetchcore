"""Ollama HTTP implementation of the embedding provider port."""

from __future__ import annotations

import json
import math
import socket
from dataclasses import dataclass
from errno import (
    EAGAIN,
    ECONNABORTED,
    ECONNREFUSED,
    ECONNRESET,
    EHOSTUNREACH,
    ENETUNREACH,
    EPIPE,
    ETIMEDOUT,
)
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fetchcore.retrieval.contracts import KnowledgeSearchError

_OLLAMA_TRANSIENT_RETRY_DELAYS = (0.25, 1.0)
_TRANSIENT_CONNECTION_ERRNOS = frozenset(
    {
        EAGAIN,
        ECONNABORTED,
        ECONNREFUSED,
        ECONNRESET,
        EHOSTUNREACH,
        ENETUNREACH,
        EPIPE,
        ETIMEDOUT,
        socket.EAI_AGAIN,
    }
)


@dataclass(frozen=True)
class OllamaEmbeddingSettings:
    """Transport/model identity for the neutral Ollama embedding provider."""

    url: str = "http://localhost:11434"
    model: str = "embeddinggemma"
    timeout_seconds: float = 30.0
    request_batch_size: int = 32


class OllamaEmbeddingProvider:
    """Local Ollama embedding provider using POST /api/embed."""

    provider_id = "ollama"

    def __init__(
        self,
        settings: OllamaEmbeddingSettings | None = None,
        *,
        url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        request_batch_size: int | None = None,
    ) -> None:
        """Initialize OllamaEmbeddingProvider with its explicit dependencies."""
        resolved = settings or OllamaEmbeddingSettings()
        self.url = (url or resolved.url).rstrip("/")
        self.model_id = (model or resolved.model).strip()
        self.timeout_seconds = (
            resolved.timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        self.request_batch_size = (
            resolved.request_batch_size
            if request_batch_size is None
            else request_batch_size
        )
        if not self.model_id:
            raise KnowledgeSearchError(
                "embedding model must not be empty",
                category="schema_mismatch",
            )
        if self.timeout_seconds <= 0 or not math.isfinite(self.timeout_seconds):
            raise KnowledgeSearchError(
                "embedding timeout_seconds must be positive",
                category="schema_mismatch",
            )
        if self.request_batch_size < 1:
            raise KnowledgeSearchError(
                "embedding request_batch_size must be positive",
                category="schema_mismatch",
            )

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed the supplied texts using bounded transport batches while preserving order."""
        if not texts:
            return ()
        vectors: list[tuple[float, ...]] = []
        for offset in range(0, len(texts), self.request_batch_size):
            batch = texts[offset : offset + self.request_batch_size]
            vectors.extend(
                self._embed_batch(
                    batch,
                    batch_offset=offset,
                    total_input_count=len(texts),
                )
            )
        return tuple(vectors)

    def _embed_batch(
        self,
        texts: tuple[str, ...],
        *,
        batch_offset: int,
        total_input_count: int,
    ) -> tuple[tuple[float, ...], ...]:
        request = Request(
            f"{self.url}/api/embed",
            data=json.dumps(
                {
                    "model": self.model_id,
                    "input": list(texts),
                    "truncate": True,
                },
                separators=(",", ":"),
            ).encode("utf-8"),
            method="POST",
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        diagnostics = {
            "embedding_model": self.model_id,
            "input_count": len(texts),
            "batch_offset": batch_offset,
            "total_input_count": total_input_count,
            "max_input_chars": max((len(text) for text in texts), default=0),
            "total_input_chars": sum(len(text) for text in texts),
        }
        raw: bytes | None = None
        max_attempts = len(_OLLAMA_TRANSIENT_RETRY_DELAYS) + 1
        for attempt, retry_delay in enumerate(
            (*_OLLAMA_TRANSIENT_RETRY_DELAYS, None),
            start=1,
        ):
            try:
                with urlopen(request, timeout=self.timeout_seconds) as response:
                    raw = response.read()
            except HTTPError as exc:
                metadata: dict[str, object] = {
                    "http_status": exc.code,
                    "attempt": attempt,
                    "max_attempts": max_attempts,
                    **diagnostics,
                }
                provider_error = _ollama_http_error_message(exc)
                if provider_error is not None:
                    metadata["provider_error"] = provider_error
                transient_http_error = (
                    exc.code == 400
                    and _ollama_transient_embedding_error(provider_error)
                )
                if retry_delay is not None and transient_http_error:
                    sleep(retry_delay)
                    continue
                detail = f": {provider_error}" if provider_error is not None else ""
                raise KnowledgeSearchError(
                    f"embedding request failed with HTTP {exc.code}{detail}",
                    category="embedding_failed",
                    metadata={
                        **metadata,
                        "retries_exhausted": retry_delay is None and transient_http_error,
                    },
                ) from exc
            except (TimeoutError, URLError, ConnectionError) as exc:
                cause = exc.reason if isinstance(exc, URLError) else exc
                transient = _is_transient_transport_cause(cause)
                if transient and retry_delay is not None:
                    sleep(retry_delay)
                    continue
                category = "timeout" if isinstance(cause, TimeoutError) else "unavailable"
                cause_errno = getattr(cause, "errno", None)
                raise KnowledgeSearchError(
                    f"embedding request {category} after {attempt} attempt(s)",
                    category=category,
                    metadata={
                        **diagnostics,
                        "attempt": attempt,
                        "max_attempts": max_attempts,
                        "cause_type": type(cause).__name__,
                        "cause_errno": cause_errno if isinstance(cause_errno, int) else None,
                        "retries_exhausted": transient and retry_delay is None,
                    },
                ) from exc
            break
        assert raw is not None

        payload = _json_object(raw, source="embedding provider", operation="/api/embed")
        raw_embeddings = payload.get("embeddings")
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
            raise KnowledgeSearchError(
                "embedding provider returned an unexpected vector count",
                category="response_parsing_failure",
                metadata=diagnostics,
            )
        vectors: list[tuple[float, ...]] = []
        for raw_vector in raw_embeddings:
            if not isinstance(raw_vector, list):
                raise KnowledgeSearchError(
                    "embedding provider returned an invalid vector",
                    category="response_parsing_failure",
                    metadata=diagnostics,
                )
            vector = tuple(_finite_float(item, "embedding value") for item in raw_vector)
            if not vector:
                raise KnowledgeSearchError(
                    "embedding provider returned an empty vector",
                    category="response_parsing_failure",
                    metadata=diagnostics,
                )
            vectors.append(vector)
        return tuple(vectors)


def _ollama_http_error_message(exc: HTTPError) -> str | None:
    try:
        raw = exc.read(4096)
    except OSError:
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    value = payload.get("error")
    if not isinstance(value, str):
        return None
    message = value.strip()
    return message[:512] if message else None


def _is_transient_transport_cause(cause: object) -> bool:
    """Retry only known temporary socket failures, including wrapped URLError reasons."""
    return (
        isinstance(cause, (TimeoutError, ConnectionError))
        or isinstance(cause, OSError)
        and cause.errno in _TRANSIENT_CONNECTION_ERRNOS
    )


def _ollama_transient_embedding_error(message: str | None) -> bool:
    """Return whether an Ollama HTTP 400 describes a transient local transport failure."""

    if message is None:
        return False

    normalized = message.casefold()

    if "health resp:" in normalized:
        return True

    if "do embedding request:" in normalized and (
        "eof" in normalized or "connect" in normalized
    ):
        return True

    return (
        "dial tcp 127.0.0.1:" in normalized
        and "connectex:" in normalized
        and "/tokenize" in normalized
    )


def _json_object(raw: bytes, *, source: str, operation: str) -> dict[str, object]:
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KnowledgeSearchError(
            f"{source} returned invalid JSON",
            category="response_parsing_failure",
            metadata={"operation": operation},
        ) from exc
    if not isinstance(value, dict):
        raise KnowledgeSearchError(
            f"{source} returned an unexpected response shape",
            category="response_parsing_failure",
            metadata={"operation": operation},
        )
    return {str(key): item for key, item in value.items()}


def _finite_float(value: object, name: str) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError) as exc:
        raise KnowledgeSearchError(
            f"{name} must be numeric",
            category="response_parsing_failure",
        ) from exc
    if not math.isfinite(result):
        raise KnowledgeSearchError(
            f"{name} must be finite",
            category="response_parsing_failure",
        )
    return result




# The original public import and Python class identity remain valid.
OllamaEmbeddingSettings.__module__ = "fetchcore.embedding"
OllamaEmbeddingProvider.__module__ = "fetchcore.embedding"
