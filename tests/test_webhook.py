import hashlib
import hmac
import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.main import app

SECRET = "test-secret"


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "test-key")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("DEVIN_API_URL", "https://api.devin.ai/v1")
    monkeypatch.setenv("TRIGGER_LABEL", "devin")
    monkeypatch.setenv("ALLOWED_REPOS", "ngao178/superset")


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


def labeled_payload(label: str = "devin", repo: str = "ngao178/superset") -> dict:
    return {
        "action": "labeled",
        "label": {"name": label},
        "repository": {"full_name": repo},
        "issue": {
            "number": 7,
            "title": "Broken thing",
            "body": "It is broken.",
            "html_url": f"https://github.com/{repo}/issues/7",
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
    assert "Fixes ngao178/superset#7" in sent["prompt"]


def test_ignores_other_repos(client: TestClient) -> None:
    response = post(client, labeled_payload(repo="someone-else/superset"))
    assert response.json()["status"] == "ignored"


def test_allows_any_repo_when_unset(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALLOWED_REPOS")
    with respx.mock:
        respx.post("https://api.devin.ai/v1/sessions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "session_id": "devin-123",
                    "url": "https://app.devin.ai/sessions/123",
                },
            )
        )
        response = post(client, labeled_payload(repo="someone-else/superset"))
    assert response.json()["status"] == "created"


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
