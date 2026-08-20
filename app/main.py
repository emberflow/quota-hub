from __future__ import annotations

import inspect
import hmac
import hashlib
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .collect import collect_cached
from .github_api import list_repos, repo_tree, valid_ref, valid_repo

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

app = FastAPI(title="quota-hub", docs_url=None, redoc_url=None)
_allowed_hosts = [
    host.strip()
    for host in os.environ.get("QUOTA_HUB_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]").split(",")
    if host.strip()
]
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)
app.state.shutdown_callback = None
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_SESSION_COOKIE = "quota_hub_session"
_SESSION_SECRET = (os.environ.get("QUOTA_HUB_SESSION_SECRET") or secrets.token_urlsafe(32)).encode()
_SESSION_SECURE = os.environ.get("QUOTA_HUB_HTTPS_ONLY", "").lower() in {"1", "true", "yes"}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    session_token = getattr(request.state, "new_session_token", None)
    if session_token:
        response.set_cookie(
            _SESSION_COOKIE,
            _signed_session(session_token),
            httponly=True,
            samesite="strict",
            secure=_SESSION_SECURE,
        )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; base-uri 'none'; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'",
    )
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        raise HTTPException(403, "missing Origin")
    parsed = urlsplit(origin)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(403, "invalid Origin")
    host = request.headers.get("host", "").lower()
    if parsed.netloc.lower() != host or parsed.path or parsed.query or parsed.fragment:
        raise HTTPException(403, "cross-origin request denied")
    if not _has_local_session(request):
        raise HTTPException(403, "missing local session")


def _start_local_session(request: Request) -> None:
    # This signed, strict, HttpOnly cookie is a second local-browser boundary
    # for shutdown in addition to the Origin comparison.  It intentionally
    # carries no user data and is invalidated when the server restarts.
    if not _has_local_session(request):
        request.state.new_session_token = secrets.token_urlsafe(24)


def _signed_session(token: str) -> str:
    signature = hmac.new(_SESSION_SECRET, token.encode(), hashlib.sha256).hexdigest()
    return f"{token}.{signature}"


def _has_local_session(request: Request) -> bool:
    value = request.cookies.get(_SESSION_COOKIE, "")
    token, separator, signature = value.rpartition(".")
    return bool(separator and token and hmac.compare_digest(signature, _signed_session(token).rpartition(".")[2]))


@app.get("/")
def index(request: Request):
    _start_local_session(request)
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def api_health():
    return {"ok": True, "app": "quota-hub"}


@app.get("/favicon.ico")
def favicon():
    ico = STATIC / "quota-hub.ico"
    if ico.exists():
        return FileResponse(ico, media_type="image/x-icon")
    png = STATIC / "quota-hub.png"
    if png.exists():
        return FileResponse(png, media_type="image/png")
    raise HTTPException(404, "no icon")


@app.get("/api/quota")
def api_quota(request: Request, force: bool = False):
    _start_local_session(request)
    return collect_cached(force=force)


@app.post("/api/shutdown")
async def api_shutdown(request: Request):
    _require_same_origin(request)
    callback = getattr(request.app.state, "shutdown_callback", None)
    if not callable(callback):
        raise HTTPException(503, "shutdown is not configured")
    result = callback()
    if inspect.isawaitable(result):
        await result
    return {"ok": True}


@app.get("/api/github/repos")
def api_repos():
    return list_repos()


@app.get("/api/github/tree")
def api_tree(repo: str = Query(..., min_length=3), ref: str | None = None):
    if not valid_repo(repo):
        raise HTTPException(400, "invalid repo")
    if ref is not None and ref.strip() and ref.strip() != "HEAD" and not valid_ref(ref.strip()):
        raise HTTPException(400, "invalid ref")
    return repo_tree(repo, ref)
