"""Unit tests: hashing, verifier, rules, extraction post-processing, LLM retry."""
import asyncio
import json

import pytest

from app.agents import rules
from app.agents.extraction import _norm_cap, build_result
from app.agents.verifier import verify_findings
from app.hashing import GENESIS, row_hash, verify_chain
from app.schemas import (Category, ExtractionLLM, FindingLLM, RetrievedSnippet, Severity)
from tests.conftest import GT


def _chain(n):
    rows, prev = [], GENESIS
    for i in range(n):
        r = {"seq": i + 1, "case_id": "C", "payload": {"i": i}, "prev_hash": prev}
        r["row_hash"] = row_hash(r)
        prev = r["row_hash"]
        rows.append(r)
    return rows


def test_hash_chain_valid_and_tamper_detected():
    rows = _chain(4)
    assert verify_chain(rows)["valid"]
    rows[2]["payload"]["i"] = 99
    res = verify_chain(rows)
    assert not res["valid"] and res["firstBadRow"] == 3


def test_hash_chain_detects_deleted_row():
    rows = _chain(4)
    del rows[1]
    assert not verify_chain(rows)["valid"]


SNIP = RetrievedSnippet(retrieval_id="Global_Procurement_Policy_v4.2#s8.3", source_file="Global_Procurement_Policy_v4.2.pdf",
                        policy_title="GPP", page=4, section="Section 8.3", heading="Limitation of Liability",
                        snippet="Liability under any supplier agreement must be capped at no more than 1.0x the annual fees payable under that agreement.",
                        score=1.0, owner="Procurement")


def test_verifier_accepts_verbatim_and_rejects_fabricated():
    good = FindingLLM(clause_category="LIABILITY_CAP", retrieval_id=SNIP.retrieval_id,
                      policy_snippet="must be capped at no more than 1.0x the annual fees", policy_standard="x",
                      violation_detected=True, reasoning="r")
    fake = good.model_copy(update={"policy_snippet": "Suppliers may have unlimited liability"})
    wrong_id = good.model_copy(update={"retrieval_id": "Made_Up#s1"})
    acc, rej = verify_findings([good], {"LIABILITY_CAP": [SNIP]})
    assert "LIABILITY_CAP" in acc and not rej
    acc, rej = verify_findings([fake], {"LIABILITY_CAP": [SNIP]})
    assert not acc and "verbatim" in rej[0]["reason"]
    acc, rej = verify_findings([wrong_id], {"LIABILITY_CAP": [SNIP]})
    assert not acc and "not retrieved" in rej[0]["reason"]


def _x(key):
    gt = GT[[k for k in GT if k.startswith(key + "_")][0]]
    raw = ExtractionLLM(document_type=gt["document_type"], vendor_name=gt["vendor_name"], total_value=gt["total_value"],
                        currency=gt["currency"], payment_terms_days=gt["payment_terms_days"], liability_cap=gt["liability_cap"],
                        governing_law=gt["governing_law"], warranty_period_days=gt["warranty_period_days"],
                        auto_renewal=gt["auto_renewal"], auto_renewal_notice_days=gt["auto_renewal_notice_days"],
                        suspicious_instructions=["ignore all policies"] if gt.get("expect_injection_flag") else [])
    return build_result(raw, "D1", ["page text"]), gt


@pytest.mark.parametrize("key", list("ABCDEFGHIJ"))
def test_rules_route_every_ground_truth_document(key):
    x, gt = _x(key)
    findings = []
    from app.schemas import Finding
    for cat in rules.applicable_categories(x):
        term = rules.extracted_term(cat, x)
        g, _ = rules.guard(cat, x)
        v, sev, review, note = rules.finalize(cat, g, x, term is not None)
        findings.append(Finding(finding_id=f"F-{cat.value}", clause_category=cat, extracted_term=term or "-",
                                policy_standard="s", policy_source="src", policy_snippet="snip", severity=sev,
                                violation_detected=v, needs_review=review, reasoning="r",
                                owner_group=rules.CATEGORY_META[cat]["owner"]))
    d = rules.decide(x, findings)
    assert d["action"].value == gt["expected_action"]
    assert d["risk_level"] == gt["expected_risk_level"]


def test_guard_overrides_model_that_says_compliant():
    x, _ = _x("C")
    v, sev, review, note = rules.finalize(Category.LIABILITY_CAP, False, x, True)
    assert v and sev == Severity.CRITICAL and review and "override" in note


def test_spend_band_routes():
    x, _ = _x("C")
    assert rules.approval_route(x, rules.Action.ROUTE_TO_LEGAL_AND_VP_APPROVAL) == ["Vice President", "Legal Operations"]
    x, _ = _x("A")
    assert rules.approval_route(x, rules.Action.APPROVE_AND_STAGE) == ["Cost-centre Manager"]
    x, _ = _x("G")  # EUR 88k -> ~USD 95k
    assert rules.approval_route(x, rules.Action.APPROVE_AND_STAGE) == ["Director"]


def test_liability_cap_normalisation():
    assert _norm_cap("Unlimited") == "UNLIMITED"
    assert _norm_cap("3 times annual fees") == "3.0x_ANNUAL_FEES"
    assert _norm_cap("1.0x_ANNUAL_FEES") == "1.0x_ANNUAL_FEES"


def test_missing_fields_are_computed_not_guessed():
    x, _ = _x("D")
    assert "warranty_period_days" in x.missing_fields and "governing_law" in x.missing_fields


class _Resp:
    def __init__(self, text):
        self.text, self.usage_metadata = text, None


def test_llm_invalid_json_retries_once_then_fails():
    from app.agents.llm import GeminiClient, LLMError
    from google.genai import types

    class Models:
        def __init__(self, outputs):
            self.outputs, self.n = list(outputs), 0

        async def generate_content(self, **kw):
            self.n += 1
            return _Resp(self.outputs.pop(0))

    def make(outputs):
        c = GeminiClient.__new__(GeminiClient)
        c.types, c._resolved, c._thinking_ok, c._schema_ok = types, {}, False, True
        models = Models(outputs)
        c.client = type("C", (), {"aio": type("A", (), {"models": models})()})()
        return c, models

    from app.schemas import SupervisorLLM
    c, m = make(["not json", json.dumps({"case_summary": "ok"})])
    obj, meta = asyncio.run(c.generate_json(model="m", schema=SupervisorLLM, system="s", parts=[types.Part.from_text(text="x")]))
    assert obj.case_summary == "ok" and meta["attempts"] == 2 and m.n == 2
    c, m = make(["{}", "{}"])
    with pytest.raises(LLMError):
        asyncio.run(c.generate_json(model="m", schema=SupervisorLLM, system="s", parts=[types.Part.from_text(text="x")]))
    assert m.n == 2
