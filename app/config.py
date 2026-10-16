"""Runtime configuration. Everything comes from environment variables; nothing is hardcoded secret."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


class Settings:
    def __init__(self) -> None:
        self.project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT") or ""
        # Gemini on Vertex AI. "global" serves the newest Gemini models.
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

        # Auth: "firebase" verifies Firebase ID tokens; "none" is for local development only.
        self.auth_mode = os.getenv("AUTH_MODE", "firebase" if self.project else "none")
        raw = os.getenv("FIREBASE_WEB_CONFIG", "")
        self.firebase_web_config = json.loads(raw) if raw else None

        self.max_upload_mb = int(os.getenv("MAX_UPLOAD_MB", "15"))
        self.mock_service_url = os.getenv("MOCK_SERVICE_URL", "")  # optional separate mock-enterprise service

    def public(self) -> dict:
        return {
            "authMode": self.auth_mode,
            "firebase": self.firebase_web_config if self.auth_mode == "firebase" else None,
            "realtime": self.store_backend == "firestore" and bool(self.firebase_web_config),
            "models": {"extraction": self.flash_model, "audit": self.audit_model},
            "maxUploadMb": self.max_upload_mb,
        }


settings = Settings()
