import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode, urlparse

import requests
from fastapi import Request, Response

import database
from config import AppConfig

SESSION_COOKIE_NAME = "nong_chok_session"
CSRF_COOKIE_NAME = "nong_chok_csrf"
OAUTH_STATE_COOKIE_NAME = "nong_chok_oauth_state"
OAUTH_STATE_TTL_SECONDS = 600

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def is_oauth_configured(cfg: AppConfig) -> bool:
    """Return True only when Google OAuth client, session secret, and email allowlist are configured."""
    return bool(
        (cfg.google_client_id or "").strip()
        and (cfg.google_client_secret or "").strip()
        and (cfg.auth_session_secret or "").strip()
        and cfg.allowed_emails
    )


def _extract_origin(url: str) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        return ""
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def resolve_external_base_url(request: Request, cfg: AppConfig) -> str:
    """Resolve the trusted external base URL without trusting internal container/proxy hosts."""
    configured = (cfg.public_base_url or "").strip().rstrip("/")
    if configured:
        return configured

    hostname = (getattr(request.url, "hostname", None) or "").lower()
    port = getattr(request.url, "port", None)
    scheme = "https" if getattr(request.url, "scheme", "http") == "https" else "http"
    if hostname not in {"127.0.0.1", "localhost"}:
        hostname = "127.0.0.1"
        if port is None:
            port = 8765

    if port:
        return f"{scheme}://{hostname}:{port}"
    return f"{scheme}://{hostname}"


def should_use_secure_cookie(request: Request, cfg: AppConfig) -> bool:
    """Force Secure cookies in production or whenever external traffic uses HTTPS."""
    if (cfg.public_base_url or "").strip().lower().startswith("https://"):
        return True
    if (cfg.app_env or "").strip().lower() == "production":
        return True
    return getattr(request.url, "scheme", "http") == "https"


def get_allowed_origins(request: Request, cfg: AppConfig) -> Tuple[str, ...]:
    """Return trusted request origins allowed to issue state-changing calls."""
    origin = _extract_origin(resolve_external_base_url(request, cfg))
    return (origin,) if origin else ()


def get_cors_allowed_origins(cfg: AppConfig) -> Tuple[str, ...]:
    """Return strict CORS origins for authenticated mode."""
    origins = []
    pub_origin = _extract_origin(cfg.public_base_url)
    if pub_origin:
        origins.append(pub_origin)
    if (cfg.app_env or "").strip().lower() != "production" or not pub_origin:
        for local_origin in ("http://127.0.0.1:8765", "http://localhost:8765"):
            if local_origin not in origins:
                origins.append(local_origin)
    return tuple(origins)


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(encoded: str) -> Optional[bytes]:
    if not encoded:
        return None
    padding = "=" * (-len(encoded) % 4)
    try:
        return base64.urlsafe_b64decode(encoded + padding)
    except Exception:
        return None


def sign_payload(payload: Dict[str, Any], secret: str) -> str:
    """Serialize payload as Base64URL JSON and sign with HMAC-SHA256."""
    clean_secret = (secret or "").strip()
    if not clean_secret:
        raise ValueError("AUTH_SESSION_SECRET is required for signing tokens")
    body_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    b64_data = _b64url_encode(body_bytes)
    sig_bytes = hmac.new(clean_secret.encode("utf-8"), b64_data.encode("ascii"), hashlib.sha256).digest()
    return f"{b64_data}.{_b64url_encode(sig_bytes)}"


def verify_signed_payload(token: str, secret: str) -> Optional[Dict[str, Any]]:
    """Verify HMAC-SHA256 signature and expiration timestamp; return payload dict or None."""
    clean_secret = (secret or "").strip()
    if not token or not clean_secret or "." not in token:
        return None
    parts = token.split(".", 1)
    if len(parts) != 2:
        return None
    b64_data, b64_sig = parts
    expected_sig = _b64url_encode(
        hmac.new(clean_secret.encode("utf-8"), b64_data.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(expected_sig, b64_sig):
        return None

    raw_bytes = _b64url_decode(b64_data)
    if raw_bytes is None:
        return None
    try:
        payload = json.loads(raw_bytes.decode("utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    exp = payload.get("exp")
    if not isinstance(exp, (int, float)) or float(exp) <= time.time():
        return None
    return payload


def authenticate_request(request: Request, cfg: AppConfig) -> Optional[Dict[str, Any]]:
    """Authenticate request via signed cookie, email allowlist, and revocable SQLite session."""
    if not (cfg.auth_session_secret or "").strip() or not cfg.allowed_emails:
        return None
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    payload = verify_signed_payload(token, cfg.auth_session_secret)
    if not payload:
        return None

    email = str(payload.get("email") or "").strip().lower()
    sid = str(payload.get("sid") or "").strip()
    if not email or not sid or email not in cfg.allowed_emails:
        return None

    session_row = database.get_active_auth_session(sid, db_path=cfg.db_path)
    if not session_row:
        return None
    row_email = str(session_row.get("email") or "").strip().lower()
    if row_email != email or row_email not in cfg.allowed_emails:
        return None
    return session_row


def validate_csrf_and_origin(request: Request, cfg: AppConfig, session: Dict[str, Any]) -> bool:
    """Enforce Origin/Referer allowlist and session-bound X-CSRF-Token on state changes."""
    allowed_origins = get_allowed_origins(request, cfg)
    raw_origin = (request.headers.get("origin") or "").strip()
    raw_referer = (request.headers.get("referer") or "").strip()
    candidate_origin = _extract_origin(raw_origin) if raw_origin else _extract_origin(raw_referer)
    if not candidate_origin or candidate_origin not in allowed_origins:
        return False

    header_csrf = (request.headers.get("x-csrf-token") or "").strip()
    expected_csrf = str(session.get("csrf_token") or "").strip()
    if not header_csrf or not expected_csrf:
        return False
    return hmac.compare_digest(header_csrf, expected_csrf)


def build_google_authorize_url(redirect_uri: str, state: str, cfg: AppConfig) -> str:
    """Build the Google OAuth 2.0 authorization URL with forced account selection."""
    query = urlencode(
        {
            "client_id": cfg.google_client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            "prompt": "select_account",
        }
    )
    return f"{GOOGLE_AUTH_URL}?{query}"


def exchange_google_code(code: str, redirect_uri: str, cfg: AppConfig) -> Dict[str, Any]:
    """Exchange Google OAuth authorization code for tokens."""
    resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": cfg.google_client_id,
            "client_secret": cfg.google_client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_google_userinfo(access_token: str) -> Dict[str, Any]:
    """Fetch verified Google user profile from OpenID Connect userinfo endpoint."""
    resp = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def issue_oauth_state_cookie(response: Response, request: Request, cfg: AppConfig) -> str:
    """Generate and bind a signed OAuth CSRF state cookie to the user's browser."""
    state = secrets.token_urlsafe(32)
    signed_state = sign_payload(
        {"state": state, "exp": int(time.time()) + OAUTH_STATE_TTL_SECONDS},
        cfg.auth_session_secret,
    )
    response.set_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        value=signed_state,
        httponly=True,
        samesite="lax",
        secure=should_use_secure_cookie(request, cfg),
        max_age=OAUTH_STATE_TTL_SECONDS,
        path="/",
    )
    return state


def verify_oauth_state(request: Request, cfg: AppConfig, query_state: str) -> bool:
    """Validate OAuth callback state against signed OAuth state cookie before calling Google."""
    clean_query_state = (query_state or "").strip()
    raw_cookie = request.cookies.get(OAUTH_STATE_COOKIE_NAME, "")
    if not clean_query_state or not raw_cookie or not (cfg.auth_session_secret or "").strip():
        return False
    payload = verify_signed_payload(raw_cookie, cfg.auth_session_secret)
    if not payload:
        return False
    expected_state = str(payload.get("state") or "").strip()
    if not expected_state:
        return False
    return hmac.compare_digest(expected_state, clean_query_state)


def clear_oauth_state_cookie(response: Response, request: Request, cfg: AppConfig) -> None:
    response.delete_cookie(
        key=OAUTH_STATE_COOKIE_NAME,
        path="/",
        secure=should_use_secure_cookie(request, cfg),
        samesite="lax",
    )


def issue_login_session(
    response: Response,
    request: Request,
    cfg: AppConfig,
    email: str,
    name: str,
    picture: str,
) -> Dict[str, Any]:
    """Create a revocable SQLite auth session and set signed HttpOnly session + CSRF cookies."""
    clean_email = (email or "").strip().lower()
    sid = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    ttl = int(cfg.auth_session_ttl_seconds)
    exp_ts = int(time.time()) + ttl
    exp_iso = datetime.fromtimestamp(exp_ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    session_row = database.create_auth_session(
        session_id=sid,
        email=clean_email,
        name=name,
        picture=picture,
        csrf_token=csrf_token,
        expires_at=exp_iso,
        db_path=cfg.db_path,
    )
    signed_session = sign_payload(
        {"sid": sid, "email": clean_email, "exp": exp_ts},
        cfg.auth_session_secret,
    )
    secure_flag = should_use_secure_cookie(request, cfg)
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=signed_session,
        httponly=True,
        samesite="lax",
        secure=secure_flag,
        max_age=ttl,
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        samesite="lax",
        secure=secure_flag,
        max_age=ttl,
        path="/",
    )
    return session_row


def revoke_login_session(response: Response, request: Request, cfg: AppConfig) -> bool:
    """Revoke the active SQLite session server-side and clear browser cookies."""
    revoked = False
    token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if token and (cfg.auth_session_secret or "").strip():
        payload = verify_signed_payload(token, cfg.auth_session_secret)
        if payload:
            sid = str(payload.get("sid") or "").strip()
            if sid:
                revoked = database.revoke_auth_session(sid, db_path=cfg.db_path)

    secure_flag = should_use_secure_cookie(request, cfg)
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/", secure=secure_flag, samesite="lax")
    response.delete_cookie(key=CSRF_COOKIE_NAME, path="/", secure=secure_flag, samesite="lax")
    return revoked
