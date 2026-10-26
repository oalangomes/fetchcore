"""Driver-boundary validation without connecting to a MongoDB server."""

from __future__ import annotations

import pytest

from fetchcore.adapters.retrieval.mongodb.settings import MongoDBSettings
from fetchcore.adapters.retrieval.mongodb.transport import PyMongoTransport, _records
from fetchcore.retrieval.contracts import KnowledgeSearchError


@pytest.mark.parametrize("uri", [
    "mongodb://localhost:27017/?w=0",
    "mongodb://localhost:27017/?readPreference=secondaryPreferred",
])
def test_unsafe_persistence_options_fail_before_network(uri: str) -> None:
    pytest.importorskip("pymongo")
    with pytest.raises(KnowledgeSearchError, match="acknowledged writes and primary reads"):
        PyMongoTransport(MongoDBSettings(uri=uri))


@pytest.mark.parametrize("code,category", [
    (13, "authentication_failed"), (18, "authentication_failed"),
    (26, "index_missing"), (27, "index_missing"), (2, "schema_mismatch"),
])
def test_vendor_errors_are_typed_and_redacted(code: int, category: str) -> None:
    driver = pytest.importorskip("pymongo")
    transport = PyMongoTransport(MongoDBSettings())
    try:
        with pytest.raises(KnowledgeSearchError) as captured, transport._errors():
            raise driver.errors.OperationFailure("private-secret-vendor-message", code=code)
        assert captured.value.category == category
        assert "private-secret" not in str(captured.value)
        assert captured.value.metadata == {"code": code}
    finally:
        transport.close()


@pytest.mark.parametrize("payload", [{}, [None], [{"valid": 1}, {1: "bad"}]])
def test_malformed_driver_records_are_rejected(payload: object) -> None:
    with pytest.raises(KnowledgeSearchError) as captured:
        _records(payload)
    assert captured.value.category == "response_parsing_failure"
