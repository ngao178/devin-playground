from dataclasses import dataclass

import httpx

GITHUB_API_URL = "https://api.github.com"


class GitHubApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Comparison:
    base_sha: str
    head_sha: str
    files: tuple[str, ...]
    commit_messages: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return self.base_sha == self.head_sha or not self.commit_messages


class GitHubClient:
    """Read-only GitHub client used to diff a repo since the previous scan."""

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

    async def compare(self, repository: str, base: str, head: str) -> Comparison:
        body = await self._get(f"/repos/{repository}/compare/{base}...{head}")
        if not isinstance(body, dict):
            raise GitHubApiError(f"Unexpected compare response for {repository}")
        files = tuple(
            entry["filename"]
            for entry in body.get("files") or []
            if isinstance(entry, dict) and isinstance(entry.get("filename"), str)
        )
        messages = tuple(
            (entry.get("commit") or {}).get("message", "")
            for entry in body.get("commits") or []
            if isinstance(entry, dict)
        )
        return Comparison(
            base_sha=base, head_sha=head, files=files, commit_messages=messages
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
