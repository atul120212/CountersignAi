"""The human approval gate: the ONLY code path that writes to enterprise systems.

approve:  PROPOSED -> APPROVED (atomic) -> execute tasks -> EXECUTED.  Idempotent: a second call gets 409.
edit:     PROPOSED -> PROPOSED with analyst overrides; risk + routing recomputed deterministically.
reject:   PROPOSED -> REJECTED, no external calls.
"""
from __future__ import annotations

from . import audit
from .agents import rules
from .auth import User
from .mock_enterprise import MockEnterprise
from .schemas import ActionProposal, AuditResult, EditRequest, ExtractionResult, Status
from .state import transition
from .store import Store, new_id, now_ms


def _decision(store: Store, case_id: str, user: User, decision: str, note: str = "", edits: dict | None = None) -> dict:
    d = {"id": new_id("DEC"), "uid": user.uid, "email": user.email, "name": user.name, "decision": decision,
         "note": note, "edits": edits or {}, "ts": now_ms()}
    store.add_decision(case_id, d)
    return d


def approve(store: Store, mock: MockEnterprise, case_id: str, user: User, note: str = "") -> dict:
    # Atomic compare-and-set; raises ConflictError (-> 409) unless status is PROPOSED.
    case = transition(store, case_id, Status.APPROVED, {"approvedBy": {"uid": user.uid, "name": user.name}, "approvedAt": now_ms()})
    d = _decision(store, case_id, user, "APPROVED", note)
    proposal = case["proposal"]
    audit.append(store, case_id, "HUMAN_DECISION", user.uid,
                 {"decision": "APPROVED", "name": user.name, "email": user.email, "note": note,
                  "proposal_id": proposal["proposal_id"], "recommended_action": proposal["recommended_action"],
                  "decision_id": d["id"]})
    results = []
    for task in proposal["tasks"]:
        rec = mock.execute(case_id, task, approved_by=user.uid)
        results.append({"task_id": task["task_id"], "target_system": task["target_system"],
                        "action_type": task["action_type"], "record_id": rec.get("id"), "status": rec.get("status")})
    audit.append(store, case_id, "EXECUTION", "approval-gate", {"results": results})
    proposal["signoff_status"] = "APPROVED"
    return transition(store, case_id, Status.EXECUTED, {"execution": results, "proposal": proposal})


def reject(store: Store, case_id: str, user: User, note: str = "") -> dict:
    case = transition(store, case_id, Status.REJECTED, {"rejectedBy": {"uid": user.uid, "name": user.name}})
    d = _decision(store, case_id, user, "REJECTED", note)
    audit.append(store, case_id, "HUMAN_DECISION", user.uid,
                 {"decision": "REJECTED", "name": user.name, "note": note, "decision_id": d["id"],
                  "proposal_id": case["proposal"]["proposal_id"]})
    return case


def edit(store: Store, case_id: str, user: User, req: EditRequest) -> dict:
    case = store.get_case(case_id)
    if case is None:
        raise KeyError(case_id)
    extraction = ExtractionResult.model_validate(case["extraction"])
    audit_result = AuditResult.model_validate(case["audit"])
    proposal = ActionProposal.model_validate(case["proposal"])
    before = {"risk_score": audit_result.risk_score, "recommended_action": proposal.recommended_action.value,
              "payment_terms_days": extraction.financial.payment_terms_days}

    new_audit, new_proposal, diff = rules.apply_edits(extraction, audit_result, proposal, req)
    after = {"risk_score": new_audit.risk_score, "recommended_action": new_proposal.recommended_action.value,
             "payment_terms_days": diff.get("payment_terms_days", before["payment_terms_days"])}
    edits = {"overrides": {k: v.value for k, v in req.severity_overrides.items()},
             "payment_terms_days": req.payment_terms_days, "before": before, "after": after}
    new_proposal.edits = proposal.edits + [{"by": user.name, "uid": user.uid, "ts": now_ms(), "note": req.note, **edits}]
    # Re-validate the whole proposal before storing (D4).
    ActionProposal.model_validate(new_proposal.model_dump(mode="json"))
    updated = transition(store, case_id, Status.PROPOSED, {
        "audit": new_audit.model_dump(mode="json"), "proposal": new_proposal.model_dump(mode="json"),
        "riskScore": new_audit.risk_score, "riskLevel": new_audit.risk_level,
        "recommendedAction": new_proposal.recommended_action.value, "edited": True})
    d = _decision(store, case_id, user, "EDITED", req.note, edits)
    audit.append(store, case_id, "HUMAN_DECISION", user.uid, {"decision": "EDITED", "name": user.name, "decision_id": d["id"], **edits})
    return updated
