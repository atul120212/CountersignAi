"""Firebase Authentication: every API route except /healthz and /api/config needs a valid ID token."""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException

from .config import settings

_app = None


@dataclass
class User:
    uid: str
    email: str | None
    name: str
    anonymous: bool


def _firebase():
    global _app
    if _app is None:
        import firebase_admin
        _app = firebase_admin.initialize_app(options={"projectId": settings.project} if settings.project else None)
    return _app


def current_user(authorization: str | None = Header(default=None)) -> User:
    if settings.auth_mode == "none":
        return User(uid="local-analyst", email="analyst@localhost", name="Local Analyst", anonymous=False)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Firebase ID token")
    token = authorization.split(" ", 1)[1].strip()
    try:
        from firebase_admin import auth as fb_auth
        claims = fb_auth.verify_id_token(token, app=_firebase())
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired Firebase ID token")
    provider = (claims.get("firebase") or {}).get("sign_in_provider")
    anonymous = provider == "anonymous"
    name = claims.get("name") or claims.get("email") or f"Guest analyst {claims['uid'][:6]}"
    return User(uid=claims["uid"], email=claims.get("email"), name=name, anonymous=anonymous)
