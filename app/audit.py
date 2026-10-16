"""Audit writer: hash-chained rows in Firestore, optionally mirrored to BigQuery."""
from __future__ import annotations

import copy
import logging

from .config import settings
from .hashing import canonical_json, verify_chain
from .store import Store, new_id, now_ms

log = logging.getLogger("operateguard.audit")
_bq = None


def _mirror_bigquery(row: dict) -> None:
    """Best-effort append to BigQuery (append-only analytics copy). Firestore stays the source of truth."""
    global _bq
    if not settings.bq_table:
        return
    try:
        if _bq is None:
            from google.cloud import bigquery
            _bq = bigquery.Client(project=settings.project or None)
        bq_row = {k: row[k] for k in ("event_id", "case_id", "event_type", "actor", "seq", "prev_hash", "row_hash")}
        bq_row["ts"] = row["ts"] / 1000.0
        bq_row["payload"] = canonical_json(row["payload"])
        errors = _bq.insert_rows_json(settings.bq_table, [bq_row])
        if errors:
            log.warning("bigquery mirror errors: %s", errors)
    except Exception as e:  # never break the workflow because the mirror failed
        log.warning("bigquery mirror failed: %s", e)


def append(store: Store, case_id: str, event_type: str, actor: str, payload: dict) -> dict:
    row = {
        "event_id": new_id("EVT"),
        "case_id": case_id,
        "event_type": event_type,  # AGENT_RUN, HUMAN_DECISION, EXECUTION
        "actor": actor,
        "payload": payload,
        "ts": now_ms(),
    }
    full = store.append_audit(case_id, row)
    _mirror_bigquery(full)
    return full


def verify(store: Store, case_id: str, simulate_tamper_row: int | None = None) -> dict:
    rows = store.list_audit(case_id)
    if simulate_tamper_row:
        # Demonstration only: alter an in-memory COPY of one row; stored rows are never modified.
        rows = copy.deepcopy(rows)
        idx = max(1, min(simulate_tamper_row, len(rows))) - 1
        if rows:
            rows[idx]["payload"] = {**rows[idx]["payload"], "tampered": True}
    result = verify_chain(rows)
    case = store.get_case(case_id) or {}
    if result["valid"] and rows and result.get("head") != case.get("lastAuditHash"):
        result = {"valid": False, "rows": len(rows), "firstBadRow": len(rows), "reason": "chain head does not match case.lastAuditHash"}
    result["simulated"] = bool(simulate_tamper_row)
    result["chain"] = [{"seq": r["seq"], "event_type": r["event_type"], "actor": r["actor"], "ts": r["ts"],
                        "prev_hash": r["prev_hash"][:12], "row_hash": r["row_hash"][:12]} for r in rows]
    return result
