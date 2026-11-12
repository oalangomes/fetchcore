"""SolrCloud collection and alias lifecycle for repository index generations."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import re
from typing import Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from fetchcore.adapters.retrieval.solr.backend import (
    SolrIndexSnapshot,
    SolrIndexStore,
    SolrIndexSyncResult,
    SolrSettings,
    corpus_checksum,
)
from fetchcore.adapters.retrieval.solr.schema_validation import (
    validate_projection_v3_schema,
)
from fetchcore.repository.indexing import RepositoryIndexResult
from fetchcore.retrieval.contracts import KnowledgeSearchError


_NAME_PATTERN = re.compile(r"[A-Za-z0-9_.-]+")
_LOCK_RECORD_ID = "fetchcore-writer-lock"


class SolrCloudCollectionsApi(Protocol):
    """Narrow Collections API seam used by generation publication."""

    def list_collections(self) -> tuple[str, ...]: ...

    def list_aliases(self) -> Mapping[str, object]: ...

    def collection_configset(self, collection: str) -> str: ...

    def create_collection(self, collection: str, configset: str) -> None: ...

    def set_alias(self, alias: str, collection: str) -> None: ...

    def delete_alias(self, alias: str) -> None: ...

    def delete_collection(self, collection: str) -> None: ...


class UrllibSolrCloudCollectionsApi:
    """Dependency-free SolrCloud Collections API client."""

    def __init__(self, settings: SolrSettings) -> None:
        self.settings = settings
        self.endpoint = f"{settings.url}/admin/collections"

    def list_collections(self) -> tuple[str, ...]:
        value = self._request("LIST")
        collections = value.get("collections")
        if not isinstance(collections, list) or not all(
            isinstance(name, str) for name in collections
        ):
            raise KnowledgeSearchError(
                "SolrCloud returned an invalid collection list",
                category="response_parsing_failure",
            )
        return tuple(collections)

    def list_aliases(self) -> Mapping[str, object]:
        value = self._request("LISTALIASES")
        aliases = value.get("aliases")
        if not isinstance(aliases, dict) or not all(isinstance(name, str) for name in aliases):
            raise KnowledgeSearchError(
                "SolrCloud returned an invalid alias list",
                category="response_parsing_failure",
            )
        return cast(Mapping[str, object], aliases)

    def collection_configset(self, collection: str) -> str:
        value = self._request("CLUSTERSTATUS", collection=collection)
        cluster = value.get("cluster")
        collections = cluster.get("collections") if isinstance(cluster, dict) else None
        details = collections.get(collection) if isinstance(collections, dict) else None
        configset = details.get("configName") if isinstance(details, dict) else None
        if not isinstance(configset, str) or not configset:
            raise KnowledgeSearchError(
                "SolrCloud collection configset could not be verified",
                category="schema_mismatch",
                metadata={"collection": collection},
            )
        return configset

    def create_collection(self, collection: str, configset: str) -> None:
        self._request(
            "CREATE",
            name=collection,
            **{
                "collection.configName": configset,
                "numShards": "1",
                "replicationFactor": "1",
            },
        )

    def set_alias(self, alias: str, collection: str) -> None:
        self._request("CREATEALIAS", name=alias, collections=collection)

    def delete_alias(self, alias: str) -> None:
        self._request("DELETEALIAS", name=alias)

    def delete_collection(self, collection: str) -> None:
        self._request("DELETE", name=collection)

    def _request(self, action: str, **params: str) -> dict[str, object]:
        query = urlencode({"action": action, "wt": "json", **params})
        request = Request(f"{self.endpoint}?{query}", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.settings.timeout_seconds) as response:
                raw = response.read()
        except HTTPError as exc:
            category = "schema_mismatch" if exc.code in {400, 409, 422} else "unavailable"
            raise KnowledgeSearchError(
                f"SolrCloud Collections API failed with HTTP {exc.code}",
                category=category,
                metadata={"http_status": exc.code, "action": action},
            ) from exc
        except TimeoutError as exc:
            raise KnowledgeSearchError(
                "SolrCloud Collections API timed out",
                category="timeout",
                metadata={"action": action},
            ) from exc
        except URLError as exc:
            raise KnowledgeSearchError(
                "SolrCloud is unavailable",
                category="unavailable",
                metadata={"action": action},
            ) from exc
        try:
            value = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise KnowledgeSearchError(
                "SolrCloud returned invalid JSON",
                category="response_parsing_failure",
                metadata={"action": action},
            ) from exc
        if not isinstance(value, dict):
            raise KnowledgeSearchError(
                "SolrCloud returned an unexpected response",
                category="response_parsing_failure",
                metadata={"action": action},
            )
        header = value.get("responseHeader")
        if isinstance(header, dict) and header.get("status") not in (None, 0):
            raise KnowledgeSearchError(
                "SolrCloud Collections API rejected the request",
                category="schema_mismatch",
                metadata={"action": action, "status": header.get("status")},
            )
        return cast(dict[str, object], value)


@dataclass(frozen=True)
class SolrCloudGeneration:
    alias: str
    collection: str
    snapshot: SolrIndexSnapshot | None


@dataclass(frozen=True)
class SolrCloudPublication:
    storage: SolrIndexSyncResult
    previous_collection: str
    active_collection: str


class SolrCloudGenerationManager:
    """Build and publish immutable SolrCloud generations behind one alias."""

    def __init__(
        self,
        settings: SolrSettings,
        *,
        api: SolrCloudCollectionsApi | None = None,
        store_factory: Callable[[str], SolrIndexStore],
    ) -> None:
        if settings.deployment_mode != "solrcloud":
            raise KnowledgeSearchError(
                "Versioned Solr generations require an explicit SolrCloud deployment mode",
                category="deployment_mode_mismatch",
            )
        self.settings = settings
        self.api = api or UrllibSolrCloudCollectionsApi(settings)
        self.store_factory = store_factory

    def active_collection(self) -> str:
        aliases = self.api.list_aliases()
        targets = _alias_targets(aliases.get(self.settings.collection))
        if len(targets) != 1:
            raise KnowledgeSearchError(
                "SolrCloud knowledge alias must resolve to exactly one collection",
                category="repository_resource_collision",
                metadata={"alias": self.settings.collection, "targets": targets},
            )
        return targets[0]

    @contextmanager
    def writer_lock(self, repository: str) -> Iterator[None]:
        """Use an atomically-created Cloud collection as a fail-closed writer lock."""
        lock_name = _lock_collection(self.settings.collection, repository)
        token = uuid4().hex
        try:
            self.api.create_collection(lock_name, self.settings.configset)
        except KnowledgeSearchError as exc:
            if lock_name in self.api.list_collections():
                raise KnowledgeSearchError(
                    "A SolrCloud writer lock already exists; inspect it before recovery",
                    category="concurrent_write",
                    metadata={
                        "repository": repository,
                        "lock_collection": lock_name,
                        "recovery_required": True,
                    },
                ) from exc
            raise

        lock_store = self.store_factory(lock_name)
        marker = {
            "id": _LOCK_RECORD_ID,
            "record_kind": "writer_lock",
            "repository": repository,
            "canonical_payload": json.dumps(
                {"token": token, "alias": self.settings.collection},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        try:
            lock_store.transport.post("update", [marker], {"commit": "true", "wt": "json"})
        except Exception as exc:
            raise KnowledgeSearchError(
                "SolrCloud writer lock ownership could not be recorded",
                category="concurrent_write",
                metadata={
                    "repository": repository,
                    "lock_collection": lock_name,
                    "recovery_required": True,
                },
            ) from exc
        try:
            yield
        finally:
            try:
                self._release_lock(lock_store, lock_name, repository, token)
            except Exception as exc:
                raise KnowledgeSearchError(
                    "SolrCloud writer lock ownership could not be verified for release",
                    category="concurrent_write",
                    metadata={
                        "repository": repository,
                        "lock_collection": lock_name,
                        "recovery_required": True,
                    },
                ) from exc

    def rebuild(self, result: RepositoryIndexResult) -> SolrCloudPublication:
        """Build a fresh generation, validate it, and switch the stable alias."""
        alias = self.settings.collection
        repository = result.repository
        with self.writer_lock(repository):
            prior_name = self.active_collection()
            prior = self._inspect_generation(prior_name, repository)
            generation_name = _generation_collection(alias)
            if generation_name in self.api.list_collections():
                raise KnowledgeSearchError(
                    "Generated SolrCloud collection name already exists",
                    category="repository_resource_collision",
                    metadata={"collection": generation_name},
                )
            try:
                self.api.create_collection(generation_name, self.settings.configset)
            except Exception as exc:
                raise KnowledgeSearchError(
                    "SolrCloud staging collection creation failed; inspect cluster state",
                    category="index_update_failure",
                    metadata={
                        "collection": generation_name,
                        "activation": "not_attempted",
                        "recovery_required": True,
                    },
                ) from exc

            staged = self.store_factory(generation_name)
            staged.validate_schema(require_vectors=staged.embedding_provider is not None)
            if self.api.collection_configset(generation_name) != self.settings.configset:
                raise KnowledgeSearchError(
                    "Staging collection does not use the requested FetchCore configset",
                    category="schema_mismatch",
                    metadata={"collection": generation_name},
                )
            storage = staged.full_rebuild(result)
            expected = self._inspect_generation(generation_name, repository)
            if (
                expected.snapshot is None
                or expected.snapshot.revision != result.revision
                or expected.snapshot.document_count != len(result.documents)
                or expected.snapshot.corpus_checksum != corpus_checksum(result.documents)
            ):
                raise KnowledgeSearchError(
                    "Staging generation snapshot does not match canonical repository knowledge",
                    category="index_update_failure",
                    metadata={
                        "collection": generation_name,
                        "activation": "not_attempted",
                        "recovery_required": True,
                    },
                )

            if self.active_collection() != prior_name:
                raise KnowledgeSearchError(
                    "SolrCloud alias changed while the generation was being built",
                    category="concurrent_write",
                    metadata={"alias": alias, "recovery_required": True},
                )
            try:
                self.api.set_alias(alias, generation_name)
            except Exception as exc:
                observed = self._alias_target_after_error()
                if observed != generation_name:
                    if observed == prior_name:
                        raise KnowledgeSearchError(
                            "SolrCloud alias promotion did not take effect",
                            category="index_update_failure",
                            metadata={"activation": "not_confirmed"},
                        ) from exc
                    raise KnowledgeSearchError(
                        "SolrCloud alias promotion outcome is indeterminate",
                        category="concurrent_write",
                        metadata={
                            "alias": alias,
                            "observed_target": observed,
                            "recovery_required": True,
                        },
                    ) from exc

            observed = self.active_collection()
            if observed != generation_name:
                if observed == prior_name:
                    raise KnowledgeSearchError(
                        "SolrCloud alias promotion was not confirmed",
                        category="index_update_failure",
                        metadata={"activation": "not_confirmed"},
                    )
                raise KnowledgeSearchError(
                    "SolrCloud alias changed to an unexpected generation",
                    category="concurrent_write",
                    metadata={
                        "alias": alias,
                        "observed_target": observed,
                        "recovery_required": True,
                    },
                )
            try:
                active = self._inspect_generation(alias, repository)
                if active.snapshot != expected.snapshot:
                    raise KnowledgeSearchError(
                        "Published SolrCloud snapshot differs from the validated generation",
                        category="index_update_failure",
                    )
                return SolrCloudPublication(
                    storage=storage,
                    previous_collection=prior_name,
                    active_collection=generation_name,
                )
            except Exception as exc:
                self._restore_prior_alias(prior_name, generation_name, prior, repository, exc)
                raise

    def require_active_generation(self, repository: str) -> str:
        """Read and validate the currently aliased generation before incremental writes."""
        generation = self.active_collection()
        self._inspect_generation(generation, repository)
        return generation

    def reconciliation_report(self, repository: str) -> dict[str, object]:
        """Inspect active and retained generations without changing cluster state."""
        active = self.active_collection()
        generation = self._inspect_generation(active, repository)
        collections = self.api.list_collections()
        prefix = _generation_prefix(self.settings.collection)
        retained = sorted(
            name for name in collections if name.startswith(prefix) and name != active
        )
        lock_name = _lock_collection(self.settings.collection, repository)
        lock_present = lock_name in collections
        snapshot = generation.snapshot
        return {
            "action": "recovery_required" if lock_present else "none",
            "ready": snapshot is not None,
            "alias": self.settings.collection,
            "active_collection": active,
            "retained_generations": retained,
            "snapshot": (
                None
                if snapshot is None
                else {
                    "repository": snapshot.repository,
                    "revision": snapshot.revision,
                    "document_count": snapshot.document_count,
                    "corpus_checksum": snapshot.corpus_checksum,
                    "projection_fingerprint": snapshot.projection_fingerprint,
                }
            ),
            "writer_lock": {
                "present": lock_present,
                "collection": lock_name if lock_present else None,
                "recovery_required": lock_present,
            },
            "mutated": False,
        }

    def assert_active_generation(self, expected: str) -> None:
        observed = self.active_collection()
        if observed != expected:
            raise KnowledgeSearchError(
                "SolrCloud active alias changed during repository synchronization",
                category="concurrent_write",
                metadata={
                    "alias": self.settings.collection,
                    "expected_target": expected,
                    "observed_target": observed,
                },
            )

    def _inspect_generation(self, collection: str, repository: str) -> SolrCloudGeneration:
        physical_collection = (
            self.active_collection()
            if collection == self.settings.collection
            else collection
        )
        if self.api.collection_configset(physical_collection) != self.settings.configset:
            raise KnowledgeSearchError(
                "SolrCloud generation has an unexpected configset",
                category="schema_mismatch",
                metadata={"collection": physical_collection},
            )
        store = self.store_factory(collection)
        store.validate_schema(require_vectors=store.embedding_provider is not None)
        validate_projection_v3_schema(
            store.transport.get("schema", {"wt": "json"}).get("schema")
        )
        total = store._select({"q": "*:*", "rows": 0})["num_found"]
        try:
            snapshot = store.load_snapshot(repository)
        except KnowledgeSearchError as exc:
            if exc.category != "snapshot_missing" or total != 0:
                raise KnowledgeSearchError(
                    "SolrCloud generation ownership cannot be verified",
                    category="repository_resource_collision",
                    metadata={"collection": collection, "document_count": total},
                ) from exc
            return SolrCloudGeneration(self.settings.collection, collection, None)
        documents = store.load_documents(repository)
        if (
            len(documents) != snapshot.document_count
            or corpus_checksum(documents) != snapshot.corpus_checksum
            or total != len(documents) + 1
        ):
            raise KnowledgeSearchError(
                "SolrCloud generation snapshot does not own its complete repository corpus",
                category="repository_resource_collision",
                metadata={
                    "collection": collection,
                    "snapshot_documents": snapshot.document_count,
                    "loaded_documents": len(documents),
                    "collection_documents": total,
                },
            )
        return SolrCloudGeneration(self.settings.collection, collection, snapshot)

    def _restore_prior_alias(
        self,
        prior_name: str,
        failed_name: str,
        prior: SolrCloudGeneration,
        repository: str,
        cause: Exception,
    ) -> None:
        try:
            if self.active_collection() != failed_name:
                raise KnowledgeSearchError(
                    "SolrCloud alias changed before rollback",
                    category="concurrent_write",
                )
            verified_prior = self._inspect_generation(prior_name, repository)
            if verified_prior.snapshot != prior.snapshot:
                raise KnowledgeSearchError(
                    "Previous SolrCloud generation identity changed before rollback",
                    category="snapshot_revision_mismatch",
                )
            self.api.set_alias(self.settings.collection, prior_name)
            if self.active_collection() != prior_name:
                raise KnowledgeSearchError(
                    "SolrCloud alias rollback was not confirmed",
                    category="index_update_failure",
                )
        except Exception as rollback_error:
            raise KnowledgeSearchError(
                "SolrCloud publication failed and the previous generation could not be restored",
                category="concurrent_write",
                metadata={
                    "alias": self.settings.collection,
                    "previous_collection": prior_name,
                    "failed_collection": failed_name,
                    "recovery_required": True,
                },
            ) from rollback_error
        raise KnowledgeSearchError(
            "SolrCloud publication failed; the verified previous generation was restored",
            category="index_update_failure",
            metadata={
                "alias": self.settings.collection,
                "active_collection": prior_name,
                "recovery_required": False,
            },
        ) from cause

    def _alias_target_after_error(self) -> str | None:
        try:
            return self.active_collection()
        except KnowledgeSearchError:
            return None

    def _release_lock(
        self,
        lock_store: SolrIndexStore,
        lock_name: str,
        repository: str,
        token: str,
    ) -> None:
        selected = lock_store._select({
                "q": f'id:"{_LOCK_RECORD_ID}"',
                "rows": 2,
                "fl": "id,record_kind,repository,canonical_payload",
                "wt": "json",
            })
        records = selected["docs"]
        if len(records) != 1:
            raise KnowledgeSearchError(
                "SolrCloud writer lock record is missing or ambiguous",
                category="concurrent_write",
            )
        record = records[0]
        try:
            payload = json.loads(cast(str, record["canonical_payload"]))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise KnowledgeSearchError(
                "SolrCloud writer lock ownership record is invalid",
                category="concurrent_write",
            ) from exc
        if (
            record.get("record_kind") != "writer_lock"
            or record.get("repository") != repository
            or not isinstance(payload, dict)
            or payload.get("token") != token
            or payload.get("alias") != self.settings.collection
        ):
            raise KnowledgeSearchError(
                "SolrCloud writer lock ownership changed",
                category="concurrent_write",
            )
        try:
            self.api.delete_collection(lock_name)
        except KnowledgeSearchError:
            if lock_name in self.api.list_collections():
                raise


def _alias_targets(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        targets = tuple(target.strip() for target in value.split(",") if target.strip())
    elif isinstance(value, list) and all(isinstance(target, str) for target in value):
        targets = tuple(value)
    else:
        targets = ()
    if not all(_NAME_PATTERN.fullmatch(target) for target in targets):
        return ()
    return targets


def _lock_collection(alias: str, repository: str) -> str:
    digest = hashlib.sha256(f"{alias}\0{repository}".encode()).hexdigest()[:20]
    return f"fetchcore-lock-{digest}"


def _generation_collection(alias: str) -> str:
    suffix = uuid4().hex[:12]
    return f"{_generation_prefix(alias)}{suffix}"


def _generation_prefix(alias: str) -> str:
    digest = hashlib.sha256(alias.encode()).hexdigest()[:10]
    return f"fcg-{digest}-"
