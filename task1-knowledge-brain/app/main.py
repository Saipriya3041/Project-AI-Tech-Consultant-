import base64

import hashlib
import hmac
import json
import logging
import os
import re
import smtplib
import ssl
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from threading import Lock
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from xml.etree.ElementTree import ParseError

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from app.auth import (
    add_sitemap,
    authenticate_user,
    create_session,
    create_password_reset,
    create_user,
    delete_session,
    get_session_user,
    get_missing_retention_checks,
    get_sitemap_schedule,
    list_sitemaps,
    remove_sitemap,
    reset_password,
    set_sitemap_schedule,
    set_missing_retention_checks,
)
from ingestion.crawler import finalize_sitemap_scan, run_ingestion
from ingestion.sitemap import fetch_sitemap_details
from retrieval.search import query_knowledge
from retrieval.store import (
    create_escalation,
    get_ingestion_status,
    get_article_change_history,
    list_archived_articles,
    resolve_escalation,
    restore_archived_article,
)

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8081",
        "http://localhost:8081",
        "http://127.0.0.1:8010",
        "http://localhost:8010",
        "http://127.0.0.1:8012",
        "http://localhost:8012",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

SESSION_COOKIE = "knowledge_session"
logger = logging.getLogger(__name__)
_ingestion_progress_lock = Lock()
_ingestion_run_lock = Lock()
_ingestion_progress: dict = {
    "user_id": None,
    "active": False,
    "status": "idle",
    "completed": 0,
    "total": 0,
    "percent": 0,
    "started_at": None,
    "estimated_seconds_remaining": None,
}
_ingestion_started_monotonic: float | None = None


def _update_ingestion_progress(
    user_id: int,
    *,
    active: bool,
    status: str,
    completed: int = 0,
    total: int = 0,
    started_at: str | None = None,
) -> None:
    global _ingestion_started_monotonic
    safe_total = max(0, total)
    safe_completed = max(0, completed)
    with _ingestion_progress_lock:
        if _ingestion_progress["active"] and _ingestion_progress["user_id"] != user_id:
            return
        if status in {"discovering", "running"} and _ingestion_started_monotonic is None:
            _ingestion_started_monotonic = time.monotonic()
        elapsed = (
            time.monotonic() - _ingestion_started_monotonic
            if _ingestion_started_monotonic is not None
            else 0
        )
        remaining = (
            max(0, round(elapsed / safe_completed * (safe_total - safe_completed)))
            if safe_completed and safe_total > safe_completed
            else 0 if not active else None
        )
        _ingestion_progress.update({
            "user_id": user_id,
            "active": active,
            "status": status,
            "completed": safe_completed,
            "total": safe_total,
            "percent": min(100, round(safe_completed / safe_total * 100)) if safe_total else 0,
            "started_at": started_at or _ingestion_progress["started_at"],
            "estimated_seconds_remaining": remaining,
        })
        if not active:
            _ingestion_started_monotonic = None


def _get_ingestion_progress(user_id: int) -> dict:
    with _ingestion_progress_lock:
        if _ingestion_progress["user_id"] != user_id:
            return {
                "active": False,
                "status": "idle",
                "completed": 0,
                "total": 0,
                "percent": 0,
                "started_at": None,
                "estimated_seconds_remaining": None,
            }
        return {key: value for key, value in _ingestion_progress.items() if key != "user_id"}


class CredentialsRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)


class PasswordResetCompletionRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)
    password: str = Field(min_length=12, max_length=128)


class SitemapRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class SitemapScheduleRequest(BaseModel):
    schedule: Literal["manual", "daily", "weekly", "every_15_days", "every_30_days", "monthly"]


class MissingRetentionRequest(BaseModel):
    checks: Literal[1, 2, 15, 30]


class RestoreArticleRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class ConversationTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ConversationalQuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    conversation_history: list[ConversationTurn] = Field(default_factory=list, max_length=10)


class EscalationRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    reason: str = Field(min_length=1, max_length=1000)


class ResolutionRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)
    reviewer: str = Field(min_length=1, max_length=200)


def require_user(request: Request) -> dict:
    auth_header = request.headers.get("Authorization")
    token = None
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.cookies.get(SESSION_COOKIE)
    user = get_session_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="Sign in to use the knowledge base")
    return user


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=os.getenv(
            "AUTH_COOKIE_SECURE",
            "true" if os.getenv("APP_ENV", "").lower() == "production" else "false",
        ).lower() == "true",
        samesite="lax",
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def validate_password_reset_mail_config() -> tuple[str, str, int]:
    host = os.getenv("SMTP_HOST")
    sender = os.getenv("SMTP_FROM")
    if not host or not sender:
        raise HTTPException(
            status_code=503,
            detail="Password reset email is not configured. Set SMTP_HOST and SMTP_FROM.",
        )
    try:
        port = int(os.getenv("SMTP_PORT", "587"))
    except ValueError as error:
        raise HTTPException(status_code=503, detail="SMTP_PORT must be a valid port number") from error
    if not 1 <= port <= 65535:
        raise HTTPException(status_code=503, detail="SMTP_PORT must be between 1 and 65535")
    if os.getenv("SMTP_USERNAME") and not os.getenv("SMTP_PASSWORD"):
        raise HTTPException(status_code=503, detail="SMTP_PASSWORD is required when SMTP_USERNAME is set")
    return host, sender, port


def send_password_reset_email(email: str, token: str) -> None:
    host, sender, port = validate_password_reset_mail_config()
    base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:8081/console.html").rstrip("?&")
    separator = "&" if "?" in base_url else "?"
    reset_url = f"{base_url}{separator}reset_token={token}"
    message = EmailMessage()
    message["Subject"] = "Reset your AI Tech Consultant password"
    message["From"] = sender
    message["To"] = email
    message.set_content(
        "We received a request to reset your AI Tech Consultant password.\n\n"
        f"Choose a new password using this link (valid for 30 minutes):\n{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )

    if os.getenv("SMTP_USE_SSL", "false").lower() == "true":
        with smtplib.SMTP_SSL(host, port, timeout=10, context=ssl.create_default_context()) as server:
            _send_email(server, message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as server:
            if os.getenv("SMTP_STARTTLS", "true").lower() == "true":
                server.starttls(context=ssl.create_default_context())
            username = os.getenv("SMTP_USERNAME")
            password = os.getenv("SMTP_PASSWORD")
            if username:
                server.login(username, password)
            _send_email(server, message)


def _send_email(server: smtplib.SMTP, message: EmailMessage) -> None:
    refused = server.send_message(message)
    if refused:
        raise smtplib.SMTPRecipientsRefused(refused)


def validate_sitemap_url(url: str) -> str:
    """Accept either a direct sitemap XML URL *or* any plain HTTP/HTTPS URL.

    When a plain page URL is given (no .xml/.xml.gz suffix and no 'sitemap'
    in the path), the system will attempt to discover sitemaps from the
    standard locations (sitemap.xml, robots.txt, etc.) at ingestion time.
    """
    normalized = url.strip()
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise HTTPException(status_code=422, detail="Enter a valid HTTP or HTTPS URL")
    return normalized


def answer_question(q: str, conversation_history: list[dict[str, str]] | None = None, user_id: int | None = None) -> dict:
    if not q.strip():
        raise HTTPException(status_code=422, detail="Question must not be empty")
    if conversation_history:
        answer, sources, needs_human = query_knowledge(q, conversation_history, user_id=user_id)
    else:
        answer, sources, needs_human = query_knowledge(q, user_id=user_id)
    escalation_id = create_escalation(q, "Insufficient relevant indexed evidence") if needs_human else None
    return {
        "query": q,
        "answer": answer,
        "status": "insufficient_evidence" if needs_human else "answered",
        "sources": sources,
        "needs_human": needs_human,
        "escalation_id": escalation_id,
    }


@app.get("/health")
def health():
    return {"status": "ok", "database": "sqlite"}


@app.post("/auth/register", status_code=201)
def register(request: CredentialsRequest, response: Response):
    email = request.email.strip().casefold()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    if len(request.password) < 12:
        raise HTTPException(status_code=422, detail="Password must be at least 12 characters")
    user = create_user(email, request.password)
    if user is None:
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    token, _ = create_session(user["id"])
    set_session_cookie(response, token)
    return {**user, "token": token}


@app.post("/auth/login")
def login(request: CredentialsRequest, response: Response):
    user = authenticate_user(request.email, request.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    token, _ = create_session(user["id"])
    set_session_cookie(response, token)
    return {**user, "token": token}


@app.post("/auth/password-reset/request", status_code=202)
def request_password_reset(request: PasswordResetRequest):
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", request.email.strip()):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    reset = create_password_reset(request.email)
    if reset is not None:
        email, token = reset
        host = os.getenv("SMTP_HOST")
        sender = os.getenv("SMTP_FROM")
        if host and sender:
            try:
                send_password_reset_email(email, token)
            except (OSError, smtplib.SMTPException) as error:
                logger.error("Password reset email delivery failed: %s", error)
        else:
            base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:8081/console.html").rstrip("?&")
            separator = "&" if "?" in base_url else "?"
            reset_url = f"{base_url}{separator}reset_token={token}"
            return {
                "message": "Password reset link generated for local environment.",
                "reset_url": reset_url,
            }
    return {"message": "If an account exists for that email, a password reset link will be sent."}


@app.post("/auth/password-reset/complete")
def complete_password_reset(request: PasswordResetCompletionRequest):
    if not reset_password(request.token, request.password):
        raise HTTPException(status_code=400, detail="This password reset link is invalid or has expired")
    return {"message": "Password updated. Sign in with your new password."}


@app.get("/auth/me")
def current_account(user: dict = Depends(require_user)):
    return user


@app.get("/auth/inbox-token")
def inbox_integration_token(user: dict = Depends(require_user)):
    secret = os.getenv("GMAIL_SESSION_SECRET")
    if not secret or len(secret) < 32:
        raise HTTPException(status_code=503, detail="Inbox integration authentication is not configured")
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub": str(user["id"]),
        "exp": int(time.time() * 1000) + 5 * 60 * 1000,
    }, separators=(",", ":")).encode()).rstrip(b"=").decode()
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    token = f"{payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"
    return {"token": token}


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    delete_session(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/", httponly=True, samesite="strict")
    return {"status": "signed_out"}


@app.get("/sitemaps")
def get_sitemaps(user: dict = Depends(require_user)):
    return {
        "sitemaps": list_sitemaps(user["id"]),
        "schedule": get_sitemap_schedule(user["id"]),
        "retention_checks": get_missing_retention_checks(user["id"]),
    }


@app.put("/sitemaps/schedule")
def update_sitemap_schedule(request: SitemapScheduleRequest, user: dict = Depends(require_user)):
    schedule = set_sitemap_schedule(user["id"], request.schedule)
    if schedule is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"schedule": schedule}


@app.put("/sitemaps/retention")
def update_missing_retention(request: MissingRetentionRequest, user: dict = Depends(require_user)):
    checks = set_missing_retention_checks(user["id"], request.checks)
    if checks is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"retention_checks": checks}


@app.post("/sitemaps", status_code=201)
def create_sitemap(request: SitemapRequest, user: dict = Depends(require_user)):
    url = validate_sitemap_url(request.url)
    source = add_sitemap(user["id"], url)
    if source is None:
        raise HTTPException(status_code=409, detail="This sitemap is already connected to your account")
    return source


@app.delete("/sitemaps/{sitemap_id}")
def delete_sitemap(sitemap_id: int, user: dict = Depends(require_user)):
    if not remove_sitemap(user["id"], sitemap_id):
        raise HTTPException(status_code=404, detail="Sitemap not found")
    return {"status": "removed"}


@app.get("/ask")
def ask_question(
    q: str,
    request: Request,
    service_key: str | None = Header(default=None, alias="X-Knowledge-Service-Key"),
):
    configured_key = os.getenv("KNOWLEDGE_SERVICE_TOKEN")
    if configured_key and service_key and hmac.compare_digest(service_key, configured_key):
        return answer_question(q)
    user = require_user(request)
    return answer_question(q, user_id=user["id"])


@app.post("/ask/chat")
def ask_conversational_question(request: ConversationalQuestionRequest, user: dict = Depends(require_user)):
    history = [turn.model_dump() for turn in request.conversation_history]
    return answer_question(request.question, history, user_id=user["id"])


@app.get("/internal/ask")
def internal_ask(
    q: str,
    user_id: int | None = None,
    authorization: str | None = Header(default=None, alias="Authorization"),
    service_key: str | None = Header(default=None, alias="X-Knowledge-Service-Key"),
):
    configured_key = os.getenv("KNOWLEDGE_SERVICE_TOKEN")
    if not configured_key:
        raise HTTPException(status_code=503, detail="Internal knowledge service authentication is not configured")
    if not service_key or not hmac.compare_digest(service_key, configured_key):
        raise HTTPException(status_code=401, detail="Invalid service credentials")
    # Try to extract user_id from inbox JWT token (Authorization: Bearer <token>)
    resolved_user_id = user_id
    if resolved_user_id is None and authorization and authorization.startswith("Bearer "):
        try:
            inbox_token = authorization[7:].strip()
            secret = os.getenv("GMAIL_SESSION_SECRET", "")
            if secret and len(secret) >= 32:
                parts = inbox_token.split(".")
                if len(parts) == 2:
                    payload_b64 = parts[0]
                    # Restore padding and decode
                    payload_bytes = base64.urlsafe_b64decode(payload_b64 + "==")
                    claims = json.loads(payload_bytes.decode())
                    if "sub" in claims:
                        resolved_user_id = int(claims["sub"])
        except Exception:
            pass
    return answer_question(q, user_id=resolved_user_id)


@app.post("/ingestion/run")
def ingestion_run(user: dict = Depends(require_user)):
    sources = list_sitemaps(user["id"])
    if not sources:
        raise HTTPException(status_code=400, detail="Connect at least one sitemap before starting ingestion")
    if not _ingestion_run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="A sitemap ingestion run is already in progress")
    started_at = datetime.now(timezone.utc).isoformat()
    _update_ingestion_progress(
        user["id"],
        active=True,
        status="discovering",
        started_at=started_at,
    )
    reports = []
    completed_offset = 0
    total_offset = 0
    try:
        retention_checks = get_missing_retention_checks(user["id"])
        seen_sitemap_urls: set[str] = set()
        for source in sources:
            def on_progress(progress: dict[str, int]) -> None:
                nonlocal total_offset
                total_offset = max(total_offset, completed_offset + progress["total"])
                _update_ingestion_progress(
                    user["id"],
                    active=True,
                    status="running",
                    completed=completed_offset + progress["completed"],
                    total=total_offset,
                    started_at=started_at,
                )

            report = run_ingestion(
                limit=None,
                sitemap_url=source["url"],
                deactivate_missing=False,
                refresh_existing=True,
                exclude_urls=set(seen_sitemap_urls),
                progress_callback=on_progress,
            )
            completed_offset += report.get("completed", 0)
            total_offset = max(total_offset, completed_offset)
            seen_sitemap_urls.update(report.get("sitemap_urls", []))
            report = finalize_sitemap_scan(source["id"], report, retention_checks)
            report["sitemap_url"] = source["url"]
            reports.append(report)
        totals = {
            key: sum(report.get(key, 0) for report in reports)
            for key in (
                "discovered",
                "discovered_articles",
                "processed",
                "added_or_updated",
                "unchanged",
                "failed",
                "disallowed",
                "removed",
                "archived",
                "duplicate_urls_skipped",
                "redirects_301",
                "pages_404",
            )
        }
        complete = bool(reports) and all(report["complete"] for report in reports)
        _update_ingestion_progress(
            user["id"],
            active=False,
            status="complete" if complete else "incomplete",
            completed=completed_offset,
            total=total_offset,
            started_at=started_at,
        )
        return {**totals, "complete": complete, "sitemaps": reports}
    except Exception:
        _update_ingestion_progress(
            user["id"],
            active=False,
            status="failed",
            completed=completed_offset,
            total=total_offset,
            started_at=started_at,
        )
        raise
    finally:
        _ingestion_run_lock.release()


@app.get("/ingestion/status")
def ingestion_status(user: dict = Depends(require_user)):
    return {**get_ingestion_status(user["id"]), "progress": _get_ingestion_progress(user["id"])}


@app.get("/ingestion/archived")
def archived_articles(user: dict = Depends(require_user)):
    return {"articles": list_archived_articles()}


@app.get("/ingestion/history")
def ingestion_history(limit: int = Query(default=25, ge=1, le=100), user: dict = Depends(require_user)):
    return {"changes": get_article_change_history(user["id"], limit)}


@app.post("/ingestion/archived/restore")
def restore_article(request: RestoreArticleRequest, user: dict = Depends(require_user)):
    if not restore_archived_article(request.url):
        raise HTTPException(status_code=404, detail="Archived article not found")
    return {"url": request.url, "status": "active"}


@app.get("/ingestion/preview")
def ingestion_preview(user: dict = Depends(require_user)):
    sources = list_sitemaps(user["id"])
    if not sources:
        raise HTTPException(status_code=400, detail="Connect at least one sitemap before reviewing ingestion")
    discovered_sitemaps = {}
    unique_urls = {}
    occurrence_counts: dict[str, int] = {}
    sitemap_references: dict[str, list[str]] = {}
    duplicates_within_sitemaps = 0
    url_occurrences = 0
    for source in sources:
        try:
            details = fetch_sitemap_details(source["url"], timeout=int(os.getenv("CRAWL_TIMEOUT_SECONDS", "15")))
        except (HTTPError, URLError, TimeoutError, ValueError, ParseError) as error:
            logger.warning("Sitemap preview failed for %s: %s", source["url"], error)
            raise HTTPException(
                status_code=502,
                detail=f"Could not preview sitemap {source['url']}: {error}",
            ) from error
        for sitemap in details["sitemaps"]:
            sitemap_details = dict(sitemap)
            sitemap_details["duplicate_urls_skipped"] = sitemap.get("duplicate_urls_skipped", 0)
            discovered_sitemaps.setdefault(sitemap["url"], sitemap_details)
        duplicates_within_sitemaps += details.get("duplicate_urls_skipped", 0)
        url_occurrences += len(details["pages"])
        detail_occurrences = details.get("url_occurrence_counts", {})
        detail_references = details.get("url_sitemap_references", {})
        for page in details["pages"]:
            page_url = page["url"]
            unique_urls.setdefault(page_url, page)
            occurrence_counts[page_url] = occurrence_counts.get(page_url, 0) + detail_occurrences.get(page_url, 1)
            page_references = sitemap_references.setdefault(page_url, [])
            for reference in detail_references.get(page_url, [source["url"]]):
                if reference not in page_references:
                    page_references.append(reference)
    urls = list(unique_urls)
    duplicate_urls = [
        {
            "url": page_url,
            "occurrences": occurrence_count,
            "sitemap_references": sitemap_references[page_url],
        }
        for page_url, occurrence_count in occurrence_counts.items()
        if occurrence_count > 1
    ]
    crawl_delay = float(os.getenv("CRAWL_DELAY_SECONDS", "0.5"))
    seconds_per_page = max(0.5, crawl_delay + 0.3)
    pages_per_1_hour = int(3600 / seconds_per_page)
    pages_per_hour = pages_per_1_hour
    pages_per_day = pages_per_hour * 24
    expected_completion_seconds = round(len(urls) * seconds_per_page)
    if expected_completion_seconds < 60:
        expected_completion_time = f"{expected_completion_seconds} seconds"
    elif expected_completion_seconds < 3600:
        mins = expected_completion_seconds // 60
        secs = expected_completion_seconds % 60
        expected_completion_time = f"{mins} min {secs} sec" if secs else f"{mins} minutes"
    else:
        hours = expected_completion_seconds // 3600
        mins = (expected_completion_seconds % 3600) // 60
        expected_completion_time = f"{hours} hr {mins} min" if mins else f"{hours} hours"

    return {
        "connected_sitemaps": len(sources),
        "total_sitemaps_found": len(discovered_sitemaps),
        "total_urls_found": len(urls),
        "duplicate_urls_skipped": duplicates_within_sitemaps + url_occurrences - len(unique_urls),
        "duplicate_urls": duplicate_urls,
        "first_url": urls[0] if urls else None,
        "last_url": urls[-1] if urls else None,
        "sitemaps": list(discovered_sitemaps.values()),
        "pages_per_1_hour": pages_per_1_hour,
        "pages_per_hour": pages_per_hour,
        "pages_per_day": pages_per_day,
        "expected_completion_seconds": expected_completion_seconds,
        "expected_completion_time": expected_completion_time,
    }


@app.post("/escalations")
def create_escalation_task(request: EscalationRequest, user: dict = Depends(require_user)):
    escalation_id = create_escalation(request.question, request.reason)
    return {"id": escalation_id, "status": "open"}


@app.post("/escalations/{escalation_id}/resolve")
def resolve_escalation_task(escalation_id: int, request: ResolutionRequest, user: dict = Depends(require_user)):
    result = resolve_escalation(escalation_id, request.answer, request.reviewer)
    if result is None:
        raise HTTPException(status_code=404, detail="Open escalation not found")
    return result
