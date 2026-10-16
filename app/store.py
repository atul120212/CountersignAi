"""Persistence: Cloud Firestore in the cloud, an in-memory twin for local development and tests.

Firestore layout
  cases/{caseId}                      case state, extraction, audit, proposal (client-readable by owner)
  cases/{caseId}/trace/{eventId}      live agent trace (client-readable by owner, drives onSnapshot)
  cases/{caseId}/decisions/{id}       human decisions
  cases/{caseId}/audit/{seq}          hash-chained audit rows (server only)
  mock_erp/{id}, mock_jira/{id}       the mock enterprise systems (server only)
"""
from __future__ import annotations

import copy
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from .config import ROOT, settings
from .hashing import GENESIS, row_hash


def now_ms() -> int:
    return int(time.time() * 1000)


class ConflictError(Exception):
    """Raised when a state transition is not allowed from the current state."""


class Store:  # interface
    def create_case(self, case: dict) -> None: ...
    def get_case(self, case_id: str) -> Optional[dict]: ...
    def update_case(self, case_id: str, fields: dict) -> None: ...
    def list_cases(self, owner_uid: str, limit: int = 50) -> list[dict]: ...
    def transition(self, case_id: str, allowed_from: set[str], to: str, fields: Optional[dict] = None) -> dict: ...
    def add_trace(self, case_id: str, event: dict) -> None: ...
    def list_trace(self, case_id: str) -> list[dict]: ...
    def add_decision(self, case_id: str, decision: dict) -> None: ...
    def list_decisions(self, case_id: str) -> list[dict]: ...
    def append_audit(self, case_id: str, row: dict) -> dict: ...
    def list_audit(self, case_id: str) -> list[dict]: ...
    def put_mock(self, collection: str, doc_id: str, data: dict) -> None: ...
    def list_mock(self, collection: str, case_id: str) -> list[dict]: ...


def _chain(row: dict, prev: str, seq: int) -> dict:
    row = dict(row)
    row["seq"] = seq
    row["prev_hash"] = prev
    row["row_hash"] = row_hash(row)
    return row


class MemoryStore(Store):
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.cases: dict[str, dict] = {}
        self.trace: dict[str, list[dict]] = {}
        self.decisions: dict[str, list[dict]] = {}
        self.audit: dict[str, list[dict]] = {}
        self.mock: dict[str, dict[str, dict]] = {}

    def create_case(self, case):
        with self._lock:
            self.cases[case["id"]] = copy.deepcopy(case)

    def get_case(self, case_id):
        with self._lock:
            c = self.cases.get(case_id)
            return copy.deepcopy(c) if c else None

    def update_case(self, case_id, fields):
        with self._lock:
            self.cases[case_id].update(copy.deepcopy(fields))

    def list_cases(self, owner_uid, limit=50):
        with self._lock:
            rows = [copy.deepcopy(c) for c in self.cases.values() if c.get("ownerUid") == owner_uid]
        return sorted(rows, key=lambda c: c.get("createdAt", 0), reverse=True)[:limit]

    def transition(self, case_id, allowed_from, to, fields=None):
        with self._lock:
            c = self.cases.get(case_id)
            if c is None:
                raise KeyError(case_id)
            if c["status"] not in allowed_from:
                raise ConflictError(f"case is {c['status']}, expected one of {sorted(allowed_from)}")
            c["status"] = to
            c["updatedAt"] = now_ms()
            if fields:
                c.update(copy.deepcopy(fields))
            return copy.deepcopy(c)

    def add_trace(self, case_id, event):
        with self._lock:
            self.trace.setdefault(case_id, []).append(copy.deepcopy(event))

    def list_trace(self, case_id):
        with self._lock:
            return sorted(copy.deepcopy(self.trace.get(case_id, [])), key=lambda e: e["ts"])

    def add_decision(self, case_id, decision):
        with self._lock:
            self.decisions.setdefault(case_id, []).append(copy.deepcopy(decision))

    def list_decisions(self, case_id):
        with self._lock:
            return copy.deepcopy(self.decisions.get(case_id, []))

    def append_audit(self, case_id, row):
        with self._lock:
            case = self.cases[case_id]
            rows = self.audit.setdefault(case_id, [])
            full = _chain(row, case.get("lastAuditHash") or GENESIS, len(rows) + 1)
            rows.append(full)
            case["lastAuditHash"] = full["row_hash"]
            case["auditRows"] = len(rows)
            return copy.deepcopy(full)

    def list_audit(self, case_id):
        with self._lock:
            return copy.deepcopy(self.audit.get(case_id, []))

    def put_mock(self, collection, doc_id, data):
        with self._lock:
            self.mock.setdefault(collection, {})[doc_id] = copy.deepcopy(data)

    def list_mock(self, collection, case_id):
        with self._lock:
            return [copy.deepcopy(d) for d in self.mock.get(collection, {}).values() if d.get("case_id") == case_id]


class FirestoreStore(Store):
    def __init__(self) -> None:
        from google.cloud import firestore  # imported lazily so local mode has no GCP dependency
        self._fs = firestore
        self.db = firestore.Client(project=settings.project or None)

    def _case(self, case_id):
        return self.db.collection("cases").document(case_id)

    def create_case(self, case):
        self._case(case["id"]).set(case)

    def get_case(self, case_id):
        snap = self._case(case_id).get()
        return snap.to_dict() if snap.exists else None

    def update_case(self, case_id, fields):
        self._case(case_id).update(fields)

    def list_cases(self, owner_uid, limit=50):
        q = self.db.collection("cases").where(filter=self._fs.FieldFilter("ownerUid", "==", owner_uid))
        rows = [d.to_dict() for d in q.stream()]
        return sorted(rows, key=lambda c: c.get("createdAt", 0), reverse=True)[:limit]

    def transition(self, case_id, allowed_from, to, fields=None):
        ref = self._case(case_id)
        fs = self._fs

        @fs.transactional
        def run(tx):
            snap = ref.get(transaction=tx)
            if not snap.exists:
                raise KeyError(case_id)
            c = snap.to_dict()
            if c["status"] not in allowed_from:
                raise ConflictError(f"case is {c['status']}, expected one of {sorted(allowed_from)}")
            upd = {"status": to, "updatedAt": now_ms(), **(fields or {})}
            tx.update(ref, upd)
            c.update(upd)
            return c

        return run(self.db.transaction())

    def add_trace(self, case_id, event):
        self._case(case_id).collection("trace").document(event["id"]).set(event)

    def list_trace(self, case_id):
        return [d.to_dict() for d in self._case(case_id).collection("trace").order_by("ts").stream()]

    def add_decision(self, case_id, decision):
        self._case(case_id).collection("decisions").document(decision["id"]).set(decision)

    def list_decisions(self, case_id):
        return [d.to_dict() for d in self._case(case_id).collection("decisions").order_by("ts").stream()]

    def append_audit(self, case_id, row):
        ref = self._case(case_id)
        fs = self._fs

        @fs.transactional
        def run(tx):
            c = ref.get(transaction=tx).to_dict()
            seq = int(c.get("auditRows") or 0) + 1
            full = _chain(row, c.get("lastAuditHash") or GENESIS, seq)
            tx.set(ref.collection("audit").document(f"{seq:05d}"), full)
            tx.update(ref, {"lastAuditHash": full["row_hash"], "auditRows": seq})
            return full

        return run(self.db.transaction())

    def list_audit(self, case_id):
        return [d.to_dict() for d in self._case(case_id).collection("audit").order_by("seq").stream()]

    def put_mock(self, collection, doc_id, data):
        self.db.collection(collection).document(doc_id).set(data)

    def list_mock(self, collection, case_id):
        q = self.db.collection(collection).where(filter=self._fs.FieldFilter("case_id", "==", case_id))
        return [d.to_dict() for d in q.stream()]


# ------------------------------------------------------------------ blobs
class BlobStore:
    """Uploaded PDFs: Cloud Storage when GCS_BUCKET is set, otherwise a local folder."""

    def __init__(self) -> None:
        self.bucket = None
        if settings.gcs_bucket:
            from google.cloud import storage
            self.bucket = storage.Client(project=settings.project or None).bucket(settings.gcs_bucket)
        self.local = ROOT / ".local_uploads"

    def put(self, key: str, data: bytes) -> str:
        if self.bucket is not None:
            self.bucket.blob(key).upload_from_string(data, content_type="application/pdf")
            return f"gs://{self.bucket.name}/{key}"
        path = self.local / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return f"file://{key}"

    def get(self, uri: str) -> bytes:
        if uri.startswith("gs://"):
            _, _, rest = uri.partition("gs://")
            bucket, _, key = rest.partition("/")
            assert self.bucket is not None and bucket == self.bucket.name
            return self.bucket.blob(key).download_as_bytes()
        if uri.startswith("file://"):
            return (self.local / uri[len("file://"):]).read_bytes()
        if uri.startswith("sample://"):
            return (ROOT / "data" / "demo_docs" / Path(uri[len("sample://"):]).name).read_bytes()
        raise ValueError(f"unsupported document uri {uri}")


_store: Optional[Store] = None
_blobs: Optional[BlobStore] = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = FirestoreStore() if settings.store_backend == "firestore" else MemoryStore()
    return _store


def set_store(store: Store) -> None:  # tests
    global _store
    _store = store


def get_blobs() -> BlobStore:
    global _blobs
    if _blobs is None:
        _blobs = BlobStore()
    return _blobs


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10].upper()}"


TraceFn = Callable[[str, str, str, Optional[dict]], None]
