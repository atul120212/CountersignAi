"""Mock enterprise systems (ERP + Jira Service Management).

Only ``gate.py`` imports this module. The agent pipeline (``app.agents``) has no import
path to it; tests/test_safety.py enforces that statically (design principle D1).

If MOCK_SERVICE_URL is set, calls go to a separately deployed mock service over an
OIDC-authenticated request instead of the in-process implementation.
"""
from __future__ import annotations

from .config import settings
from .store import Store, new_id, now_ms


class MockEnterprise:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.calls: list[dict] = []  # observed by the safety tests

    def execute(self, case_id: str, task: dict, approved_by: str) -> dict:
        self.calls.append({"case_id": case_id, "task_id": task["task_id"]})
        if settings.mock_service_url:
            return self._remote(case_id, task, approved_by)
        p = task["parameters"]
        if task["target_system"] == "MOCK_ERP":
            rec = {"id": new_id("ERP"), "case_id": case_id, "task_id": task["task_id"], "type": task["action_type"],
                   "vendor": p.get("vendor"), "amount": p.get("amount"), "currency": p.get("currency"),
                   "status": p.get("status", "STAGED"), "approvers": p.get("approvers"),
                   "payment_terms_days": p.get("payment_terms_days"), "created_by": approved_by, "ts": now_ms()}
            if task["action_type"] == "STAGE_PURCHASE_ORDER":
                rec["po_number"] = "PO-" + rec["id"][-6:]
            self.store.put_mock("mock_erp", rec["id"], rec)
            return rec
        if task["target_system"] == "MOCK_JIRA":
            rec = {"id": new_id("OPS"), "case_id": case_id, "task_id": task["task_id"], "type": task["action_type"],
                   "key": "OPSX-" + str(now_ms())[-5:], "summary": p.get("summary"), "description": p.get("description"),
                   "priority": p.get("priority"), "assigned_group": p.get("assigned_group"), "status": "OPEN",
                   "created_by": approved_by, "ts": now_ms()}
            self.store.put_mock("mock_jira", rec["id"], rec)
            return rec
        raise ValueError(f"unknown target system {task['target_system']}")

    def _remote(self, case_id: str, task: dict, approved_by: str) -> dict:
        import google.auth.transport.requests
        import google.oauth2.id_token
        import httpx
        token = google.oauth2.id_token.fetch_id_token(google.auth.transport.requests.Request(), settings.mock_service_url)
        r = httpx.post(f"{settings.mock_service_url}/execute", json={"case_id": case_id, "task": task, "approved_by": approved_by},
                       headers={"Authorization": f"Bearer {token}"}, timeout=20)
        r.raise_for_status()
        return r.json()

    def status(self, case_id: str) -> dict:
        return {"erp": self.store.list_mock("mock_erp", case_id), "jira": self.store.list_mock("mock_jira", case_id)}
