"""Agent 3 - Action Dispatch, and the Supervisor summary.

rules.py has already decided the action, route and task structure. Gemini only writes prose:
the approver rationale, exception-ticket texts, and the Supervisor's case summary. Neither agent
has any tool that writes to an enterprise system.
"""
from __future__ import annotations

import asyncio
import json

from ..config import settings
from ..schemas import DispatchLLM, ExtractionResult, Finding, SupervisorLLM

DISPATCH_SYSTEM = """You are the Action Dispatch Agent of OperateGuard AI. The recommended action, approval route
and task list have already been decided by deterministic rules; do not change or question them.
Write: (1) rationale - 3 to 5 sentences for the human approver explaining why this action is recommended,
citing finding ids and policy sources exactly as given; (2) one ticket per assigned_group listed, with a
concise summary (max 90 characters) and a description (max 80 words) that cites the policy source.
Never invent policy terms, numbers or sources that are not in the input. You never execute actions."""

SUPERVISOR_SYSTEM = """You are the Operations Supervisor of OperateGuard AI. You never invent policy terms and you
never execute write actions. Summarise the case for an analyst queue in at most two sentences: vendor, value,
the most important issue (or that it is compliant) and the recommended action. Use only the input."""


def _facts(x: ExtractionResult, findings: list[Finding], decision: dict, groups: list[str]) -> str:
    return json.dumps({
        "vendor": x.vendor.name, "document_type": x.document_type.value,
        "value": f"{x.financial.currency or 'USD'} {x.financial.total_value:,.2f}" if x.financial.total_value is not None else None,
        "recommended_action": decision["action"].value, "approval_route": decision["route"],
        "risk_score": decision["risk_score"], "risk_level": decision["risk_level"],
        "ticket_groups": groups,
        "findings": [{"finding_id": f.finding_id, "category": f.clause_category.value, "contract_term": f.extracted_term,
                      "severity": f.severity.value, "violation": f.violation_detected, "needs_review": f.needs_review,
                      "policy_standard": f.policy_standard, "policy_source": f.policy_source}
                     for f in findings if f.severity.value != "NONE" or f.clause_category.value == "SPEND_APPROVAL"],
        "embedded_instructions_detected": bool(x.suspicious_instructions),
    }, indent=1)


async def run(llm, x: ExtractionResult, findings: list[Finding], decision: dict, groups: list[str]
              ) -> tuple[DispatchLLM, SupervisorLLM, dict]:
    facts = _facts(x, findings, decision, groups)
    (d, dm), (s, sm) = await asyncio.gather(
        llm.generate_json(model=settings.flash_model, schema=DispatchLLM, system=DISPATCH_SYSTEM, temperature=0.2,
                          parts=[llm.text_part("Case facts:\n" + facts)]),
        llm.generate_json(model=settings.flash_model, schema=SupervisorLLM, system=SUPERVISOR_SYSTEM, temperature=0.2,
                          parts=[llm.text_part("Case facts:\n" + facts)]),
    )
    return d, s, {"dispatch": dm, "supervisor": sm}
