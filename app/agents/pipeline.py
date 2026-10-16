"""Pipeline runner. Orchestration order is fixed in code (Supervisor does not choose it):

  Extraction -> Policy Audit (retrieval -> Gemini -> verifier -> guards) -> Rules -> Action Dispatch + Supervisor

Every step writes a trace event (Firestore -> live UI) and each agent run writes a hash-chained audit row.
The pipeline ends in PROPOSED: it produces an UNCOMMITTED draft and has no code path to enterprise systems.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import traceback

from .. import audit
from ..schemas import ActionProposal, AuditResult, Status
from ..state import transition
from ..store import Store, get_blobs, new_id, now_ms
from . import dispatch, extraction, policy_audit, rules
from .llm import get_llm
from .pdftext import page_texts

log = logging.getLogger("operateguard.pipeline")


class Tracer:
    def __init__(self, store: Store, case: dict) -> None:
        self.store, self.case_id, self.owner = store, case["id"], case["ownerUid"]
        self._seq = 0

    def __call__(self, agent: str, step: str, message: str, payload: dict | None = None) -> None:
        self._seq += 1
        ev = {"id": f"{now_ms():013d}-{self._seq:03d}", "seq": self._seq, "ts": now_ms(), "agent": agent, "step": step,
              "message": message[:400], "payload": payload or {}, "ownerUid": self.owner}
        try:
            self.store.add_trace(self.case_id, ev)
        except Exception as e:  # tracing must never break the case
            log.warning("trace write failed: %s", e)
        log.info('{"case_id":"%s","agent":"%s","step":"%s","msg":%r}', self.case_id, agent, step, message[:200])


def _sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:16]


async def run(store: Store, case_id: str) -> dict:
    case = store.get_case(case_id)
    trace = Tracer(store, case)
    t_start = time.perf_counter()
    lat: dict[str, int] = {}

    async def step(name, coro):
        t = time.perf_counter()
        r = await coro
        lat[name] = int((time.perf_counter() - t) * 1000)
        return r

    try:
        llm = get_llm()
        trace("Supervisor", "start", "Case received. Fixed plan: Extraction -> Policy Audit -> Action Dispatch. "
                                     "Agents draft only; nothing is written to ERP or Jira without human approval.")
        pdf = await asyncio.to_thread(get_blobs().get, case["documentUri"])
        pages = await asyncio.to_thread(page_texts, pdf)
        trace("Extraction", "parse", f"Loaded {case['filename']}: {len(pages)} pages, "
                                     f"{sum(1 for p in pages if len(p.strip()) > 20)} with a text layer. Sending to Gemini (multimodal PDF).")

        # ---- Agent 1: extraction
        x, xmeta = await step("extraction", extraction.run(llm, pdf, case_id, pages))
        verified = sum(1 for e in x.evidence if e.verified)
        trace("Extraction", "result",
              f"{x.document_type.value} from {x.vendor.name or 'unknown vendor'}: "
              f"{x.financial.currency or ''} {f'{x.financial.total_value:,.0f}' if x.financial.total_value is not None else '?'}, "
              f"Net {x.financial.payment_terms_days}, liability {x.risk_clauses.liability_cap}. "
              f"{verified}/{len(x.evidence)} quotes verified on the PDF text layer.",
              {"model": xmeta.get("model"), "latency_ms": xmeta.get("latency_ms"), "missing": x.missing_fields})
        if x.missing_fields:
            trace("Extraction", "missing", f"Not stated in the document (left empty, not guessed): {', '.join(x.missing_fields)}")
        if x.suspicious_instructions:
            trace("Extraction", "injection", "Embedded instructions addressed to AI/reviewers detected and treated as data: "
                                             f"\"{x.suspicious_instructions[0][:160]}\"")
        audit.append(store, case_id, "AGENT_RUN", "ExtractionAgent",
                     {"model": xmeta.get("model"), "system_prompt_sha": _sha(extraction.SYSTEM), "attempts": xmeta.get("attempts"),
                      "input": {"filename": case["filename"], "pages": len(pages)}, "output": x.model_dump(mode="json")})
        store.update_case(case_id, {"extraction": x.model_dump(mode="json"), "documentType": x.document_type.value,
                                    "vendor": x.vendor.name, "value": x.financial.total_value, "currency": x.financial.currency})

        # ---- Agent 2: policy audit
        retrieval, retriever = await step("retrieval", policy_audit.retrieve_all(x, llm))
        for cat, snips in retrieval.items():
            top = snips[0] if snips else None
            trace("PolicyAudit", "retrieve", f"{cat}: {len(snips)} policy snippets via {retriever}"
                  + (f"; top = {top.source_file} {top.section}" if top else " (none found)"),
                  {"ids": [s.retrieval_id for s in snips]})
        findings, rejected, ameta = await step("audit", policy_audit.run(llm, x, retrieval, trace))
        d = rules.decide(x, findings)
        audit_result = AuditResult(findings=findings, rejected_findings=rejected, retrieval=retrieval, retriever=retriever,
                                   risk_score=d["risk_score"], risk_level=d["risk_level"], overall_status=d["overall_status"])
        viol = [f for f in findings if f.violation_detected]
        trace("PolicyAudit", "findings", f"{len(findings)} findings, {len(viol)} violations"
              + (": " + ", ".join(f"{f.clause_category.value} ({f.severity.value})" for f in viol) if viol else "") +
              f". {len(rejected)} ungrounded findings dropped by the verifier.", {"model": ameta.get("model")})
        trace("Rules", "decide", f"Risk {d['risk_score']}/100 ({d['risk_level']}) computed in code. "
                                 f"Action: {d['action'].value}. Approval route: {' -> '.join(d['route'])}.")
        audit.append(store, case_id, "AGENT_RUN", "PolicyAuditAgent",
                     {"model": ameta.get("model"), "system_prompt_sha": _sha(policy_audit.SYSTEM), "retriever": retriever,
                      "prompt": ameta.get("prompt"), "citations": [{"finding": f.finding_id, "retrieval_id": f.retrieval_id,
                                                                    "snippet": f.policy_snippet} for f in findings],
                      "rejected": rejected, "risk_score": d["risk_score"], "overall_status": d["overall_status"]})
        store.update_case(case_id, {"audit": audit_result.model_dump(mode="json"), "riskScore": d["risk_score"],
                                    "riskLevel": d["risk_level"]})

        # ---- Agent 3: dispatch + supervisor
        draft_tasks = rules.build_tasks(x, findings, d["action"], d["route"])
        groups = [t.parameters["assigned_group"] for t in draft_tasks if t.target_system == "MOCK_JIRA"]
        trace("ActionDispatch", "draft", f"Drafting {len(draft_tasks)} tasks ({', '.join(t.action_type for t in draft_tasks)}); writing rationale.")
        dl, sl, dmeta = await step("dispatch", dispatch.run(llm, x, findings, d, groups))
        texts = {t.assigned_group: {"summary": t.summary, "description": t.description} for t in dl.tickets if t.assigned_group in groups}
        tasks = rules.build_tasks(x, findings, d["action"], d["route"], texts)
        proposal = ActionProposal(
            proposal_id=new_id("ACT"), case_id=case_id, recommended_action=d["action"], approval_route=d["route"],
            tasks=tasks, rationale=dl.rationale, case_summary=sl.case_summary,
            citations=[f"{f.finding_id}: {f.policy_source}" for f in findings if f.retrieval_id and (f.violation_detected or f.needs_review)])
        proposal = ActionProposal.model_validate(proposal.model_dump(mode="json"))  # strict contract (D4)
        audit.append(store, case_id, "AGENT_RUN", "ActionDispatchAgent",
                     {"models": {k: v.get("model") for k, v in dmeta.items()}, "proposal": proposal.model_dump(mode="json")})
        total = int((time.perf_counter() - t_start) * 1000)
        lat["total"] = total
        trace("Supervisor", "done", f"Draft ready in {total / 1000:.1f}s. Status UNCOMMITTED - awaiting human approval.")
        return transition(store, case_id, Status.PROPOSED, {
            "proposal": proposal.model_dump(mode="json"), "recommendedAction": d["action"].value,
            "summary": sl.case_summary, "latencyMs": lat, "error": None})
    except Exception as e:
        log.error("pipeline failed for %s: %s\n%s", case_id, e, traceback.format_exc())
        trace("Supervisor", "error", f"Case failed: {str(e)[:300]}")
        try:
            audit.append(store, case_id, "AGENT_RUN", "Supervisor", {"error": str(e)[:1000]})
        except Exception:
            pass
        return transition(store, case_id, Status.FAILED, {"error": str(e)[:500],
                                                          "latencyMs": {**lat, "total": int((time.perf_counter() - t_start) * 1000)}})
