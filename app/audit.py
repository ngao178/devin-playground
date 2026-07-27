"""Vulnerability auditing of a repo checkout.

The dependency scanner checks out the flagged commit into a temporary
directory and runs the ecosystem's audit tool (`npm audit --json`,
`pip-audit`) so the bump session starts from a concrete vulnerability list
instead of guessing what is out of date.
"""

import asyncio
import base64
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("devin-webhook.audit")


class AuditError(RuntimeError):
    pass


@dataclass(frozen=True)
class Finding:
    ecosystem: str
    manifest: str
    package: str
    severity: str
    installed: str
    fixed_in: str
    title: str

    def describe(self) -> str:
        fix = f"fixed in {self.fixed_in}" if self.fixed_in else "no fix available"
        installed = self.installed or "unknown"
        return (
            f"[{self.severity}] {self.package} {installed} ({self.manifest}, "
            f"{self.ecosystem}): {self.title} — {fix}"
        )


SEVERITY_ORDER = {
    "critical": 0,
    "high": 1,
    "moderate": 2,
    "medium": 2,
    "low": 3,
    "info": 4,
    "unknown": 5,
}


def _sort_key(finding: Finding) -> tuple[int, str]:
    return (SEVERITY_ORDER.get(finding.severity.lower(), 5), finding.package)


async def _run(
    args: list[str], cwd: Path | None, timeout: float
) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        raise AuditError(f"{args[0]} timed out after {timeout}s") from exc
    return (
        process.returncode or 0,
        stdout.decode(errors="replace"),
        stderr.decode(errors="replace"),
    )


async def checkout(repository: str, sha: str, token: str, dest: Path, timeout: float):
    """Shallow-fetch a single commit of `repository` into `dest`."""
    url = f"https://github.com/{repository}.git"
    auth_args: list[str] = []
    if token:
        credential = base64.b64encode(f"x-access-token:{token}".encode()).decode()
        auth_args = ["-c", f"http.extraheader=Authorization: Basic {credential}"]

    await _run(["git", "init", "--quiet", str(dest)], None, timeout)
    for args in (
        ["git", *auth_args, "fetch", "--depth", "1", "--quiet", url, sha],
        ["git", "checkout", "--quiet", "FETCH_HEAD"],
    ):
        code, _, stderr = await _run(args, dest, timeout)
        if code != 0:
            raise AuditError(f"{' '.join(args[:3])} failed: {stderr.strip()}")


def parse_npm_audit(payload: str, manifest: str) -> list[Finding]:
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise AuditError("npm audit did not return JSON") from exc

    findings = []
    for name, entry in (data.get("vulnerabilities") or {}).items():
        if not isinstance(entry, dict):
            continue
        titles = [
            via.get("title", "")
            for via in entry.get("via") or []
            if isinstance(via, dict) and via.get("title")
        ]
        fix = entry.get("fixAvailable")
        findings.append(
            Finding(
                ecosystem="npm",
                manifest=manifest,
                package=str(name),
                severity=str(entry.get("severity") or "unknown"),
                installed=str(entry.get("range") or ""),
                fixed_in=str(fix.get("version") or "") if isinstance(fix, dict) else "",
                title="; ".join(titles) or "vulnerable dependency",
            )
        )
    return findings


def parse_pip_audit(payload: str, manifest: str) -> list[Finding]:
    try:
        data = json.loads(payload)
    except ValueError as exc:
        raise AuditError("pip-audit did not return JSON") from exc

    entries = data.get("dependencies") if isinstance(data, dict) else data
    findings = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        for vuln in entry.get("vulns") or []:
            if not isinstance(vuln, dict):
                continue
            fixes = [str(v) for v in vuln.get("fix_versions") or []]
            findings.append(
                Finding(
                    ecosystem="pip",
                    manifest=manifest,
                    package=str(entry.get("name") or "unknown"),
                    severity="unknown",
                    installed=str(entry.get("version") or ""),
                    fixed_in=", ".join(fixes),
                    title=str(vuln.get("id") or "known vulnerability"),
                )
            )
    return findings


async def _audit_npm(root: Path, manifest: str, timeout: float) -> list[Finding]:
    if shutil.which("npm") is None:
        raise AuditError("npm is not installed")
    directory = root / manifest
    directory = directory.parent
    if not (directory / "package-lock.json").exists():
        await _run(
            ["npm", "install", "--package-lock-only", "--ignore-scripts"],
            directory,
            timeout,
        )
    _, stdout, stderr = await _run(["npm", "audit", "--json"], directory, timeout)
    if not stdout.strip():
        raise AuditError(f"npm audit produced no output: {stderr.strip()}")
    return parse_npm_audit(stdout, manifest)


async def _audit_pip(root: Path, manifest: str, timeout: float) -> list[Finding]:
    if shutil.which("pip-audit") is None:
        raise AuditError("pip-audit is not installed")
    _, stdout, stderr = await _run(
        ["pip-audit", "--format", "json", "--requirement", manifest],
        root,
        timeout,
    )
    if not stdout.strip():
        raise AuditError(f"pip-audit produced no output: {stderr.strip()}")
    return parse_pip_audit(stdout, manifest)


AUDITORS = {
    "package.json": ("npm", _audit_npm),
    "requirements.txt": ("pip", _audit_pip),
    "requirements-dev.txt": ("pip", _audit_pip),
}


async def audit_manifests(
    repository: str,
    sha: str,
    manifests: tuple[str, ...],
    token: str = "",
    timeout: float = 300.0,
) -> tuple[Finding, ...]:
    """Check out `sha` and audit every manifest an auditor is available for."""
    auditable = [
        (path, AUDITORS[path.rsplit("/", 1)[-1].lower()])
        for path in manifests
        if path.rsplit("/", 1)[-1].lower() in AUDITORS
    ]
    if not auditable:
        return ()

    findings: list[Finding] = []
    with tempfile.TemporaryDirectory(prefix="depscan-") as workdir:
        root = Path(workdir)
        await checkout(repository, sha, token, root, timeout)
        for manifest, (name, auditor) in auditable:
            try:
                findings.extend(await auditor(root, manifest, timeout))
            except (AuditError, OSError):
                logger.exception(
                    "%s audit of %s in %s failed", name, manifest, repository
                )
    return tuple(sorted(findings, key=_sort_key))
