"""Citation verifier (design principle D2: grounded or silent).

A finding survives only if
  * its retrieval_id was returned by retrieval in THIS run for THIS category, and
  * its policy_snippet is a verbatim (whitespace/punctuation-normalized) substring of that retrieved text.
Anything else is rejected and logged; the category falls back to "No policy found - manual review".
"""
from __future__ import annotations

from ..schemas import Category, FindingLLM, RetrievedSnippet
from .pdftext import norm


def verify_findings(findings: list[FindingLLM], retrieval: dict[str, list[RetrievedSnippet]]
                    ) -> tuple[dict[str, tuple[FindingLLM, RetrievedSnippet]], list[dict]]:
    accepted: dict[str, tuple[FindingLLM, RetrievedSnippet]] = {}
    rejected: list[dict] = []
    for f in findings:
        cat = f.clause_category.strip().upper()
        reason = None
        if cat not in Category.__members__:
            reason = "unknown clause_category"
        elif cat not in retrieval:
            reason = "category was not audited in this run"
        elif cat in accepted:
            reason = "duplicate finding for category"
        else:
            hit = next((s for s in retrieval[cat] if s.retrieval_id == f.retrieval_id), None)
            if hit is None:
                reason = f"retrieval_id {f.retrieval_id!r} was not retrieved in this run"
            elif len(norm(f.policy_snippet)) < 12:
                reason = "policy_snippet is empty or too short"
            elif norm(f.policy_snippet) not in norm(hit.snippet):
                reason = "policy_snippet is not a verbatim quote of the retrieved policy text"
            else:
                accepted[cat] = (f, hit)
        if reason:
            rejected.append({"clause_category": f.clause_category, "retrieval_id": f.retrieval_id,
                             "policy_snippet": f.policy_snippet[:300], "reason": reason})
    return accepted, rejected
