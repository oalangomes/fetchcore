"""Optional PyMongo driver and a small injectable adapter-internal transport."""

from __future__ import annotations

import importlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Protocol, cast

from fetchcore.retrieval.contracts import KnowledgeSearchError

from .settings import MongoDBSettings


class MongoDBTransport(Protocol):
    def health(self) -> None: ...
    def find(self, query: Mapping[str, object]) -> tuple[dict[str, object], ...]: ...
    def aggregate(self, pipeline: list[dict[str, object]]) -> tuple[dict[str, object], ...]: ...
    def replace(self, record: dict[str, object], expected: Mapping[str, object] | None) -> bool: ...
    def delete(self, query: Mapping[str, object]) -> int: ...
    def indexes(self) -> tuple[dict[str, object], ...]: ...
    def create_index(self, definition: dict[str, object]) -> None: ...
    def close(self) -> None: ...


def _records(raw: object) -> tuple[dict[str, object], ...]:
    if not isinstance(raw, (list, tuple)) or any(
        not isinstance(item, dict) or any(not isinstance(key, str) for key in item) for item in raw
    ):
        raise KnowledgeSearchError(
            "MongoDB returned malformed records", category="response_parsing_failure"
        )
    return cast(tuple[dict[str, object], ...], tuple(raw))


class PyMongoTransport:
    def __init__(self, settings: MongoDBSettings) -> None:
        try:
            driver = importlib.import_module("pymongo")
            self.errors = importlib.import_module("pymongo.errors")
        except ModuleNotFoundError as exc:
            if exc.name != "pymongo":
                raise
            raise KnowledgeSearchError(
                "Install fetchcore[mongodb] to use MongoDB Search",
                category="unsupported_capability",
            ) from exc
        self.settings = settings
        with self._errors():
            self.client = driver.MongoClient(
                settings.uri, connect=False,
                serverSelectionTimeoutMS=int(settings.timeout_seconds * 1000),
                connectTimeoutMS=int(settings.timeout_seconds * 1000),
                socketTimeoutMS=int(settings.timeout_seconds * 1000),
                timeoutMS=int(settings.timeout_seconds * 1000),
            )
            self.collection = self.client[settings.database][settings.collection]
            if (
                not self.collection.write_concern.acknowledged
                or self.collection.read_preference != driver.ReadPreference.PRIMARY
            ):
                self.client.close()
                raise KnowledgeSearchError(
                    "MongoDB snapshots require acknowledged writes and primary reads",
                    category="schema_mismatch",
                )

    @contextmanager
    def _errors(self) -> Iterator[None]:
        try:
            yield
        except self.errors.DuplicateKeyError as exc:
            raise KnowledgeSearchError(
                "MongoDB concurrent write", category="concurrent_write"
            ) from exc
        except self.errors.PyMongoError as exc:
            code = getattr(exc, "code", None)
            category = (
                "authentication_failed" if code in {13, 18}
                else "index_missing" if code in {26, 27}
                else "timeout" if getattr(exc, "timeout", False)
                else "unavailable" if isinstance(exc, self.errors.ConnectionFailure)
                else "schema_mismatch"
            )
            raise KnowledgeSearchError(
                "MongoDB operation failed", category=category,
                metadata={"code": code},
            ) from exc

    def health(self) -> None:
        with self._errors():
            self.client.admin.command("ping")

    def find(self, query: Mapping[str, object]) -> tuple[dict[str, object], ...]:
        with self._errors():
            return _records(list(self.collection.find(dict(query))))

    def aggregate(self, pipeline: list[dict[str, object]]) -> tuple[dict[str, object], ...]:
        with self._errors():
            return _records(list(self.collection.aggregate(
                pipeline, maxTimeMS=int(self.settings.timeout_seconds * 1000),
            )))

    def replace(self, record: dict[str, object], expected: Mapping[str, object] | None) -> bool:
        with self._errors():
            if expected is None:
                self.collection.replace_one({"_id": record["_id"]}, record, upsert=True)
                return True
            if expected.get("_absent"):
                self.collection.insert_one(record)
                return True
            result = self.collection.replace_one(dict(expected), record)
            return bool(result.matched_count == 1)

    def delete(self, query: Mapping[str, object]) -> int:
        with self._errors():
            return int(self.collection.delete_many(dict(query)).deleted_count)

    def indexes(self) -> tuple[dict[str, object], ...]:
        with self._errors():
            return _records(list(self.collection.list_search_indexes()))

    def create_index(self, definition: dict[str, object]) -> None:
        with self._errors():
            self.collection.create_index([("repository", 1), ("record_kind", 1)])
            self.collection.create_search_index(definition)

    def close(self) -> None:
        self.client.close()
