"""Periodic dependency-bump scanner.

Independent of the webhook flow: on a fixed interval it diffs each watched repo
since the previous scan, flags dependency-manifest churn, and starts a Devin
session whose job is to open the bump pull requests.
"""

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.devin import DevinApiError, DevinClient, Session
from app.github import Comparison, GitHubApiError, GitHubClient
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
    base_sha: str
    head_sha: str
    manifests: tuple[str, ...]
    session: Session | None
    reason: str


def find_manifests(files: tuple[str, ...]) -> tuple[str, ...]:
    """Return the changed files that are dependency manifests or lockfiles."""
    return tuple(path for path in files if path.rsplit("/", 1)[-1].lower() in MANIFESTS)


def ecosystems(manifests: tuple[str, ...]) -> tuple[str, ...]:
    found = {MANIFESTS[path.rsplit("/", 1)[-1].lower()] for path in manifests}
    return tuple(sorted(found))


def scan_tag(repository: str, head_sha: str) -> str:
    return f"depscan:{repository}@{head_sha[:12]}"


def build_prompt(
    repository: str, comparison: Comparison, manifests: tuple[str, ...]
) -> str:
    diff_url = (
        f"https://github.com/{repository}/compare/"
        f"{comparison.base_sha}...{comparison.head_sha}"
    )
    return "\n".join(
        [
            f"Dependency bump chore for {repository}.",
            "",
            (
                f"A scan of the changes since the last scan ({diff_url}) touched "
                "these dependency manifests:"
            ),
            *(f"- {path}" for path in manifests),
            "",
            f"Package ecosystems involved: {', '.join(ecosystems(manifests))}.",
            "",
            "REQUIREMENTS:",
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


class DependencyScanner:
    """Diffs watched repos since the last scan and delegates bumps to Devin."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._github = GitHubClient(settings.github_token)
        self._devin = DevinClient(
            settings.devin_api_key, settings.devin_api_url, settings.devin_org_id
        )
        self._last_scanned: dict[str, str] = {}

    @property
    def last_scanned(self) -> dict[str, str]:
        return dict(self._last_scanned)

    async def scan_all(self) -> list[ScanResult]:
        results = []
        for repository in self._settings.dep_scan_repos:
            try:
                results.append(await self.scan_repo(repository))
            except (httpx.HTTPError, GitHubApiError, DevinApiError):
                logger.exception("Dependency scan failed for %s", repository)
        return results

    async def scan_repo(self, repository: str) -> ScanResult:
        head_sha = await self._github.head_sha(repository)
        base_sha = self._last_scanned.get(repository)
        self._last_scanned[repository] = head_sha

        if base_sha is None:
            return ScanResult(
                repository=repository,
                base_sha=head_sha,
                head_sha=head_sha,
                manifests=(),
                session=None,
                reason="baseline recorded",
            )

        comparison = await self._github.compare(repository, base_sha, head_sha)
        if comparison.is_empty:
            return ScanResult(
                repository, base_sha, head_sha, (), None, "no new commits"
            )

        manifests = find_manifests(comparison.files)
        if not manifests:
            return ScanResult(
                repository, base_sha, head_sha, (), None, "no dependency changes"
            )

        session = await self._devin.create_tagged_session(
            prompt=build_prompt(repository, comparison, manifests),
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
            "Dependency scan of %s flagged %s; session %s",
            repository,
            ", ".join(manifests),
            session.session_id,
        )
        return ScanResult(
            repository, base_sha, head_sha, manifests, session, "bump session created"
        )

    async def run_forever(self) -> None:
        interval = self._settings.dep_scan_interval_seconds
        logger.info(
            "Dependency scanner watching %s every %.1fs",
            ", ".join(self._settings.dep_scan_repos) or "(no repos)",
            interval,
        )
        while True:
            await self.scan_all()
            await asyncio.sleep(interval)
