import hashlib
import hmac
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app
from app.store import store

SECRET = "test-secret"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "test-key")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("DEVIN_API_URL", "https://api.devin.ai/v1")
    monkeypatch.setenv("TRIGGER_LABEL", "devin")


@pytest.fixture(autouse=True)
def reset_store() -> None:
    store._sessions.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def post(
    client: TestClient, payload: dict, event: str = "issues", secret: str = SECRET
):
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": event,
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )


def labeled_payload(label: str = "devin") -> dict:
    return {
        "action": "labeled",
        "label": {"name": label},
        "repository": {"full_name": "ngao178/devin-playground"},
        "issue": {
            "number": 7,
            "title": "Broken thing",
            "body": "It is broken.",
            "html_url": "https://github.com/ngao178/devin-playground/issues/7",
            "labels": [{"name": label}],
        },
    }


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_rejects_bad_signature(client: TestClient) -> None:
    response = post(client, labeled_payload(), secret="wrong")
    assert response.status_code == 401


def test_ignores_other_labels(client: TestClient) -> None:
    response = post(client, labeled_payload(label="bug"))
    assert response.json()["status"] == "ignored"


def test_ping(client: TestClient) -> None:
    assert post(client, {}, event="ping").json() == {"status": "pong"}


@respx.mock
def test_creates_session(client: TestClient) -> None:
    route = respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "devin-123",
                "url": "https://app.devin.ai/sessions/123",
                "is_new_session": True,
            },
        )
    )

    response = post(client, labeled_payload())

    assert response.status_code == 200
    assert response.json() == {
        "status": "created",
        "session_id": "devin-123",
        "url": "https://app.devin.ai/sessions/123",
    }
    sent = json.loads(route.calls.last.request.content)
    assert sent["idempotent"] is True
    assert "issues/7" in sent["prompt"]
    assert "Fixes ngao178/devin-playground#7" in sent["prompt"]


@respx.mock
def test_tracks_session_and_shows_it_on_dashboard(client: TestClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "devin-123",
                "url": "https://app.devin.ai/sessions/123",
                "is_new_session": True,
                "status_enum": "running",
            },
        )
    )

    assert post(client, labeled_payload()).status_code == 200

    respx.get("https://api.devin.ai/v1/session/devin-123").mock(
        return_value=httpx.Response(200, json={"status_enum": "running"})
    )

    response = client.get("/sessions")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    body = response.text
    assert "Devin session dashboard" in body
    assert "devin-123" in body
    assert "ngao178/devin-playground#7" in body
    assert "running" in body
    # one issue addressed, one active session
    assert "Issues addressed" in body


def test_dashboard_empty_state(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "No sessions have been started yet." in response.text


@respx.mock
def test_dashboard_counts_active_and_completed(client: TestClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "devin-done",
                "url": "https://app.devin.ai/sessions/done",
                "is_new_session": True,
                "status_enum": "finished",
            },
        )
    )
    assert post(client, labeled_payload()).status_code == 200

    respx.get("https://api.devin.ai/v1/session/devin-done").mock(
        return_value=httpx.Response(200, json={"status_enum": "finished"})
    )

    body = client.get("/").text
    assert "finished" in body
    assert "Completed sessions" in body


@respx.mock
def test_refresh_updates_status(client: TestClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(
            200,
            json={
                "session_id": "devin-123",
                "url": "https://app.devin.ai/sessions/123",
                "is_new_session": True,
                "status_enum": "running",
            },
        )
    )
    assert post(client, labeled_payload()).status_code == 200

    respx.get("https://api.devin.ai/v1/session/devin-123").mock(
        return_value=httpx.Response(200, json={"status_enum": "finished"})
    )

    body = client.get("/sessions", params={"refresh": "true"}).text
    assert "finished" in body


@respx.mock
def test_malformed_devin_response_returns_502(client: TestClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(200, json={"session_id": "devin-123"})
    )
    response = post(client, labeled_payload())
    assert response.status_code == 502
    assert "url" in response.json()["detail"]


@respx.mock
def test_devin_error_returns_502(client: TestClient) -> None:
    respx.post("https://api.devin.ai/v1/sessions").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    assert post(client, labeled_payload()).status_code == 502
