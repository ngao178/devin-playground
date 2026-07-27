from dataclasses import dataclass

import httpx


class DevinApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Issue:
    repository: str
    number: int
    title: str
    body: str
    url: str


@dataclass(frozen=True)
class Session:
    session_id: str
    url: str
    is_new_session: bool


def build_prompt(issue: Issue) -> str:
    ref = f"{issue.repository}#{issue.number}"
    return "\n".join(
        [
            f"Triage and resolve this GitHub issue: {issue.url}",
            "",
            f"Repository: {issue.repository}",
            f"Title: {issue.title}",
            "",
            "Body:",
            issue.body or "(no description provided)",
            "",
            "REQUIREMENTS:",
            "- Investigate the issue first and report your plan before making changes.",
            f"- When you open a PR, include this exact line in the description: Fixes {ref}",
            f'- Include "Refs {ref}" in every commit message of that PR.',
            f"- Once the PR is open, comment its URL on issue {issue.url} so the reporter is notified.",
        ]
    )


class DevinClient:
    def __init__(self, api_key: str, api_url: str, timeout: float = 30.0) -> None:
        self._api_url = api_url
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    async def create_session(self, issue: Issue) -> Session:
        payload = {
            "prompt": build_prompt(issue),
            "title": f"Issue #{issue.number}: {issue.title}",
            "idempotent": True,
            "tags": [
                "src:github",
                f"repo:{issue.repository}",
                f"issue:{issue.number}",
            ],
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                f"{self._api_url}/sessions", headers=self._headers, json=payload
            )
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise DevinApiError("Devin API returned a non-JSON response") from exc

        if not isinstance(data, dict):
            raise DevinApiError("Devin API returned an unexpected response body")

        session_id = data.get("session_id")
        url = data.get("url")
        missing = [
            name
            for name, value in (("session_id", session_id), ("url", url))
            if not isinstance(value, str) or not value
        ]
        if missing:
            raise DevinApiError(
                f"Devin API response missing field(s): {', '.join(missing)}"
            )

        return Session(
            session_id=session_id,
            url=url,
            is_new_session=bool(data.get("is_new_session", True)),
        )
