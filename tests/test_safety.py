"""Safety suite (must be green before every deploy).

1. No mock ERP/Jira call happens unless the case is PROPOSED and approve is called.
2. Approving twice returns 409 and makes no second call.
3. Reject makes no external call.
4. Tampering with a stored audit row makes verify return valid=false.
5. A request without a valid token returns 401 (Firebase auth mode).
6. The agent package has no import path to the gate or the mock enterprise systems.
Plus: end-to-end routing for A/B/C, verifier drops fabricated citations, guard beats a compliant-saying model,
invalid model output fails the case cleanly, analyst edits recompute deterministically.
"""
import ast
from pathlib import Path

from app.agents import llm as llm_mod
from tests.conftest import FakeLLM


def _run(client, key):
    c = client.post("/api/cases", json={"sample": key}).json()
    r = client.post(f"/api/cases/{c['id']}/process")
    assert r.status_code == 200, r.text
    return client.get(f"/api/cases/{c['id']}").json()


def test_abc_routing_end_to_end(client):
    expected = {"A": ("APPROVE_AND_STAGE", "LOW"), "B": ("ROUTE_TO_TREASURY", "MEDIUM"),
                "C": ("ROUTE_TO_LEGAL_AND_VP_APPROVAL", "CRITICAL")}
    for key, (action, level) in expected.items():
        case = _run(client, key)
        assert case["status"] == "PROPOSED", case.get("error")
        assert case["proposal"]["recommended_action"] == action
        assert case["riskLevel"] == level
        assert case["proposal"]["requires_human_signoff"] is True
        assert len(case["trace"]) >= 8
    liab = next(f for f in case["audit"]["findings"] if f["clause_category"] == "LIABILITY_CAP")
    assert "Section 8.3" in liab["policy_source"] and liab["severity"] == "CRITICAL"


def test_no_write_without_approval_and_idempotent_gate(client):
    from app import main
    case = _run(client, "C")
    cid = case["id"]
    assert main.mock().calls == []  # (1) nothing executed while PROPOSED
    assert client.get(f"/api/cases/{cid}/mock-status").json() == {"erp": [], "jira": []}
    r = client.post(f"/api/cases/{cid}/approve", json={"note": "ok"})
    assert r.status_code == 200 and r.json()["status"] == "EXECUTED"
    n = len(main.mock().calls)
    assert n == len(case["proposal"]["tasks"])
    erp = client.get(f"/api/cases/{cid}/mock-status").json()["erp"]
    assert any(e["status"] == "BLOCKED_ON_EXCEPTION" for e in erp)
    r2 = client.post(f"/api/cases/{cid}/approve")  # (2)
    assert r2.status_code == 409 and len(main.mock().calls) == n


def test_approve_before_processing_is_refused(client):
    from app import main
    c = client.post("/api/cases", json={"sample": "A"}).json()
    assert client.post(f"/api/cases/{c['id']}/approve").status_code == 409
    assert main.mock().calls == []


def test_reject_makes_no_external_call(client):  # (3)
    from app import main
    case = _run(client, "B")
    r = client.post(f"/api/cases/{case['id']}/reject", json={"note": "no"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert main.mock().calls == []
    assert client.post(f"/api/cases/{case['id']}/approve").status_code == 409


def test_audit_chain_verifies_and_detects_tampering(client, store):  # (4)
    case = _run(client, "C")
    cid = case["id"]
    client.post(f"/api/cases/{cid}/approve")
    v = client.get(f"/api/cases/{cid}/audit/verify").json()
    assert v["valid"] and v["rows"] == 5  # 3 agent runs + decision + execution
    sim = client.get(f"/api/cases/{cid}/audit/verify?simulate_tamper=2").json()
    assert not sim["valid"] and sim["firstBadRow"] == 2
    assert client.get(f"/api/cases/{cid}/audit/verify").json()["valid"]  # simulation did not modify storage
    store.audit[cid][3]["payload"]["decision"] = "REJECTED"  # real tampering of a stored row
    v = client.get(f"/api/cases/{cid}/audit/verify").json()
    assert not v["valid"] and v["firstBadRow"] == 4


def test_missing_token_is_401(store, monkeypatch):  # (5)
    from fastapi.testclient import TestClient
    from app import main
    from app.config import settings
    monkeypatch.setattr(settings, "auth_mode", "firebase")
    c = TestClient(main.app)
    assert c.get("/api/cases").status_code == 401
    assert c.get("/api/cases", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    assert c.get("/healthz").status_code == 200


def test_agents_cannot_import_gate_or_mock():  # (6)
    pkg = Path(__file__).resolve().parent.parent / "app" / "agents"
    banned = {"gate", "mock_enterprise"}
    for f in pkg.glob("*.py"):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""] + [a.name for a in node.names]
            for n in names:
                assert not any(b in n.split(".") for b in banned), f"{f.name} imports {n}"


def test_owner_isolation(client, store):
    case = _run(client, "A")
    store.cases[case["id"]]["ownerUid"] = "someone-else"
    assert client.get(f"/api/cases/{case['id']}").status_code == 404
    assert client.post(f"/api/cases/{case['id']}/approve").status_code == 404


def test_fabricated_citations_are_dropped(client):
    llm_mod.set_llm(FakeLLM(fabricate=True))
    case = _run(client, "C")
    assert case["status"] == "PROPOSED"
    assert len(case["audit"]["rejected_findings"]) >= 5
    for f in case["audit"]["findings"]:
        assert "unlimited liability for all suppliers" not in f["policy_snippet"]
    # Guards still catch the hard-limit breaches without a model citation
    assert case["proposal"]["recommended_action"] == "ROUTE_TO_LEGAL_AND_VP_APPROVAL"


def test_guard_beats_model_saying_compliant_and_injection(client):
    llm_mod.set_llm(FakeLLM(flip_compliant=True))
    case = _run(client, "J")
    assert case["proposal"]["recommended_action"] == "ROUTE_TO_LEGAL_AND_VP_APPROVAL"
    cats = {f["clause_category"]: f for f in case["audit"]["findings"]}
    assert cats["LIABILITY_CAP"]["violation_detected"] and "override" in cats["LIABILITY_CAP"]["guard_note"]
    assert "EMBEDDED_INSTRUCTIONS" in cats


def test_invalid_model_output_fails_case(client):
    llm_mod.set_llm(FakeLLM(invalid=1))
    c = client.post("/api/cases", json={"sample": "A"}).json()
    r = client.post(f"/api/cases/{c['id']}/process").json()
    assert r["status"] == "FAILED" and "schema" in r["error"]
    llm_mod.set_llm(FakeLLM())
    assert client.post(f"/api/cases/{c['id']}/process").json()["status"] == "PROPOSED"  # retry allowed from FAILED


def test_analyst_edit_recomputes_and_is_audited(client):
    case = _run(client, "B")
    cid = case["id"]
    r = client.post(f"/api/cases/{cid}/edit", json={"payment_terms_days": 45, "note": "Vendor agreed Net 45"})
    assert r.status_code == 200, r.text
    case = client.get(f"/api/cases/{cid}").json()
    assert case["proposal"]["recommended_action"] == "APPROVE_AND_STAGE"
    assert case["riskScore"] == 0 and case["proposal"]["edits"][0]["before"]["recommended_action"] == "ROUTE_TO_TREASURY"
    assert any(d["decision"] == "EDITED" for d in case["decisions"])
    assert client.get(f"/api/cases/{cid}/audit/verify").json()["valid"]
    # severity downgrade on C
    c = _run(client, "C")
    r = client.post(f"/api/cases/{c['id']}/edit", json={"severity_overrides": {"F-WARRANTY": "NONE"}})
    assert r.status_code == 200
    assert client.post(f"/api/cases/{c['id']}/edit", json={"severity_overrides": {"F-NOPE": "LOW"}}).status_code == 400


def test_upload_rejects_non_pdf(client):
    r = client.post("/api/cases/upload", files={"file": ("x.pdf", b"hello", "application/pdf")})
    assert r.status_code == 415


def test_upload_pdf_flow(client):
    data = (Path(__file__).resolve().parent.parent / "data" / "demo_docs" / "A_clean_sow_brightpath.pdf").read_bytes()
    c = client.post("/api/cases/upload", files={"file": ("mine.pdf", data, "application/pdf")}).json()
    assert client.post(f"/api/cases/{c['id']}/process").json()["status"] == "PROPOSED"
    assert client.get(f"/api/cases/{c['id']}/document").content[:4] == b"%PDF"
