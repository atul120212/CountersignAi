"""Evaluation: run the 10 synthetic documents through a running OperateGuard deployment and measure.

  python eval/run_eval.py --base https://PROJECT.web.app --firebase-api-key AIza...   # deployed (guest sign-in)
  python eval/run_eval.py --base http://127.0.0.1:8080                                # local, AUTH_MODE=none

Writes eval/results.json and eval/report.md (generated; do not edit by hand).
Metrics: end-to-end latency p50/p95, per-entity extraction accuracy (12 entities), routing accuracy,
citation fidelity, gate safety checks and audit-chain verification.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ENTITIES = ["document_type", "vendor_name", "vendor_country", "total_value", "currency", "payment_terms_days",
            "milestone_billing", "liability_cap", "governing_law", "warranty_period_days", "auto_renewal",
            "auto_renewal_notice_days"]
COUNTRY = {"usa": "usa", "united states": "usa", "us": "usa", "united states of america": "usa",
           "uk": "united kingdom", "england": "united kingdom", "united kingdom": "united kingdom", "germany": "germany"}


def got(x: dict, k: str):
    return {"document_type": x["document_type"], "vendor_name": x["vendor"]["name"], "vendor_country": x["vendor"]["country"],
            "total_value": x["financial"]["total_value"], "currency": x["financial"]["currency"],
            "payment_terms_days": x["financial"]["payment_terms_days"], "milestone_billing": x["financial"]["milestone_billing"],
            "liability_cap": x["risk_clauses"]["liability_cap"], "governing_law": x["risk_clauses"]["governing_law"],
            "warranty_period_days": x["risk_clauses"]["warranty_period_days"], "auto_renewal": x["risk_clauses"]["auto_renewal"],
            "auto_renewal_notice_days": x["risk_clauses"]["auto_renewal_notice_days"]}[k]


def match(k: str, exp, val) -> bool:
    if exp is None:
        # For not-applicable / not-stated terms the system must not invent a value.
        if k == "milestone_billing":
            return val in (None, False)
        if k == "auto_renewal" and val is False:
            return True
        return val is None
    if val is None:
        return False
    if isinstance(exp, bool):
        return bool(val) == exp
    if isinstance(exp, (int, float)):
        try:
            return abs(float(val) - float(exp)) < 0.01
        except (TypeError, ValueError):
            return False
    e, v = str(exp).lower().strip(), str(val).lower().strip()
    if k == "vendor_country":
        return COUNTRY.get(e, e) == COUNTRY.get(v, v)
    if k in ("vendor_name", "governing_law"):
        return e in v or v in e
    return e == v


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    i = (len(xs) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--firebase-api-key", default="")
    ap.add_argument("--out", default=str(ROOT / "eval"))
    ap.add_argument("--only", default="", help="comma-separated sample keys, default all")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    headers = {}
    if a.firebase_api_key:
        r = httpx.post(f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={a.firebase_api_key}",
                       json={"returnSecureToken": True}, timeout=20)
        r.raise_for_status()
        headers["Authorization"] = f"Bearer {r.json()['idToken']}"
    c = httpx.Client(base_url=base, headers=headers, timeout=180)
    samples = c.get("/api/samples").json()
    if a.only:
        samples = [s for s in samples if s["key"] in a.only.upper().split(",")]

    rows = []
    for s in samples:
        gt = json.loads((ROOT / "data" / "ground_truth" / f"{s['key']}.json").read_text())
        case = c.post("/api/cases", json={"sample": s["key"]}).json()
        t0 = time.perf_counter()
        res = c.post(f"/api/cases/{case['id']}/process")
        wall = time.perf_counter() - t0
        full = c.get(f"/api/cases/{case['id']}").json()
        row = {"key": s["key"], "file": s["file"], "case_id": case["id"], "status": full["status"], "wall_s": round(wall, 2),
               "server_latency_ms": full.get("latencyMs"), "http": res.status_code, "error": full.get("error")}
        if full["status"] == "PROPOSED":
            x = full["extraction"]
            ent = {k: match(k, gt[k], got(x, k)) for k in ENTITIES}
            row["entities"] = ent
            row["entity_values"] = {k: got(x, k) for k in ENTITIES}
            row["action"] = full["proposal"]["recommended_action"]
            row["action_ok"] = row["action"] == gt["expected_action"]
            row["risk_level"] = full["riskLevel"]
            row["risk_ok"] = full["riskLevel"] == gt["expected_risk_level"]
            # citation fidelity: every displayed policy snippet must be verbatim inside a snippet retrieved in this run
            au = full["audit"]
            cited = [f for f in au["findings"] if f.get("retrieval_id") and f.get("policy_snippet")]
            ok = 0
            for f in cited:
                pool = au["retrieval"].get(f["clause_category"], [])
                if any(f["policy_snippet"].strip() in p["snippet"] for p in pool if p["retrieval_id"] == f["retrieval_id"]):
                    ok += 1
            row["citations"] = {"total": len(cited), "verbatim": ok, "dropped_by_verifier": len(au["rejected_findings"]),
                                "model_grounded": sum(1 for f in au["findings"] if not (f.get("guard_note") or "").startswith("No verified"))}
            if gt.get("expect_injection_flag"):
                row["injection_flagged"] = bool(x.get("suspicious_instructions"))
            row["evidence_verified"] = f"{sum(1 for e in x['evidence'] if e['verified'])}/{len(x['evidence'])}"
        rows.append(row)
        print(f"{s['key']}: {row['status']} {row.get('action')} {row['wall_s']}s "
              f"entities {sum(row.get('entities', {}).values())}/{len(ENTITIES)}" + (f" ERROR {row['error']}" if row.get("error") else ""))

    # Gate safety + audit on one fresh case (document C)
    safety = {}
    case = c.post("/api/cases", json={"sample": "C"}).json()
    cid = case["id"]
    safety["approve_before_processing_409"] = c.post(f"/api/cases/{cid}/approve").status_code == 409
    c.post(f"/api/cases/{cid}/process")
    safety["no_writes_before_approval"] = c.get(f"/api/cases/{cid}/mock-status").json() == {"erp": [], "jira": []}
    safety["approve_200"] = c.post(f"/api/cases/{cid}/approve").status_code == 200
    n_after = len(sum(c.get(f"/api/cases/{cid}/mock-status").json().values(), []))
    safety["second_approve_409"] = c.post(f"/api/cases/{cid}/approve").status_code == 409
    safety["no_duplicate_writes"] = len(sum(c.get(f"/api/cases/{cid}/mock-status").json().values(), [])) == n_after
    v = c.get(f"/api/cases/{cid}/audit/verify").json()
    safety["audit_chain_valid"] = v["valid"]
    safety["audit_rows"] = v["rows"]
    safety["tamper_simulation_detected"] = not c.get(f"/api/cases/{cid}/audit/verify?simulate_tamper=2").json()["valid"]
    r2 = c.post("/api/cases", json={"sample": "B"}).json()
    c.post(f"/api/cases/{r2['id']}/process")
    c.post(f"/api/cases/{r2['id']}/reject", json={"note": "eval"})
    safety["reject_no_writes"] = c.get(f"/api/cases/{r2['id']}/mock-status").json() == {"erp": [], "jira": []}
    if headers:
        safety["no_token_401"] = httpx.get(f"{base}/api/cases", timeout=20).status_code == 401

    ok_rows = [r for r in rows if r["status"] == "PROPOSED"]
    lat = [r["wall_s"] for r in ok_rows]
    per_entity = {k: sum(1 for r in ok_rows if r["entities"][k]) / len(ok_rows) for k in ENTITIES} if ok_rows else {}
    tot_ent = sum(sum(r["entities"].values()) for r in ok_rows)
    cit_total = sum(r["citations"]["total"] for r in ok_rows)
    cit_ok = sum(r["citations"]["verbatim"] for r in ok_rows)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "base_url": base,
        "documents": len(rows), "succeeded": len(ok_rows),
        "latency_s": {"p50": round(pct(lat, 0.5), 2) if lat else None, "p95": round(pct(lat, 0.95), 2) if lat else None,
                      "max": max(lat) if lat else None},
        "extraction_accuracy": round(tot_ent / (len(ok_rows) * len(ENTITIES)), 4) if ok_rows else None,
        "per_entity_accuracy": {k: round(v, 3) for k, v in per_entity.items()},
        "routing_accuracy": round(sum(r["action_ok"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "risk_level_accuracy": round(sum(r["risk_ok"] for r in ok_rows) / len(ok_rows), 4) if ok_rows else None,
        "citation_fidelity": round(cit_ok / cit_total, 4) if cit_total else None,
        "citations": {"total": cit_total, "verbatim": cit_ok,
                      "dropped_by_verifier": sum(r["citations"]["dropped_by_verifier"] for r in ok_rows)},
        "safety": safety,
    }
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=2), encoding="utf-8")
    md = [f"# OperateGuard AI - evaluation report", "",
          f"Generated by `eval/run_eval.py` at {summary['generated_at']} against `{base}`. Do not edit by hand.",
          "All documents are synthetic (see `data/`).", "",
          "| Metric | Result | Target |", "|---|---|---|",
          f"| Documents processed | {summary['succeeded']}/{summary['documents']} | 10/10 |",
          f"| End-to-end latency p50 / p95 | {summary['latency_s']['p50']} s / {summary['latency_s']['p95']} s | < 20 s |",
          f"| Extraction accuracy (12 entities) | {summary['extraction_accuracy'] and round(summary['extraction_accuracy'] * 100, 1)}% | > 90% |",
          f"| Routing accuracy (recommended action) | {summary['routing_accuracy'] and round(summary['routing_accuracy'] * 100, 1)}% | 100% |",
          f"| Risk level accuracy | {summary['risk_level_accuracy'] and round(summary['risk_level_accuracy'] * 100, 1)}% | 100% |",
          f"| Citation fidelity (verbatim, retrieved this run) | {summary['citation_fidelity'] and round(summary['citation_fidelity'] * 100, 1)}% ({cit_ok}/{cit_total}) | 100% |",
          f"| Ungrounded findings dropped by verifier | {summary['citations']['dropped_by_verifier']} | reported |",
          f"| Unapproved writes | {0 if safety.get('no_writes_before_approval') and safety.get('reject_no_writes') else 'FAIL'} | 0 |",
          f"| Audit chain verifiable | {safety.get('audit_chain_valid')} ({safety.get('audit_rows')} rows), tamper detected: {safety.get('tamper_simulation_detected')} | yes |",
          "", "## Per document", "", "| Doc | Status | Action | Expected OK | Risk | Latency (s) | Entities | Evidence quotes verified |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| {r['key']} | {r['status']} | {r.get('action', '-')} | {r.get('action_ok', '-')} | {r.get('risk_level', '-')} | {r['wall_s']} | "
                  f"{sum(r.get('entities', {}).values())}/{len(ENTITIES)} | {r.get('evidence_verified', '-')} |")
    md += ["", "## Per entity accuracy", "", "| Entity | Accuracy |", "|---|---|"]
    md += [f"| {k} | {round(v * 100, 1)}% |" for k, v in summary["per_entity_accuracy"].items()]
    md += ["", "## Safety checks", "", "| Check | Pass |", "|---|---|"] + [f"| {k} | {v} |" for k, v in safety.items()]
    misses = [(r["key"], k, r["entity_values"][k]) for r in ok_rows for k, okv in r["entities"].items() if not okv]
    if misses:
        md += ["", "## Extraction misses", ""] + [f"- {k}: `{f}` = `{v}`" for k, f, v in misses]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
