"""Runtime configuration. Everything comes from environment variables; nothing is hardcoded secret."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"
ON_VERCEL = bool(os.getenv("VERCEL"))


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _bootstrap_service_account() -> None:
    """Hosts without Google metadata credentials (e.g. Vercel) pass a service-account key in
    GCP_SERVICE_ACCOUNT_JSON (raw JSON or base64). It is written to a temp file and exposed as
    GOOGLE_APPLICATION_CREDENTIALS, so Firestore, Firebase Admin, Cloud Storage, BigQuery and
    Vertex AI all pick it up as Application Default Credentials."""
    raw = os.getenv("GCP_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw or os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        return
    if not raw.startswith("{"):
        raw = base64.b64decode(raw).decode("utf-8")
    info = json.loads(raw)
    path = Path(tempfile.gettempdir()) / "operateguard-sa.json"
    path.write_text(json.dumps(info), encoding="utf-8")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = str(path)
    if not os.getenv("GOOGLE_CLOUD_PROJECT") and info.get("project_id"):
        os.environ["GOOGLE_CLOUD_PROJECT"] = info["project_id"]


_bootstrap_service_account()


class Settings:
    def __init__(self) -> None:
        self.project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
        # Gemini: Vertex AI (ADC) by default, or the Gemini API when GEMINI_API_KEY is set.
        self.gemini_location = os.getenv("GEMINI_LOCATION", "global")
        self.use_vertex = _bool("GOOGLE_GENAI_USE_VERTEXAI", default=not bool(os.getenv("GEMINI_API_KEY")))
        self.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        # Model IDs are version-sensitive: read from env, try fallbacks in order if a model is unavailable.
        self.flash_model = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")
        self.audit_model = os.getenv("GEMINI_AUDIT_MODEL", self.flash_model)
        self.model_fallbacks = [m.strip() for m in os.getenv(
            "GEMINI_MODEL_FALLBACKS", "gemini-3.8-flash,gemini-3.5-flash-lite,gemini-flash-latest").split(",") if m.strip()]
        self.thinking_level = os.getenv("GEMINI_THINKING_LEVEL", "low")  # "" disables the thinking config
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "gemini-embedding-001")
        self.use_embeddings = _bool("USE_EMBEDDINGS", True)

        # Storage backends
        self.store_backend = os.getenv("STORE_BACKEND", "firestore" if self.project else "memory")
        self.gcs_bucket = os.getenv("GCS_BUCKET", "")
        self.bq_table = os.getenv("BQ_AUDIT_TABLE", "")  # e.g. project.operateguard.audit_log (optional mirror)

        # Auth: "firebase" verifies Firebase ID tokens (per-analyst queues).
        #       "none" = shared demo workspace with one demo analyst (local dev or a quick public demo).
        raw = os.getenv("FIREBASE_WEB_CONFIG", "").strip()
        self.firebase_web_config = json.loads(raw) if raw else None
        self.auth_mode = os.getenv("AUTH_MODE", "firebase" if self.firebase_web_config else "none")
        self.demo_user_name = os.getenv("DEMO_USER_NAME", "Demo Analyst" if ON_VERCEL else "Local Analyst")

        # Vercel serverless functions accept request bodies up to 4.5 MB.
        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "4" if ON_VERCEL else "15"))
        self.runs_per_hour = int(os.getenv("RUNS_PER_HOUR", "120" if self.auth_mode == "none" else "40"))
        self.mock_service_url = os.getenv("MOCK_SERVICE_URL", "")  # optional separate mock-enterprise service

    def warnings(self) -> list[str]:
        w = []
        if ON_VERCEL and self.store_backend != "firestore":
            w.append("Storage is not configured: set GCP_SERVICE_ACCOUNT_JSON so cases persist in Firestore "
                     "(serverless instances do not share memory).")
        if not self.gemini_api_key and not (self.use_vertex and self.project):
            w.append("Gemini is not configured: set GEMINI_API_KEY (or GOOGLE_GENAI_USE_VERTEXAI=true with a service account).")
        return w

    def public(self) -> dict:
        firebase = self.firebase_web_config if self.auth_mode == "firebase" else None
        return {
            "authMode": self.auth_mode,
            "firebase": firebase,
            "realtime": self.store_backend == "firestore" and bool(firebase),
            "demoUser": self.demo_user_name,
            "models": {"extraction": self.flash_model, "audit": self.audit_model},
            "maxUploadMb": self.max_upload_mb,
            "warnings": self.warnings(),
            "host": "vercel" if ON_VERCEL else ("cloud-run" if os.getenv("K_SERVICE") else "local"),
        }


settings = Settings()
