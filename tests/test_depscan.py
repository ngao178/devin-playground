import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import depscan, main
from app.audit import Finding
from app.config import Settings
from app.depscan import DependencyScanner, find_manifests
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


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVIN_API_KEY", "test-key")
    monkeypatch.setenv("DEVIN_ORG_ID", ORG)
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("DEVIN_API_URL", "https://api.devin.ai")
    monkeypatch.setenv("DEP_SCAN_REPOS", REPO)
    monkeypatch.setenv("DEP_SCAN_INTERVAL_SECONDS", "150")
    monkeypatch.setenv("DEP_AUDIT_ENABLED", "false")


@pytest.fixture(autouse=True)
def reset_store() -> None:
    store._sessions.clear()


def mock_head(sha: str) -> None:
    respx.get(COMMITS_URL).mock(return_value=httpx.Response(200, json=[{"sha": sha}]))


def mock_compare(base: str, head: str, files: list[str]) -> None:
    respx.get(f"https://api.github.com/repos/{REPO}/compare/{base}...{head}").mock(
        return_value=httpx.Response(
            200,
            json={
                "files": [{"filename": name} for name in files],
                "commits": [{"sha": head, "commit": {"message": "chore: deps"}}],
            },
        )
    )


def test_interval_defaults_to_150_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEP_SCAN_INTERVAL_SECONDS")
    assert Settings.from_env().dep_scan_interval_seconds == 150.0


def test_interval_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEP_SCAN_INTERVAL_SECONDS", "42.5")
    assert Settings.from_env().dep_scan_interval_seconds == 42.5


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
async def test_first_scan_only_records_a_baseline() -> None:
    mock_head("sha1")
    scanner = DependencyScanner(Settings.from_env())

    result = await scanner.scan_repo(REPO)

    assert result.session is None
    assert result.reason == "baseline recorded"
    assert scanner.last_scanned[REPO] == "sha1"


@respx.mock
async def test_scan_creates_bump_session_for_manifest_changes() -> None:
    mock_head("sha1")
    scanner = DependencyScanner(Settings.from_env())
    await scanner.scan_repo(REPO)

    respx.get(COMMITS_URL).mock(
        return_value=httpx.Response(200, json=[{"sha": "sha2"}])
    )
    mock_compare("sha1", "sha2", ["requirements.txt", "src/app.py"])
    respx.get(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )
    create = respx.post(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json=SESSION_BODY)
    )

    result = await scanner.scan_repo(REPO)

    assert result.manifests == ("requirements.txt",)
    assert result.session is not None
    sent = create.calls.last.request.read().decode()
    assert "depscan:ngao178/superset@sha2" in sent
    assert "requirements.txt" in sent
    assert "chore(deps)" in sent
    tracked = await store.list()
    assert [s.session_id for s in tracked] == ["devin-dep"]
    assert tracked[0].source == "depscan"


@respx.mock
async def test_audit_findings_are_fed_into_the_bump_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEP_AUDIT_ENABLED", "true")
    finding = Finding(
        "npm", "package.json", "lodash", "high", "<4.17.21", "4.17.21", "pollution"
    )

    async def fake_audit(repository, sha, manifests, token, timeout):
        assert manifests == ("package.json",)
        return (finding,)

    monkeypatch.setattr(depscan, "audit_manifests", fake_audit)

    mock_head("sha1")
    scanner = DependencyScanner(Settings.from_env())
    await scanner.scan_repo(REPO)

    respx.get(COMMITS_URL).mock(
        return_value=httpx.Response(200, json=[{"sha": "sha2"}])
    )
    mock_compare("sha1", "sha2", ["package.json"])
    respx.get(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )
    create = respx.post(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json=SESSION_BODY)
    )

    result = await scanner.scan_repo(REPO)

    assert result.findings == (finding,)
    prompt = json.loads(create.calls.last.request.read())["prompt"]
    assert "npm audit --json" in prompt
    assert "[high] lodash <4.17.21" in prompt
    assert "Fix every vulnerability listed above first" in prompt


@respx.mock
async def test_audit_is_skipped_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(*args, **kwargs):
        raise AssertionError("audit should not run")

    monkeypatch.setattr(depscan, "audit_manifests", fail)

    mock_head("sha1")
    scanner = DependencyScanner(Settings.from_env())
    await scanner.scan_repo(REPO)

    respx.get(COMMITS_URL).mock(
        return_value=httpx.Response(200, json=[{"sha": "sha2"}])
    )
    mock_compare("sha1", "sha2", ["package.json"])
    respx.get(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json={"items": [], "total": 0})
    )
    respx.post(SESSIONS_URL).mock(return_value=httpx.Response(200, json=SESSION_BODY))

    assert (await scanner.scan_repo(REPO)).findings == ()


@respx.mock
async def test_scan_skips_when_no_dependency_files_changed() -> None:
    mock_head("sha1")
    scanner = DependencyScanner(Settings.from_env())
    await scanner.scan_repo(REPO)

    respx.get(COMMITS_URL).mock(
        return_value=httpx.Response(200, json=[{"sha": "sha2"}])
    )
    mock_compare("sha1", "sha2", ["src/app.py"])
    create = respx.post(SESSIONS_URL).mock(return_value=httpx.Response(500))

    result = await scanner.scan_repo(REPO)

    assert result.reason == "no dependency changes"
    assert not create.called


@respx.mock
async def test_scan_reuses_session_for_same_head_sha() -> None:
    mock_head("sha1")
    settings = Settings.from_env()
    first = DependencyScanner(settings)
    await first.scan_repo(REPO)

    respx.get(COMMITS_URL).mock(
        return_value=httpx.Response(200, json=[{"sha": "sha2"}])
    )
    mock_compare("sha1", "sha2", ["pyproject.toml"])
    respx.get(SESSIONS_URL).mock(
        return_value=httpx.Response(200, json={"items": [SESSION_BODY], "total": 1})
    )
    create = respx.post(SESSIONS_URL).mock(return_value=httpx.Response(500))

    result = await first.scan_repo(REPO)

    assert result.session is not None
    assert not create.called


@respx.mock
async def test_scan_all_survives_github_errors() -> None:
    respx.get(COMMITS_URL).mock(return_value=httpx.Response(503))
    scanner = DependencyScanner(Settings.from_env())

    assert await scanner.scan_all() == []


def test_dep_scan_endpoint_conflicts_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(main, "scanner", None)
    response = TestClient(main.app).post("/dep-scan")
    assert response.status_code == 409


@respx.mock
def test_dep_scan_endpoint_runs_a_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    mock_head("sha1")
    monkeypatch.setattr(main, "scanner", DependencyScanner(Settings.from_env()))

    body = TestClient(main.app).post("/dep-scan").json()

    assert body["scans"] == [
        {
            "repository": REPO,
            "base_sha": "sha1",
            "head_sha": "sha1",
            "manifests": [],
            "findings": [],
            "reason": "baseline recorded",
            "session_id": None,
            "session_url": None,
        }
    ]


async def test_scanner_starts_and_stops_with_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEP_SCAN_ENABLED", "false")
    with TestClient(main.app):
        assert main.scanner is None
