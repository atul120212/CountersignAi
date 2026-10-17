"""OperateGuard AI - API, approval gate and web console (one Cloud Run service)."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import audit, gate
from .agents import pipeline
from .auth import User, current_user
from .config import DATA_DIR, WEB_DIR, settings
from .mock_enterprise import MockEnterprise
from .schemas import CreateCaseRequest, DecisionRequest, EditRequest, Status
from .state import transition
from .store import ConflictError, get_blobs, get_store, new_id, now_ms

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("operateguard")

app = FastAPI(title="OperateGuard AI", version="1.0.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
CATALOG = json.loads((DATA_DIR / "demo_docs" / "catalog.json").read_text(encoding="utf-8"))
_mock: MockEnterprise | None = None


def mock() -> MockEnterprise:
    global _mock
    if _mock is None or _mock.store is not get_store():
        _mock = MockEnterprise(get_store())
    return _mock


# Simple per-user rate limit so a public demo cannot run up the Gemini bill.
_runs: dict[str, deque] = defaultdict(deque)
RUNS_PER_HOUR = settings.runs_per_hour


def _rate_limit(uid: str) -> None:
    q = _runs[uid]
    cutoff = time.time() - 3600
    while q and q[0] < cutoff:
        q.popleft()
    if len(q) >= RUNS_PER_HOUR:
        raise HTTPException(429, f"Demo limit reached: {RUNS_PER_HOUR} documents per hour")
    q.append(time.time())


def _own(case_id: str, user: User) -> dict:
    case = get_store().get_case(case_id)
    if case is None or case.get("ownerUid") != user.uid:
        raise HTTPException(404, "Case not found")
    return case


def _summary(c: dict) -> dict:
    keys = ["id", "status", "filename", "sample", "vendor", "value", "currency", "documentType", "riskScore", "riskLevel",
            "recommendedAction", "summary", "createdAt", "updatedAt", "latencyMs", "error"]
    return {k: c.get(k) for k in keys}


# ------------------------------------------------------------------ public
@app.get("/healthz")
def healthz():
    return {"ok": True, "store": settings.store_backend, "auth": settings.auth_mode,
            "gemini": "vertex" if settings.use_vertex else "api-key", "warnings": settings.warnings()}


@app.get("/api/config")
def config():
    return settings.public()


@app.get("/api/samples")
def samples():
    return CATALOG


@app.get("/api/samples/{key}/pdf")
def sample_pdf(key: str):
    item = next((c for c in CATALOG if c["key"] == key.upper()), None)
    if not item:
        raise HTTPException(404, "Unknown sample")
    return FileResponse(DATA_DIR / "demo_docs" / item["file"], media_type="application/pdf")


@app.get("/api/policies")
def policies():
    corpus = json.loads((DATA_DIR / "policies" / "corpus.json").read_text(encoding="utf-8"))
    return corpus


@app.get("/api/policies/{fname}")
def policy_pdf(fname: str):
    path = DATA_DIR / "policies" / fname
    if path.suffix != ".pdf" or path.parent != DATA_DIR / "policies" or not path.exists():
        raise HTTPException(404, "Unknown policy")
    return FileResponse(path, media_type="application/pdf")


# ------------------------------------------------------------------ cases
def _new_case(user: User, filename: str, uri: str, sample: str | None) -> dict:
    case = {"id": new_id("CASE"), "ownerUid": user.uid, "ownerName": user.name, "status": Status.UPLOADED.value,
            "filename": filename, "documentUri": uri, "sample": sample, "createdAt": now_ms(), "updatedAt": now_ms(),
            "lastAuditHash": None, "auditRows": 0}
    get_store().create_case(case)
    return case


@app.post("/api/cases")
def create_case(req: CreateCaseRequest, user: User = Depends(current_user)):
    item = next((c for c in CATALOG if c["key"] == (req.sample or "").upper()), None)
    if not item:
        raise HTTPException(400, "Provide a sample key (A-J) or use /api/cases/upload")
    return _summary(_new_case(user, item["file"], f"sample://{item['file']}", item["key"]))


@app.post("/api/cases/upload")
async def upload_case(file: UploadFile = File(...), user: User = Depends(current_user)):
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"PDF larger than {settings.max_upload_mb} MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(415, "Only PDF files are accepted")
    name = (file.filename or "document.pdf").replace("/", "_").replace("\\", "_")[:120]
    case_id_hint = new_id("DOC")
    uri = await asyncio.to_thread(get_blobs().put, f"inbound/{user.uid}/{case_id_hint}.pdf", data)
    return _summary(_new_case(user, name, uri, None))


@app.post("/api/cases/{case_id}/process")
async def process_case(case_id: str, user: User = Depends(current_user)):
    _own(case_id, user)
    _rate_limit(user.uid)
    try:
        await asyncio.to_thread(transition, get_store(), case_id, Status.PROCESSING, {"error": None, "processingStartedAt": now_ms()})
    except ConflictError as e:
        raise HTTPException(409, str(e))
    # Runs inside the request (Cloud Run keeps CPU allocated); the UI follows progress live via Firestore.
    case = await pipeline.run(get_store(), case_id)
    return _summary(case)


@app.get("/api/cases")
def list_cases(user: User = Depends(current_user)):
    return [_summary(c) for c in get_store().list_cases(user.uid)]


@app.get("/api/cases/{case_id}")
def get_case(case_id: str, user: User = Depends(current_user)):
    case = _own(case_id, user)
    store = get_store()
    case.pop("documentUri", None)
    case["trace"] = store.list_trace(case_id)
    case["decisions"] = store.list_decisions(case_id)
    return case


@app.get("/api/cases/{case_id}/document")
def get_document(case_id: str, user: User = Depends(current_user)):
    case = _own(case_id, user)
    data = get_blobs().get(case["documentUri"])
    return Response(content=data, media_type="application/pdf", headers={"Cache-Control": "private, max-age=300"})


@app.post("/api/cases/{case_id}/approve")
def approve(case_id: str, req: DecisionRequest | None = None, user: User = Depends(current_user)):
    _own(case_id, user)
    try:
        case = gate.approve(get_store(), mock(), case_id, user, (req.note if req else ""))
    except ConflictError as e:
        raise HTTPException(409, f"Not approvable: {e}")
    return _summary(case)


@app.post("/api/cases/{case_id}/reject")
def reject(case_id: str, req: DecisionRequest | None = None, user: User = Depends(current_user)):
    _own(case_id, user)
    try:
        case = gate.reject(get_store(), case_id, user, (req.note if req else ""))
    except ConflictError as e:
        raise HTTPException(409, f"Not rejectable: {e}")
    return _summary(case)


@app.post("/api/cases/{case_id}/edit")
def edit(case_id: str, req: EditRequest, user: User = Depends(current_user)):
    case = _own(case_id, user)
    if case["status"] != Status.PROPOSED.value:
        raise HTTPException(409, f"Only PROPOSED cases can be edited (case is {case['status']})")
    try:
        updated = gate.edit(get_store(), case_id, user, req)
    except ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return _summary(updated)


@app.get("/api/cases/{case_id}/audit")
def audit_rows(case_id: str, user: User = Depends(current_user)):
    _own(case_id, user)
    return get_store().list_audit(case_id)


@app.get("/api/cases/{case_id}/audit/verify")
def audit_verify(case_id: str, simulate_tamper: int | None = Query(None, ge=1), user: User = Depends(current_user)):
    _own(case_id, user)
    return audit.verify(get_store(), case_id, simulate_tamper)


@app.get("/api/cases/{case_id}/mock-status")
def mock_status(case_id: str, user: User = Depends(current_user)):
    _own(case_id, user)
    return mock().status(case_id)


@app.exception_handler(KeyError)
async def _key_error(_, exc):
    return JSONResponse({"detail": f"Not found: {exc}"}, status_code=404)


# ------------------------------------------------------------------ web console
# On Cloud Run / local the service also serves the console. On Vercel the CDN serves web/ directly.
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
