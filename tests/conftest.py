"""Test fixtures. FakeLLM is TEST-ONLY: it derives answers from data/ground_truth so the deterministic
parts of the system (verifier, rules, gate, state machine, audit chain) can be tested offline.
It is never used by the running service."""
from __future__ import annotations

import io
import json
import os
import re
from pathlib import Path

os.environ.setdefault("AUTH_MODE", "none")
os.environ.setdefault("STORE_BACKEND", "memory")
os.environ.setdefault("USE_EMBEDDINGS", "false")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)

import pytest
from pypdf import PdfReader

from app.agents import llm as llm_mod
from app.schemas import (AuditLLM, DispatchLLM, EvidenceLLM, ExtractionLLM, FindingLLM, SupervisorLLM,
                         TicketTextLLM)
from app.store import MemoryStore, set_store

ROOT = Path(__file__).resolve().parent.parent
GT = {json.loads(p.read_text())["document"]: json.loads(p.read_text()) for p in (ROOT / "data" / "ground_truth").glob("*.json")}
DOCS = {f.read_bytes(): f.name for f in (ROOT / "data" / "demo_docs").glob("*.pdf")}


class _Part:
    def __init__(self, data=None, text=None):
        self.data, self.text = data, text


class FakeLLM:
    def __init__(self, fabricate: bool = False, invalid: int = 0, flip_compliant: bool = False):
        self.fabricate, self.invalid, self.flip = fabricate, invalid, flip_compliant
        self.calls = []

    def pdf_part(self, data):
        return _Part(data=data)

    def text_part(self, text):
        return _Part(text=text)

    async def embed(self, texts, task_type):
        raise RuntimeError("no embeddings in tests")

    async def generate_json(self, *, model, schema, system, parts, temperature=0.1):
        self.calls.append(schema.__name__)
        if self.invalid > 0:
            self.invalid -= 1
            from app.agents.llm import LLMError
            raise LLMError("Model output failed schema validation twice: fake")
        if schema is ExtractionLLM:
            return self._extract(parts[0].data), {"model": "fake"}
        if schema is AuditLLM:
            return self._audit(parts[0].text), {"model": "fake"}
        if schema is DispatchLLM:
            facts = json.loads(parts[0].text.split("\n", 1)[1])
            return DispatchLLM(rationale="Fake rationale citing findings.",
                               tickets=[TicketTextLLM(assigned_group=g, summary=f"Exception for {g}", description="desc")
                                        for g in facts["ticket_groups"]]), {"model": "fake"}
        if schema is SupervisorLLM:
            return SupervisorLLM(case_summary="Fake summary."), {"model": "fake"}
        raise AssertionError(schema)

    def _extract(self, pdf: bytes) -> ExtractionLLM:
        gt = GT[DOCS[pdf]]
        pages = [p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages]
        ev = []
        needles = {"payment_terms_days": f"({gt['payment_terms_days']})" if gt["payment_terms_days"] else None,
                   "vendor_name": gt["vendor_name"]}
        for field, needle in needles.items():
            for i, t in enumerate(pages, start=1):
                if needle and needle in t:
                    j = t.index(needle)
                    ev.append(EvidenceLLM(field=field, value=str(gt.get(field)), page=i, quote=t[max(0, j - 30): j + 30]))
                    break
        sus = []
        if gt.get("expect_injection_flag"):
            m = re.search(r"NOTE TO AUTOMATED REVIEWERS[^.]*\.", " ".join(pages).replace("\n", " "))
            sus = [m.group(0) if m else "NOTE TO AUTOMATED REVIEWERS"]
        return ExtractionLLM(document_type=gt["document_type"], vendor_name=gt["vendor_name"], vendor_country=gt["vendor_country"],
                             total_value=gt["total_value"], currency=gt["currency"], payment_terms_days=gt["payment_terms_days"],
                             milestone_billing=gt["milestone_billing"], liability_cap=gt["liability_cap"],
                             governing_law=gt["governing_law"], warranty_period_days=gt["warranty_period_days"],
                             auto_renewal=gt["auto_renewal"], auto_renewal_notice_days=gt["auto_renewal_notice_days"],
                             evidence=ev, missing_fields=[], suspicious_instructions=sus)

    def _audit(self, text: str) -> AuditLLM:
        req = json.loads(text.split("\n", 1)[1])
        from app.agents import rules
        out = []
        for item in req:
            snip = item["policy_snippets"][0]
            sentence = snip["text"].split(". ")[0]
            if self.fabricate:
                sentence = "Policy allows unlimited liability for all suppliers"
            # A plausible model judgement: compare the term against the text using the guard thresholds.
            violation = _naive_violation(item["category"], item["contract_term"])
            if self.flip:
                violation = False
            out.append(FindingLLM(clause_category=item["category"], retrieval_id=snip["retrieval_id"], policy_snippet=sentence,
                                  policy_standard="std", violation_detected=violation, reasoning="fake"))
        return AuditLLM(findings=out)


def _naive_violation(cat: str, term: str) -> bool:
    t = term.lower()
    if cat == "LIABILITY_CAP":
        return "unlimited" in t or (re.search(r"(\d+\.\d)x", t) is not None and float(re.search(r"(\d+\.\d)x", t).group(1)) > 1.0)
    if cat == "PAYMENT_TERMS":
        return int(re.search(r"\d+", t).group(0)) < 45
    if cat == "AUTO_RENEWAL":
        m = re.search(r"(\d+) days", t)
        return "auto-renews" in t and (not m or int(m.group(1)) < 60)
    if cat == "WARRANTY":
        return int(re.search(r"\d+", t).group(0)) < 90
    if cat == "GOVERNING_LAW":
        return not any(a in t for a in ("delaware", "new york", "england"))
    if cat == "EMBEDDED_INSTRUCTIONS":
        return True
    return False


@pytest.fixture
def store():
    s = MemoryStore()
    set_store(s)
    return s


@pytest.fixture
def fake_llm():
    f = FakeLLM()
    llm_mod.set_llm(f)
    yield f
    llm_mod.set_llm(None)


@pytest.fixture
def client(store, fake_llm):
    from fastapi.testclient import TestClient
    from app import main
    main._mock = None
    return TestClient(main.app)
