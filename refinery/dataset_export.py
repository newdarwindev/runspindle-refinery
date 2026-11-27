"""One-item synthetic-testable content export using existing Store immutable records.

No HTTP implementation, credentials, dataset creation, archive, delete or services.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote
from weakref import WeakKeyDictionary

from . import dataset_contract as c
from .dataset_materialization import Authority, DatasetRegistry
from .feedback_projection import RetainedSource
from .langfuse_http import validate_origin
from .langfuse_projection import HTTP, Response
from .materialized_use import BoundBatch
from .readonly_view import ReadView, identity, schema
from .storage import Store

MAX_BYTES = 262144
PREFIX = "dataset_export_"
_BINDINGS: WeakKeyDictionary[Any, tuple[Any, ...]] = WeakKeyDictionary()
_VIEWS: WeakKeyDictionary[Any, tuple[ReadView, ...]] = WeakKeyDictionary()


def safe_content(value: Any) -> None:
    c.finite_json(value)

    def visit(v: Any) -> None:
        if type(v) is str:
            if (
                any(unicodedata.category(ch) in {"Cc", "Cs"} for ch in v)
                or "@@@langfuseMedia:" in v
            ):
                raise ValueError("remote normalization/media unsupported")
        elif type(v) is dict:
            for key, child in v.items():
                visit(key)
                visit(child)
        elif type(v) is list:
            for child in v:
                visit(child)

    visit(value)


@dataclass(frozen=True)
class Destination:
    origin: str
    project_id: str
    dataset_id: str
    dataset_name: str
    split: str

    def __post_init__(self) -> None:
        validate_origin(self.origin, None)
        for v in (self.project_id, self.dataset_id, self.dataset_name):
            if type(v) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", v):
                raise ValueError("bounded explicit dataset destination required")
        if len(self.dataset_name) > 64:
            raise ValueError("bounded opaque dataset name required")
        if self.split not in {"train", "heldout"}:
            raise ValueError("explicit destination split required")

    def value(self) -> dict[str, Any]:
        self.__post_init__()
        return dict(
            origin=self.origin,
            project_id=self.project_id,
            dataset_id=self.dataset_id,
            dataset_name=self.dataset_name,
            split=self.split,
            status="ACTIVE",
        )


@dataclass(frozen=True)
class DatasetProjection:
    http: HTTP
    destination: Destination

    def _request(
        self, method: str, path: str, body: bytes | None = None
    ) -> tuple[int, Any]:
        self.destination.__post_init__()
        if self.http.origin != self.destination.origin:
            raise ValueError("dataset transport origin changed")
        result = self.http.request(method, path, body, timeout=10, max_bytes=MAX_BYTES)
        if self.http.origin != self.destination.origin:
            raise ValueError("dataset transport origin changed")
        if (
            type(result) is not Response
            or type(result.status) is not int
            or type(result.body) is not bytes
            or len(result.body) > MAX_BYTES
        ):
            raise ValueError("invalid bounded dataset response")
        if (
            result.status == 404
            and method == "GET"
            and path.startswith("/api/public/dataset-items/")
        ):
            return (
                404,
                None,
            )  # Not visible, NEVER proof a previous attempt did not happen.
        if result.status != 200:
            raise ValueError("dataset request unconfirmed")

        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for key, value in items:
                if key in out:
                    raise ValueError("duplicate dataset response field")
                out[key] = value
            return out

        value = json.loads(result.body, object_pairs_hook=pairs)
        c.finite_json(value)
        return result.status, value

    def verify_dataset(self) -> None:
        _, value = self._request(
            "GET",
            "/api/public/v2/datasets/" + quote(self.destination.dataset_name, safe=""),
        )
        if type(value) is not dict or (
            value.get("projectId"),
            value.get("id"),
            value.get("name"),
        ) != (
            self.destination.project_id,
            self.destination.dataset_id,
            self.destination.dataset_name,
        ):
            raise ValueError("existing dataset project/id/name mismatch")
        if (
            type(value.get("metadata")) is not dict
            or value["metadata"].get("runspindle_dataset_split")
            != self.destination.split
        ):
            raise ValueError("existing dataset split binding missing or changed")

    def read(self, expected: dict[str, Any]) -> bool:
        self.verify_dataset()
        status, value = self._request(
            "GET", "/api/public/dataset-items/" + expected["id"]
        )
        if status == 404:
            return False
        if (
            type(value) is not dict
            or value.get("datasetId") != self.destination.dataset_id
            or value.get("datasetName") != self.destination.dataset_name
        ):
            raise ValueError("dataset item association mismatch")
        for key in ("id", "status", "input", "expectedOutput", "metadata"):
            if key not in value or c.canonical(value[key]) != c.canonical(
                expected[key]
            ):
                raise ValueError("dataset item readback conflict")
        if (
            "sourceTraceId" not in value
            or value["sourceTraceId"] is not None
            or "sourceObservationId" not in value
            or value["sourceObservationId"] is not None
            or value.get("mediaReferences") != []
        ):
            raise ValueError("unapproved remote associations/media")
        return True

    def put(self, body: dict[str, Any]) -> None:
        raw = c.canonical(body)
        if len(raw) > MAX_BYTES:
            raise ValueError("dataset body bound")
        _, value = self._request("POST", "/api/public/dataset-items", raw)
        if type(value) is not dict or value.get("id") != body["id"]:
            raise ValueError("dataset write acceptance unconfirmed")


class Export:
    """Typed records on source's existing Store, not a separate coordinator."""

    def __init__(
        self,
        store: Store,
        registry: DatasetRegistry,
        source: RetainedSource,
        item_id: str,
        destination: Destination,
        *,
        approve: Authority,
        permission_ref: str,
        checkpoint: Callable[[str], None] = lambda _: None,
    ):
        if (
            type(store) is not Store
            or type(registry) is not DatasetRegistry
            or type(source) is not RetainedSource
            or source.store is not store
            or type(destination) is not Destination
        ):
            raise ValueError("exact local dataset export context required")
        if type(item_id) is not str or not re.fullmatch("[0-9a-f]{64}", item_id):
            raise ValueError("invalid materialized item id")
        self.store: Any = store
        self.registry, self.source = registry, source
        self.item_id, self.destination = item_id, destination
        self.approve, self.permission_ref, self.checkpoint = (
            approve,
            permission_ref,
            checkpoint,
        )
        self.destination_bytes = c.canonical(destination.value())
        self.key = c.digest(
            c.canonical(
                dict(
                    item_id=item_id, destination_sha256=c.digest(self.destination_bytes)
                )
            )
        )
        _VIEWS[self] = tuple(
            ReadView(obj, expected) for obj, expected in Export.authorities(self)
        )
        _BINDINGS[self] = (
            id(store),
            id(registry),
            id(source),
            item_id,
            self.destination_bytes,
            source.snapshot,
            c.digest(c.canonical(vars(source.selection))),
            id(source.use),
            id(source.authorize_projection),
            id(approve),
            permission_ref,
            id(checkpoint),
            self.key,
        )

    def authorities(self) -> tuple[tuple[Any, type[Any]], ...]:
        use = self.source.use
        dep = use.dependency
        values: tuple[tuple[Any, type[Any]], ...] = (
            (self.store, Store),
            (self.registry, DatasetRegistry),
            (use.tether_store, dep.modules["tether.feedback"].FeedbackStore),
        )
        if use.source_guard is not None:
            values += (
                (
                    use.source_guard.observation_store,
                    dep.modules["tether.source_observation"].SourceObservationStore,
                ),
            )
        return values

    def binding_check(self) -> None:
        views = _VIEWS.get(self, ())
        authorities = Export.authorities(self)
        if len(views) != len(authorities):
            raise ValueError("dataset export authority changed")
        for view, (obj, expected) in zip(views, authorities):
            if view.store is not obj or view.expected_type is not expected:
                raise ValueError("dataset export authority changed")
            if (
                obj.db is not view.connection
                or (expected is not Store and obj.db.in_transaction)
                or identity(view.path) != view.file_identity
                or schema(obj.db) != view.expected_schema
            ):
                raise ValueError("dataset export storage binding changed")
        if (
            id(self.store),
            id(self.registry),
            id(self.source),
            self.item_id,
            c.canonical(self.destination.value()),
            self.source.snapshot,
            c.digest(c.canonical(vars(self.source.selection))),
            id(self.source.use),
            id(self.source.authorize_projection),
            id(self.approve),
            self.permission_ref,
            id(self.checkpoint),
            self.key,
        ) != _BINDINGS.get(self) or c.canonical(
            self.destination.value()
        ) != self.destination_bytes:
            raise ValueError("dataset export binding changed")

    def check(self) -> None:
        Export.binding_check(self)
        revoked = Export.record(self, "revoked")
        if revoked is not None:
            if revoked != dict(
                version="refinery.dataset-export-revoked.v1", projection_sha256=self.key
            ):
                raise ValueError("invalid local export tombstone")
            raise ValueError("dataset export locally revoked")

    def record(self, kind: str) -> Any:
        row = self.store.db.execute(
            "SELECT 1 FROM objects WHERE kind=? AND identity=?",
            (PREFIX + kind, self.key),
        ).fetchone()
        return None if row is None else self.store.get(PREFIX + kind, self.key)

    def body(self) -> dict[str, Any]:
        Export.check(self)
        descriptor, input_value, output = DatasetRegistry.export_read(
            self.registry,
            self.item_id,
            self.source,
            destination=self.destination.value(),
            approve=self.approve,
            permission_ref=self.permission_ref,
        )
        Export.check(self)
        safe_content(input_value)
        text = output.decode("utf-8")
        safe_content(text)
        value = dict(
            id=self.key,
            datasetName=self.destination.dataset_name,
            input=input_value,
            expectedOutput=text,
            metadata=dict(runspindle_dataset=descriptor, projection_sha256=self.key),
            status="ACTIVE",
        )
        if len(c.canonical(value)) > MAX_BYTES:
            raise ValueError("dataset body bound")
        return value

    def capture(self) -> dict[str, Any]:
        body = Export.body(self)
        intent = dict(
            version="refinery.dataset-export.v1",
            item_id=self.item_id,
            destination_sha256=c.digest(self.destination_bytes),
            snapshot_sha256=self.source.snapshot,
            body_sha256=c.digest(c.canonical(body)),
            permission_sha256=c.digest(self.permission_ref.encode("ascii")),
            authority_sha256={
                name: BoundBatch.storage_binding(view)
                for name, view in zip(
                    ("store", "registry", "feedback", "source_guard"), _VIEWS[self]
                )
            },
        )
        with self.store.transaction() as tx:
            Export.check(self)
            self.store.put(tx, PREFIX + "intent", self.key, intent)
        return intent

    def revoke(self, *, approve: Authority, permission_ref: str) -> None:
        if type(permission_ref) is not str or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,128}", permission_ref
        ):
            raise ValueError("explicit revocation reference")
        Export.binding_check(self)
        request = dict(projection_sha256=self.key, destination=self.destination.value())
        if approve(request, "dataset_export_revoke", permission_ref) is not True:
            raise ValueError("local export revocation denied")
        Export.binding_check(self)
        with self.store.transaction() as tx:
            self.store.put(
                tx,
                PREFIX + "revoked",
                self.key,
                dict(
                    version="refinery.dataset-export-revoked.v1",
                    projection_sha256=self.key,
                ),
            )

    def validate_records(self, intent: dict[str, Any]) -> tuple[Any, Any]:
        expected_attempt = dict(intent_sha256=c.digest(c.canonical(intent)))
        attempt = Export.record(self, "attempt")
        readback = Export.record(self, "readback")
        if attempt is not None and attempt != expected_attempt:
            raise ValueError("invalid export attempt binding")
        if readback is not None and readback != dict(
            intent_sha256=expected_attempt["intent_sha256"],
            body_sha256=intent["body_sha256"],
        ):
            raise ValueError("invalid export readback binding")
        return attempt, readback

    def dispatch(self, projection: DatasetProjection) -> str:
        if (
            type(projection) is not DatasetProjection
            or projection.destination != self.destination
        ):
            raise ValueError("exact dataset projection destination required")
        intent = Export.capture(self)
        Export.validate_records(self, intent)
        body = Export.body(self)
        if c.digest(c.canonical(body)) != intent["body_sha256"]:
            raise ValueError("export content changed")
        visible = projection.read(body)
        Export.check(self)
        if not visible:
            with self.store.transaction() as tx:
                Export.check(self)
                attempt, readback = Export.validate_records(self, intent)
                if attempt is not None or readback is not None:
                    return "uncertain"
                self.store.put(
                    tx,
                    PREFIX + "attempt",
                    self.key,
                    dict(intent_sha256=c.digest(c.canonical(intent))),
                )
            self.checkpoint("after_dataset_export_attempt")
            # Serialize the final local fence/write against local sticky revoke.
            # Does not atomically lock source/registry or an external server.
            with self.store.transaction():
                Export.check(self)
                fresh = Export.body(self)
                if fresh != body:
                    raise ValueError("export source changed before write")
                projection.put(body)
            visible = projection.read(body)
        if not visible:
            return "uncertain"
        with self.store.transaction() as tx:
            Export.check(self)
            Export.validate_records(self, intent)
            if Export.body(self) != body:
                raise ValueError("export source changed before settlement")
            self.store.put(
                tx,
                PREFIX + "readback",
                self.key,
                dict(
                    intent_sha256=c.digest(c.canonical(intent)),
                    body_sha256=intent["body_sha256"],
                ),
            )
        return "delivered"
