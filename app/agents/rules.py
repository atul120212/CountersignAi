"""Deterministic decision layer (design principle D3).

The LLM reads documents and explains policy; this module decides. Severity, risk score,
overall status, recommended action, approval route and the task list are computed here
from validated findings, so the same inputs always give the same routing.

Guard thresholds mirror the synthetic policy corpus (data/policies/corpus.json). They act
as a safety net: a model can add a violation, but it can never talk a hard-limit breach away.
"""
from __future__ import annotations

import re
from typing import Optional

from ..schemas import (Action, ActionProposal, AuditResult, Category, EditRequest, ExtractionResult, Finding,
                       Severity, Task)

C = Category
LEGAL, TREASURY, PROCUREMENT = "Legal Operations", "Treasury", "Procurement"

CATEGORY_META: dict[Category, dict] = {
    C.LIABILITY_CAP: {"query": "limitation of liability cap annual fees unlimited liability", "owner": LEGAL, "severity": Severity.CRITICAL},
    C.PAYMENT_TERMS: {"query": "standard vendor payment terms Net days invoice treasury exception", "owner": TREASURY, "severity": Severity.MODERATE},
    C.AUTO_RENEWAL: {"query": "automatic renewal notice period cancel renewal date", "owner": LEGAL, "severity": Severity.HIGH},
    C.WARRANTY: {"query": "warranty period deliverables days from acceptance", "owner": LEGAL, "severity": Severity.MODERATE},
    C.GOVERNING_LAW: {"query": "approved governing law jurisdictions", "owner": LEGAL, "severity": Severity.LOW},
    C.SPEND_APPROVAL: {"query": "spend authority approval bands total contract value Director Vice President", "owner": PROCUREMENT, "severity": Severity.NONE},
    C.EMBEDDED_INSTRUCTIONS: {"query": "instructions embedded in supplier document pre-approve skip policy checks", "owner": LEGAL, "severity": Severity.HIGH},
}

WEIGHTS = {Severity.CRITICAL: 45, Severity.HIGH: 30, Severity.MODERATE: 20, Severity.LOW: 5, Severity.NONE: 0}
SEV_ORDER = [Severity.NONE, Severity.LOW, Severity.MODERATE, Severity.HIGH, Severity.CRITICAL]
PRIORITY = {Severity.CRITICAL: "Highest", Severity.HIGH: "High", Severity.MODERATE: "Medium", Severity.LOW: "Low"}

# Guard values copied from the synthetic policies (retrieval ids in comments).
MAX_LIABILITY_MULTIPLE = 1.0          # Global_Procurement_Policy_v4.2#s8.3
MIN_PAYMENT_DAYS = 45                 # Treasury_Working_Capital_Guidelines#s3.1
MIN_RENEWAL_NOTICE_DAYS = 60          # Legal_Contract_Standards#s3
MIN_WARRANTY_DAYS = 90                # Legal_Contract_Standards#s4
APPROVED_LAW = ("delaware", "new york", "england")  # Legal_Contract_Standards#s2
SPEND_BANDS = [(50_000, ["Cost-centre Manager"]), (100_000, ["Director"]), (float("inf"), ["Vice President", LEGAL])]  # GPP#s4.1
FX_TO_USD = {"USD": 1.0, "EUR": 1.08, "GBP": 1.27, "INR": 0.012, "CAD": 0.73}  # synthetic Treasury reference rates (GPP#s4.2)


def applicable_categories(x: ExtractionResult) -> list[Category]:
    if x.document_type.value == "INVOICE":
        cats = [C.PAYMENT_TERMS, C.SPEND_APPROVAL]
    else:
        cats = [C.LIABILITY_CAP, C.PAYMENT_TERMS, C.AUTO_RENEWAL, C.WARRANTY, C.GOVERNING_LAW, C.SPEND_APPROVAL]
    if x.suspicious_instructions:
        cats.append(C.EMBEDDED_INSTRUCTIONS)
    return cats


def usd_value(x: ExtractionResult) -> Optional[float]:
    v, cur = x.financial.total_value, (x.financial.currency or "USD").upper()
    if v is None:
        return None
    return v * FX_TO_USD.get(cur, 1.0)


def extracted_term(cat: Category, x: ExtractionResult) -> Optional[str]:
    f, r = x.financial, x.risk_clauses
    if cat == C.LIABILITY_CAP:
        return r.liability_cap
    if cat == C.PAYMENT_TERMS:
        return f"Net {f.payment_terms_days}" if f.payment_terms_days is not None else None
    if cat == C.AUTO_RENEWAL:
        if r.auto_renewal is None:
            return None
        if not r.auto_renewal:
            return "No automatic renewal"
        return f"Auto-renews; {r.auto_renewal_notice_days} days notice" if r.auto_renewal_notice_days is not None else "Auto-renews; notice period not stated"
    if cat == C.WARRANTY:
        return f"{r.warranty_period_days} days" if r.warranty_period_days is not None else None
    if cat == C.GOVERNING_LAW:
        return r.governing_law
    if cat == C.SPEND_APPROVAL:
        if f.total_value is None:
            return None
        return f"{f.currency or 'USD'} {f.total_value:,.2f}"
    if cat == C.EMBEDDED_INSTRUCTIONS:
        return x.suspicious_instructions[0] if x.suspicious_instructions else None
    return None


EVIDENCE_FIELD = {C.LIABILITY_CAP: "liability_cap", C.PAYMENT_TERMS: "payment_terms_days", C.AUTO_RENEWAL: "auto_renewal",
                  C.WARRANTY: "warranty_period_days", C.GOVERNING_LAW: "governing_law", C.SPEND_APPROVAL: "total_value"}


def liability_multiple(cap: Optional[str]) -> Optional[float]:
    if not cap:
        return None
    if "UNLIMITED" in cap.upper():
        return float("inf")
    m = re.search(r"(\d+(?:\.\d+)?)\s*x", cap, re.I)
    return float(m.group(1)) if m else None


def guard(cat: Category, x: ExtractionResult) -> tuple[Optional[bool], str]:
    """Deterministic violation check. None = cannot decide (term missing or not machine-readable)."""
    f, r = x.financial, x.risk_clauses
    if cat == C.LIABILITY_CAP:
        mult = liability_multiple(r.liability_cap)
        if mult is None:
            return None, ""
        return mult > MAX_LIABILITY_MULTIPLE, f"cap {r.liability_cap} vs max {MAX_LIABILITY_MULTIPLE}x annual fees"
    if cat == C.PAYMENT_TERMS:
        if f.payment_terms_days is None:
            return None, ""
        return f.payment_terms_days < MIN_PAYMENT_DAYS, f"Net {f.payment_terms_days} vs minimum Net {MIN_PAYMENT_DAYS}"
    if cat == C.AUTO_RENEWAL:
        if r.auto_renewal is None:
            return None, ""
        if not r.auto_renewal:
            return False, "no automatic renewal"
        n = r.auto_renewal_notice_days
        return (n is None or n < MIN_RENEWAL_NOTICE_DAYS), f"notice {n} days vs minimum {MIN_RENEWAL_NOTICE_DAYS}"
    if cat == C.WARRANTY:
        if r.warranty_period_days is None:
            return None, ""
        return r.warranty_period_days < MIN_WARRANTY_DAYS, f"{r.warranty_period_days} days vs minimum {MIN_WARRANTY_DAYS}"
    if cat == C.GOVERNING_LAW:
        if not r.governing_law:
            return None, ""
        ok = any(a in r.governing_law.lower() for a in APPROVED_LAW)
        return (not ok), f"{r.governing_law} vs approved list"
    if cat == C.SPEND_APPROVAL:
        return False, "approval band, not a violation"
    if cat == C.EMBEDDED_INSTRUCTIONS:
        return bool(x.suspicious_instructions), "document contains text addressed to AI or reviewers"
    return None, ""


def finalize(cat: Category, llm_violation: Optional[bool], x: ExtractionResult, term_present: bool
             ) -> tuple[bool, Severity, bool, Optional[str]]:
    """Merge model judgement with the guard. Returns (violation, severity, needs_review, guard_note)."""
    meta = CATEGORY_META[cat]
    if cat == C.SPEND_APPROVAL:
        return False, Severity.NONE, False, None
    if not term_present:
        return False, Severity.LOW, True, "Term not found in the document - manual review required"
    g, why = guard(cat, x)
    note = None
    if llm_violation is None:  # no grounded model finding for this category
        violation = bool(g)
        note = f"No verified policy citation; deterministic guard: {why}" if g is not None else "No verified policy citation - manual review"
        needs_review = True
    elif g is True and not llm_violation:
        violation, needs_review = True, True
        note = f"Deterministic guard override: {why}. The model judged this compliant; the guard flags it."
    elif g is False and llm_violation:
        violation, needs_review = True, True
        note = f"Model flagged a violation the guard did not ({why}); kept for human review."
    else:
        violation, needs_review = bool(llm_violation), False
    sev = meta["severity"] if violation else (Severity.LOW if needs_review else Severity.NONE)
    return violation, sev, needs_review, note


def _active(f: Finding) -> bool:
    return f.severity != Severity.NONE and (f.violation_detected or f.needs_review)


def score(findings: list[Finding]) -> tuple[int, str, str]:
    active = [f for f in findings if _active(f)]
    s = min(100, sum(WEIGHTS[f.severity] for f in active))
    if any(f.severity == Severity.CRITICAL for f in active):
        level = "CRITICAL"
    elif s >= 45:
        level = "HIGH"
    elif s >= 15:
        level = "MEDIUM"
    else:
        level = "LOW"
    if any(f.violation_detected for f in active):
        status = "FLAGGED_FOR_EXCEPTION"
    elif active:
        status = "NEEDS_REVIEW"
    else:
        status = "COMPLIANT"
    return s, level, status


def recommend(findings: list[Finding]) -> Action:
    active = [f for f in findings if _active(f)]
    legal = [f for f in active if f.owner_group == LEGAL]
    if any(f.violation_detected and SEV_ORDER.index(f.severity) >= SEV_ORDER.index(Severity.HIGH) for f in legal):
        return Action.ROUTE_TO_LEGAL_AND_VP_APPROVAL
    if legal:
        return Action.ROUTE_TO_LEGAL_REVIEW
    if any(f.owner_group == TREASURY for f in active):
        return Action.ROUTE_TO_TREASURY
    return Action.APPROVE_AND_STAGE


def approval_route(x: ExtractionResult, action: Action) -> list[str]:
    usd = usd_value(x)
    route: list[str] = []
    if usd is None:
        route = ["Director"]  # value unknown: never the lowest band
    else:
        for limit, approvers in SPEND_BANDS:
            if usd < limit or limit == float("inf"):
                route = list(approvers)
                break
    extra = {Action.ROUTE_TO_LEGAL_AND_VP_APPROVAL: ["Vice President", LEGAL],
             Action.ROUTE_TO_LEGAL_REVIEW: [LEGAL], Action.ROUTE_TO_TREASURY: ["Treasury Manager"]}.get(action, [])
    for a in extra:
        if a not in route:
            route.append(a)
    return route


def build_tasks(x: ExtractionResult, findings: list[Finding], action: Action, route: list[str],
                ticket_texts: dict[str, dict] | None = None) -> list[Task]:
    ticket_texts = ticket_texts or {}
    vendor = x.vendor.name or "Unknown vendor"
    is_invoice = x.document_type.value == "INVOICE"
    tasks = [Task(task_id="T1", target_system="MOCK_ERP",
                  action_type="STAGE_INVOICE_FOR_PAYMENT" if is_invoice else "STAGE_PURCHASE_ORDER",
                  parameters={"vendor": vendor, "amount": x.financial.total_value, "currency": x.financial.currency,
                              "payment_terms_days": x.financial.payment_terms_days, "document_reference": x.document_reference,
                              "status": "READY_TO_POST" if action == Action.APPROVE_AND_STAGE else "BLOCKED_ON_EXCEPTION"}),
             Task(task_id="T2", target_system="MOCK_ERP", action_type="REQUEST_APPROVAL",
                  parameters={"vendor": vendor, "amount": x.financial.total_value, "currency": x.financial.currency,
                              "approvers": route, "status": "PENDING_APPROVAL"})]
    groups: dict[str, list[Finding]] = {}
    for f in findings:
        if _active(f) and f.owner_group != PROCUREMENT:
            groups.setdefault(f.owner_group, []).append(f)
    for i, (group, fs) in enumerate(sorted(groups.items()), start=3):
        top = max((f.severity for f in fs), key=SEV_ORDER.index)
        txt = ticket_texts.get(group) or {}
        cats = ", ".join(f.clause_category.value.replace("_", " ").lower() for f in fs)
        tasks.append(Task(task_id=f"T{i}", target_system="MOCK_JIRA", action_type="CREATE_EXCEPTION_TICKET",
                          parameters={"assigned_group": group, "priority": PRIORITY.get(top, "Low"),
                                      "finding_ids": [f.finding_id for f in fs],
                                      "summary": txt.get("summary") or f"Procurement exception: {vendor} - {cats}",
                                      "description": txt.get("description") or "; ".join(
                                          f"{f.clause_category.value}: contract '{f.extracted_term}' vs policy '{f.policy_standard}' ({f.policy_source})"
                                          for f in fs)}))
    return tasks


def decide(x: ExtractionResult, findings: list[Finding]) -> dict:
    s, level, status = score(findings)
    action = recommend(findings)
    route = approval_route(x, action)
    return {"risk_score": s, "risk_level": level, "overall_status": status, "action": action, "route": route}


def apply_edits(x: ExtractionResult, audit: AuditResult, proposal: ActionProposal, req: EditRequest
                ) -> tuple[AuditResult, ActionProposal, dict]:
    """Analyst edits: severity overrides and/or negotiated payment terms. Recomputed deterministically."""
    x = x.model_copy(deep=True)
    findings = [f.model_copy(deep=True) for f in audit.findings]
    diff: dict = {}
    if req.payment_terms_days is not None:
        diff["payment_terms_days"] = req.payment_terms_days
        x.financial.payment_terms_days = req.payment_terms_days
        for f in findings:
            if f.clause_category == C.PAYMENT_TERMS:
                g, why = guard(C.PAYMENT_TERMS, x)
                f.extracted_term = f"Net {req.payment_terms_days} (analyst edit)"
                f.violation_detected = bool(g)
                f.severity = CATEGORY_META[C.PAYMENT_TERMS]["severity"] if g else Severity.NONE
                f.needs_review = False
                f.guard_note = f"Re-evaluated after analyst edit: {why}"
    known = {f.finding_id for f in findings}
    for fid, sev in req.severity_overrides.items():
        if fid not in known:
            raise ValueError(f"unknown finding {fid}")
        for f in findings:
            if f.finding_id == fid and f.severity != sev:
                f.analyst_override = f"{f.severity.value} -> {sev.value}"
                f.severity = sev
    d = decide(x, findings)
    new_audit = audit.model_copy(update={"findings": findings, "risk_score": d["risk_score"],
                                         "risk_level": d["risk_level"], "overall_status": d["overall_status"]})
    old_texts = {t.parameters.get("assigned_group"): {"summary": t.parameters.get("summary"), "description": t.parameters.get("description")}
                 for t in proposal.tasks if t.target_system == "MOCK_JIRA"}
    tasks = build_tasks(x, findings, d["action"], d["route"], old_texts)
    rationale = proposal.rationale
    if req.note or req.severity_overrides or req.payment_terms_days is not None:
        rationale += f"\n\nAnalyst edit: {req.note or 'overrides applied'}. Risk recomputed by rules: {d['risk_score']} ({d['risk_level']}), action {d['action'].value}."
    new_proposal = proposal.model_copy(update={"recommended_action": d["action"], "approval_route": d["route"],
                                               "tasks": tasks, "rationale": rationale})
    return new_audit, new_proposal, diff
