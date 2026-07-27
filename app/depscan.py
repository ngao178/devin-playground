"""On-demand dependency-bump scanner.

Independent of the webhook flow: when triggered (dashboard button or
`POST /dep-scan`) it lists every dependency manifest in the repo at HEAD, audits
them for known vulnerabilities, and starts a Devin session whose job is to open
the bump pull requests.
"""

import logging
from dataclasses import dataclass

import httpx

from app.audit import AuditError, Finding, audit_manifests
from app.config import Settings
from app.devin import DevinApiError, DevinClient, Session
from app.github import GitHubApiError, GitHubClient
from app.store import store

logger = logging.getLogger("devin-webhook.depscan")

# Manifest / lockfile names whose changes indicate dependency work.
MANIFESTS = {
    "requirements.txt": "pip",
    "requirements-dev.txt": "pip",
    "pyproject.toml": "python",
    "poetry.lock": "poetry",
    "uv.lock": "uv",
    "setup.cfg": "python",
    "package.json": "npm",
    "package-lock.json": "npm",
    "yarn.lock": "yarn",
    "pnpm-lock.yaml": "pnpm",
    "go.mod": "go",
    "go.sum": "go",
    "cargo.toml": "cargo",
    "cargo.lock": "cargo",
    "gemfile": "bundler",
    "gemfile.lock": "bundler",
    "build.gradle": "gradle",
    "pom.xml": "maven",
}


@dataclass(frozen=True)
class ScanResult:
    repository: str
    head_sha: str
    manifests: tuple[str, ...]
    session: Session | None
    reason: str
    findings: tuple[Finding, ...] = ()
    error: str = ""

    @property
    def failed(self) -> bool:
        return bool(self.error)


def find_manifests(files: tuple[str, ...]) -> tuple[str, ...]:
    """Return the paths that are dependency manifests or lockfiles."""
    return tuple(path for path in files if path.rsplit("/", 1)[-1].lower() in MANIFESTS)


def ecosystems(manifests: tuple[str, ...]) -> tuple[str, ...]:
    found = {MANIFESTS[path.rsplit("/", 1)[-1].lower()] for path in manifests}
    return tuple(sorted(found))


def scan_tag(repository: str, head_sha: str) -> str:
    return f"depscan:{repository}@{head_sha[:12]}"


def build_prompt(
    repository: str,
    head_sha: str,
    manifests: tuple[str, ...],
    findings: tuple[Finding, ...] = (),
) -> str:
    tree_url = f"https://github.com/{repository}/tree/{head_sha}"
    return "\n".join(
        [
            f"Dependency bump chore for {repository}.",
            "",
            (f"A dependency scan of {tree_url} covered these manifests:"),
            *(f"- {path}" for path in manifests),
            "",
            f"Package ecosystems involved: {', '.join(ecosystems(manifests))}.",
            "",
            *_audit_section(findings),
            "REQUIREMENTS:",
            *(
                []
                if findings
                else [
                    (
                        "- The audit tools reported no known vulnerabilities, so "
                        "focus on dependencies that are simply out of date."
                    )
                ]
            ),
            *(
                [
                    (
                        "- Fix every vulnerability listed above first; those bumps "
                        "are the priority of this PR."
                    )
                ]
                if findings
                else []
            ),
            (
                "- For each manifest above, check the declared dependencies against "
                "the latest releases and identify the ones that are out of date."
            ),
            (
                "- Only bump versions published at least 7 days ago, and prefer "
                "non-breaking upgrades; skip major bumps that need code changes and "
                "list them in the PR description instead."
            ),
            (
                "- Regenerate lockfiles with the project's package manager rather "
                "than editing them by hand."
            ),
            "- Run the repository's lint and test commands before opening the PR.",
            (
                "- Open one pull request per ecosystem, titled "
                '"chore(deps): bump <ecosystem> dependencies", and list every '
                "old -> new version in the description."
            ),
            (
                "- If nothing is actually out of date, do not open a PR; just report "
                "that the dependencies are current."
            ),
        ]
    )


def _audit_section(findings: tuple[Finding, ...]) -> list[str]:
    if not findings:
        return []
    return [
        (
            "The scanner ran the ecosystem audit tools (`npm audit --json`, "
            "`pip-audit`) on that commit and found:"
        ),
        *(f"- {finding.describe()}" for finding in findings),
        "",
    ]


class DependencyScanner:
    """Audits watched repos on demand and delegates the bumps to Devin."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._github = GitHubClient(settings.github_token)
        self._devin = DevinClient(
            settings.devin_api_key, settings.devin_api_url, settings.devin_org_id
        )

    async def scan_all(self) -> list[ScanResult]:
        results = []
        for repository in self._settings.dep_scan_repos:
            try:
                results.append(await self.scan_repo(repository))
            except (httpx.HTTPError, GitHubApiError, DevinApiError) as exc:
                logger.exception("Dependency scan failed for %s", repository)
                results.append(
                    ScanResult(
                        repository=repository,
                        head_sha="",
                        manifests=(),
                        session=None,
                        reason="scan failed",
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        return results

    async def scan_repo(self, repository: str) -> ScanResult:
        head_sha = await self._github.head_sha(repository)
        manifests = find_manifests(await self._github.list_files(repository, head_sha))
        if not manifests:
            return ScanResult(repository, head_sha, (), None, "no manifests found")

        findings = await self._audit(repository, head_sha, manifests)
        if self._settings.dep_audit_enabled and not findings:
            return ScanResult(
                repository, head_sha, manifests, None, "no vulnerabilities found"
            )

        session = await self._devin.create_tagged_session(
            prompt=build_prompt(repository, head_sha, manifests, findings),
            title=f"chore(deps): bump dependencies in {repository}",
            repository=repository,
            dedupe_tag=scan_tag(repository, head_sha),
            extra_tags=["src:depscan"],
        )
        await store.upsert(
            session_id=session.session_id,
            url=session.url,
            repository=repository,
            issue_number=None,
            status=session.status,
            source="depscan",
        )
        logger.info(
            "Dependency scan of %s found %d vulnerabilities; session %s",
            repository,
            len(findings),
            session.session_id,
        )
        return ScanResult(
            repository,
            head_sha,
            manifests,
            session,
            "bump session created",
            findings,
        )

    async def _audit(
        self, repository: str, head_sha: str, manifests: tuple[str, ...]
    ) -> tuple[Finding, ...]:
        if not self._settings.dep_audit_enabled:
            return ()
        try:
            return await audit_manifests(
                repository,
                head_sha,
                manifests,
                token=self._settings.github_token,
                timeout=self._settings.dep_audit_timeout_seconds,
            )
        except (AuditError, OSError):
            logger.exception("Audit of %s@%s failed", repository, head_sha)
            return ()
