"""Case state machine. Transitions are enforced here and nowhere else.

UPLOADED -> PROCESSING -> PROPOSED -> APPROVED -> EXECUTED
                 |            |-> (edit) -> PROPOSED, edit recorded
                 |            '-> REJECTED
                 '-> FAILED -> PROCESSING (retry)
"""
from __future__ import annotations

from typing import Optional

from .schemas import Status
from .store import ConflictError, Store

S = Status
ALLOWED: dict[Status, set[Status]] = {
    S.PROCESSING: {S.UPLOADED, S.FAILED},
    S.PROPOSED: {S.PROCESSING, S.PROPOSED},  # PROPOSED -> PROPOSED is an analyst edit
    S.APPROVED: {S.PROPOSED},
    S.EXECUTED: {S.APPROVED},
    S.REJECTED: {S.PROPOSED},
    S.FAILED: {S.PROCESSING},
}


def transition(store: Store, case_id: str, to: Status, fields: Optional[dict] = None) -> dict:
    allowed = {s.value for s in ALLOWED[to]}
    return store.transition(case_id, allowed, to.value, fields)


__all__ = ["transition", "ConflictError", "ALLOWED"]
