"""Vercel serverless entrypoint. vercel.json rewrites /api/* and /healthz here; the FastAPI app
sees the original path. Static console files (web/) are served by Vercel's CDN."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402,F401  (Vercel looks for the ASGI variable named `app`)
