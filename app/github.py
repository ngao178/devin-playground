import logging

import httpx

GITHUB_API_URL = "https://api.github.com"

logger = logging.getLogger("devin-webhook.github")


class GitHubApiError(RuntimeError):
    pass


class GitHubClient:
    """Read-only GitHub client used to inspect a repo's files at a commit."""

    def __init__(
        self, token: str, api_url: str = GITHUB_API_URL, timeout: float = 30.0
    ) -> None:
        self._api_url = api_url.rstrip("/")
        self._headers = {"Accept": "application/vnd.github+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._timeout = timeout

    async def head_sha(self, repository: str, branch: str | None = None) -> str:
        params: dict[str, object] = {"per_page": 1}
        if branch:
            params["sha"] = branch
        body = await self._get(f"/repos/{repository}/commits", params=params)
        if not isinstance(body, list) or not body:
            raise GitHubApiError(f"No commits returned for {repository}")
        commit = body[0]
        if not isinstance(commit, dict) or not isinstance(commit.get("sha"), str):
            raise GitHubApiError(f"Unexpected commits response for {repository}")
        return commit["sha"]

    async def list_files(self, repository: str, sha: str) -> tuple[str, ...]:
        """List every file path in the repo tree at `sha`."""
        body = await self._get(
            f"/repos/{repository}/git/trees/{sha}", params={"recursive": "1"}
        )
        if not isinstance(body, dict):
            raise GitHubApiError(f"Unexpected tree response for {repository}")
        if body.get("truncated"):
            logger.warning("Tree listing for %s@%s was truncated", repository, sha)
        return tuple(
            entry["path"]
            for entry in body.get("tree") or []
            if isinstance(entry, dict)
            and entry.get("type") == "blob"
            and isinstance(entry.get("path"), str)
        )

    async def _get(self, path: str, params: dict | None = None) -> object:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._api_url}{path}", headers=self._headers, params=params
            )
            response.raise_for_status()
            try:
                return response.json()
            except ValueError as exc:
                raise GitHubApiError("GitHub returned a non-JSON response") from exc
