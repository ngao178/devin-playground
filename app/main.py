import hashlib
import hmac
import logging
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.config import Settings
from app.dashboard import render_dashboard
from app.devin import DevinApiError, DevinClient, Issue
from app.store import store

load_dotenv()

logger = logging.getLogger("devin-webhook")

app = FastAPI(title="Devin issue webhook")


def get_settings() -> Settings:
    return Settings.from_env()


def verify_signature(secret: str, body: bytes, signature: str | None) -> None:
    if not signature:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256")

    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


def should_trigger(payload: dict[str, Any], trigger_label: str) -> bool:
    action = payload.get("action")
    if action == "labeled":
        return (payload.get("label") or {}).get("name") == trigger_label
    if action == "opened":
        labels = (payload.get("issue") or {}).get("labels") or []
        return any(label.get("name") == trigger_label for label in labels)
    return False


def parse_issue(payload: dict[str, Any]) -> Issue:
    issue = payload.get("issue") or {}
    repository = (payload.get("repository") or {}).get("full_name")
    number = issue.get("number")
    if not repository or number is None:
        raise HTTPException(status_code=400, detail="Malformed issue payload")

    return Issue(
        repository=repository,
        number=number,
        title=issue.get("title") or "",
        body=issue.get("body") or "",
        url=issue.get("html_url") or "",
    )


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    raw_body = await request.body()
    verify_signature(settings.github_webhook_secret, raw_body, x_hub_signature_256)

    if x_github_event == "ping":
        return {"status": "pong"}
    if x_github_event != "issues":
        return {"status": "ignored", "reason": f"unsupported event: {x_github_event}"}

    payload = await request.json()
    if not should_trigger(payload, settings.trigger_label):
        return {"status": "ignored", "reason": "trigger conditions not met"}

    issue = parse_issue(payload)
    client = DevinClient(settings.devin_api_key, settings.devin_api_url)
    try:
        session = await client.create_session(issue)
    except (httpx.HTTPError, DevinApiError) as exc:
        logger.exception("Failed to create Devin session for %s", issue.url)
        raise HTTPException(
            status_code=502, detail=f"Devin API request failed: {exc}"
        ) from exc

    await store.upsert(
        session_id=session.session_id,
        url=session.url,
        repository=issue.repository,
        issue_number=issue.number,
        status=session.status,
    )

    logger.info(
        "Created Devin session %s for %s#%s",
        session.session_id,
        issue.repository,
        issue.number,
    )
    return {
        "status": "created" if session.is_new_session else "existing",
        "session_id": session.session_id,
        "url": session.url,
    }


@app.get("/", response_class=HTMLResponse)
@app.get("/sessions", response_class=HTMLResponse)
async def sessions_dashboard(refresh: bool = False) -> HTMLResponse:
    if refresh:
        await refresh_statuses()
    sessions = await store.list()
    return HTMLResponse(render_dashboard(sessions))


async def refresh_statuses() -> None:
    """Poll the Devin API for the latest status of each tracked session."""
    settings = get_settings()
    client = DevinClient(settings.devin_api_key, settings.devin_api_url)
    for session in await store.list():
        try:
            status = await client.get_status(session.session_id)
        except (httpx.HTTPError, DevinApiError):
            logger.exception("Failed to refresh status for %s", session.session_id)
            continue
        await store.set_status(session.session_id, status)
