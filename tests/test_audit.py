import json
from pathlib import Path

import pytest

from app import audit
from app.audit import (
    AuditError,
    Finding,
    audit_manifests,
    parse_npm_audit,
    parse_pip_audit,
)

NPM_AUDIT = json.dumps(
    {
        "vulnerabilities": {
            "lodash": {
                "severity": "high",
                "range": "<4.17.21",
                "via": [{"title": "Prototype pollution"}],
                "fixAvailable": {"name": "lodash", "version": "4.17.21"},
            },
            "minimist": {
                "severity": "critical",
                "range": "<1.2.6",
                "via": [{"title": "Prototype pollution"}],
                "fixAvailable": False,
            },
        }
    }
)

PIP_AUDIT = json.dumps(
    {
        "dependencies": [
            {
                "name": "requests",
                "version": "2.19.0",
                "vulns": [{"id": "PYSEC-2018-28", "fix_versions": ["2.20.0"]}],
            },
            {"name": "httpx", "version": "0.27.0", "vulns": []},
        ]
    }
)


def test_parse_npm_audit() -> None:
    findings = parse_npm_audit(NPM_AUDIT, "frontend/package.json")
    by_name = {f.package: f for f in findings}
    assert by_name["lodash"].severity == "high"
    assert by_name["lodash"].fixed_in == "4.17.21"
    assert by_name["minimist"].fixed_in == ""
    assert "Prototype pollution" in by_name["lodash"].describe()


def test_parse_pip_audit_skips_clean_packages() -> None:
    findings = parse_pip_audit(PIP_AUDIT, "requirements.txt")
    assert [f.package for f in findings] == ["requests"]
    assert findings[0].fixed_in == "2.20.0"


def test_parse_rejects_non_json() -> None:
    with pytest.raises(AuditError):
        parse_npm_audit("not json", "package.json")


def test_findings_sort_critical_first() -> None:
    findings = parse_npm_audit(NPM_AUDIT, "package.json")
    ordered = sorted(findings, key=audit._sort_key)
    assert [f.package for f in ordered] == ["minimist", "lodash"]


async def test_audit_manifests_skips_unsupported_manifests() -> None:
    assert await audit_manifests("owner/repo", "sha", ("go.mod", "Cargo.toml")) == ()


async def test_audit_manifests_runs_auditor_on_checkout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked_out: dict[str, object] = {}

    async def fake_checkout(repository, sha, token, dest, timeout):
        checked_out.update(repository=repository, sha=sha, dest=Path(dest))

    async def fake_npm(root: Path, manifest: str, timeout: float):
        return [Finding("npm", manifest, "lodash", "high", "<4.17.21", "4.17.21", "pp")]

    monkeypatch.setattr(audit, "checkout", fake_checkout)
    monkeypatch.setitem(audit.AUDITORS, "package.json", ("npm", fake_npm))

    findings = await audit_manifests("owner/repo", "sha1", ("package.json",))

    assert checked_out["repository"] == "owner/repo"
    assert [f.package for f in findings] == ["lodash"]


async def test_audit_manifests_survives_auditor_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_checkout(repository, sha, token, dest, timeout):
        return None

    async def failing(root: Path, manifest: str, timeout: float):
        raise AuditError("npm is not installed")

    monkeypatch.setattr(audit, "checkout", fake_checkout)
    monkeypatch.setitem(audit.AUDITORS, "package.json", ("npm", failing))

    assert await audit_manifests("owner/repo", "sha1", ("package.json",)) == ()
