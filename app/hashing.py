"""Tamper-evident hash chain for the audit log (design principle D5).

row_hash = sha256(prev_hash + canonical_json(row without row_hash))
The first row of a case uses prev_hash = "GENESIS".
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

GENESIS = "GENESIS"


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def row_hash(row: dict) -> str:
    body = {k: v for k, v in row.items() if k != "row_hash"}
    return hashlib.sha256((row["prev_hash"] + canonical_json(body)).encode("utf-8")).hexdigest()


def verify_chain(rows: Iterable[dict]) -> dict:
    """Recompute the chain over rows ordered by seq. Returns {valid, rows, firstBadRow, reason}."""
    prev = GENESIS
    n = 0
    for n, row in enumerate(rows, start=1):
        if row.get("prev_hash") != prev:
            return {"valid": False, "rows": n, "firstBadRow": n, "reason": "prev_hash does not match the previous row"}
        if row_hash(row) != row.get("row_hash"):
            return {"valid": False, "rows": n, "firstBadRow": n, "reason": "row contents do not match row_hash"}
        prev = row["row_hash"]
    return {"valid": True, "rows": n, "firstBadRow": None, "reason": None, "head": prev}
