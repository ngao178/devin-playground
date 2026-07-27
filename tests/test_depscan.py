import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import depscan
from app.audit import Finding
from app.config import Settings
from app.depscan import DependencyScanner, find_manifests
from app.main import app
from app.store import store

ORG = "org-test"
REPO = "ngao178/superset"
SESSIONS_URL = f"https://api.devin.ai/v3/organizations/{ORG}/sessions"
COMMITS_URL = f"https://api.github.com/repos/{REPO}/commits"
SESSION_BODY = {
    "session_id": "devin-dep",
    "url": "https://app.devin.ai/sessions/dep",
    "status": "running",
}
FINDING = Finding(
    "npm", "package.json", "lodash", "high", "<4.17.21", "4.17.21", "pollution"
)


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "test-key")
    monkeypatch.setenv("DEVIN_ORG_ID", ORG)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("DEVIN_API_URL", "https://api.devin.ai")
    monkeypatch.setenv("DEP_SCAN_REPOS", REPO)


@pytest.fixture(autouse=True)
def reset_store() -> None:
    store._sessions.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def mock_repo(files: list[str], sha: str = "sha1") -> None:
    respx.get(COMMITS_URL).mock(return_value=httpx.Response(200, json=[{"sha": sha}]))
    respx.get(f"https://api.github.com/repos/{REPO}/git/trees/{sha}").mock(
        return_value=httpx.Response(
            200,
            json={"tree": [{"path": path, "type": "blob"} for path in files]},
        )
    )


def mock_devin(existing: dict | None = None):
    respx.get(SESSIONS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [existing] if existing else [],
                "total": 1 if existing else 0,
            },
        )
    )
    return respx.post(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json=SESSION_BODY)
    )


def stub_audit(monkeypatch: pytest.MonkeyPatch, findings: tuple[Finding, ...]) -> None:
    async def fake_audit(repository, sha, manifests, token, timeout):
        return findings

    monkeypatch.setattr(depscan, "audit_manifests", fake_audit)


def test_dep_scan_repos_default_to_allowed_repos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEP_SCAN_REPOS")
    monkeypatch.setenv("ALLOWED_REPOS", REPO)
    assert Settings.from_env().dep_scan_repos == (REPO,)


def test_find_manifests_ignores_unrelated_files() -> None:
    files = ("src/app.py", "frontend/package.json", "docs/readme.md", "go.sum")
    assert find_manifests(files) == ("frontend/package.json", "go.sum")


@respx.mock
async def test_scan_creates_session_when_vulnerabilities_are_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_repo(["src/app.py", "package.json", "requirements.txt"])
    stub_audit(monkeypatch, (FINDING,))
    create = mock_devin()

    result = await DependencyScanner(Settings.from_env()).scan_repo(REPO)

    assert result.reason == "bump session created"
    assert result.manifests == ("package.json", "requirements.txt")
    assert result.findings == (FINDING,)
    sent = json.loads(create.calls.last.request.read())
    assert "depscan:ngao178/superset@sha1" in sent["tags"]
    assert "[high] lodash <4.17.21" in sent["prompt"]
    tracked = await store.list()
    assert [(s.session_id, s.source) for s in tracked] == [("devin-dep", "depscan")]


@respx.mock
async def test_scan_needs_no_commit_touching_manifests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated scans of an unchanged repo still report the vulnerability."""
    mock_repo(["package.json"])
    stub_audit(monkeypatch, (FINDING,))
    create = mock_devin()
    scanner = DependencyScanner(Settings.from_env())

    first = await scanner.scan_repo(REPO)
    assert first.reason == "bump session created"
    assert create.call_count == 1

    # Same head sha: the tag dedupes, so no second session and no duplicate PRs.
    mock_devin(existing=SESSION_BODY)
    second = await scanner.scan_repo(REPO)
    assert second.session is not None
    assert second.findings == (FINDING,)


@respx.mock
async def test_scan_skips_session_when_no_vulnerabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_repo(["package.json"])
    stub_audit(monkeypatch, ())
    create = respx.post(SESSIONS_URL).mock(return_value=httpx.Response(500))

    result = await DependencyScanner(Settings.from_env()).scan_repo(REPO)

    assert result.reason == "no vulnerabilities found"
    assert not create.called


@respx.mock
async def test_scan_reports_when_repo_has_no_manifests() -> None:
    mock_repo(["src/app.py", "README.md"])

    result = await DependencyScanner(Settings.from_env()).scan_repo(REPO)

    assert result.reason == "no manifests found"
    assert result.manifests == ()


@respx.mock
async def test_scan_creates_session_when_auditing_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEP_AUDIT_ENABLED", "false")
    mock_repo(["package.json"])
    create = mock_devin()

    result = await DependencyScanner(Settings.from_env()).scan_repo(REPO)

    assert result.reason == "bump session created"
    assert result.findings == ()
    prompt = json.loads(create.calls.last.request.read())["prompt"]
    assert "out of date" in prompt


@respx.mock
async def test_scan_all_reports_github_errors() -> None:
    respx.get(COMMITS_URL).mock(return_value=httpx.Response(503))

    results = await DependencyScanner(Settings.from_env()).scan_all()

    assert [(r.repository, r.reason, r.failed) for r in results] == [
        (REPO, "scan failed", True)
    ]
    assert "503" in results[0].error


@respx.mock
def test_dep_scan_endpoint_reports_failures(client: TestClient) -> None:
    respx.get(COMMITS_URL).mock(return_value=httpx.Response(503))

    response = client.post("/dep-scan")

    assert response.status_code == 502
    assert REPO in response.json()["detail"]


def test_dep_scan_endpoint_conflicts_when_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEP_SCAN_ENABLED", "false")
    assert client.post("/dep-scan").status_code == 409


@respx.mock
def test_dep_scan_endpoint_runs_a_scan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_repo(["package.json"])
    stub_audit(monkeypatch, (FINDING,))
    mock_devin()

    body = client.post("/dep-scan").json()

    assert body["scans"] == [
        {
            "repository": REPO,
            "head_sha": "sha1",
            "manifests": ["package.json"],
            "findings": [FINDING.describe()],
            "reason": "bump session created",
            "error": "",
            "session_id": "devin-dep",
            "session_url": "https://app.devin.ai/sessions/dep",
        }
    ]


def test_dashboard_shows_scan_button(client: TestClient) -> None:
    body = client.get("/", params={"refresh": "false"}).text
    assert "Run dependency scan" in body
    assert "/dep-scan" in body
    assert REPO in body


def test_dashboard_hides_scan_button_when_disabled(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEP_SCAN_ENABLED", "false")
    body = client.get("/", params={"refresh": "false"}).text
    assert "Run dependency scan" not in body
    assert "Dependency scanner is disabled" in body
