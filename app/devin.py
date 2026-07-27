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
    status: str


def issue_tag(issue: Issue) -> str:
    return f"issue:{issue.repository}#{issue.number}"


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
    def __init__(
        self, api_key: str, api_url: str, org_id: str, timeout: float = 30.0
    ) -> None:
        self._sessions_url = f"{api_url}/v3/organizations/{org_id}/sessions"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout

    async def create_session(self, issue: Issue) -> Session:
        tag = issue_tag(issue)
        payload = {
            "prompt": build_prompt(issue),
            "title": f"Issue #{issue.number}: {issue.title}",
            "repos": [issue.repository],
            "tags": ["src:github", f"repo:{issue.repository}", tag],
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            existing = await self._find_session(client, tag)
            if existing is not None:
                return existing

            response = await client.post(
                self._sessions_url, headers=self._headers, json=payload
            )
            response.raise_for_status()
            return _parse_session(_json_body(response), is_new_session=True)

    async def _find_session(
        self, client: httpx.AsyncClient, tag: str
    ) -> Session | None:
        """Reuse the session already created for this issue, if there is one.

        The v3 API has no `idempotent` flag, so the issue tag is the dedupe key.
        """
        response = await client.get(
            self._sessions_url,
            headers=self._headers,
            params={"tags": tag, "first": 1},
        )
        response.raise_for_status()
        body = _json_body(response)
        items = body.get("items")
        if not isinstance(items, list) or not items:
            return None
        if not isinstance(items[0], dict):
            raise DevinApiError("Devin API returned an unexpected session entry")
        return _parse_session(items[0], is_new_session=False)

    async def get_status(self, session_id: str) -> str:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.get(
                f"{self._sessions_url}/{_devin_id(session_id)}", headers=self._headers
            )
            response.raise_for_status()
            return _extract_status(_json_body(response))


def _devin_id(session_id: str) -> str:
    return session_id if session_id.startswith("devin-") else f"devin-{session_id}"


def _json_body(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise DevinApiError("Devin API returned a non-JSON response") from exc
    if not isinstance(data, dict):
        raise DevinApiError("Devin API returned an unexpected response body")
    return data


def _parse_session(data: dict, is_new_session: bool) -> Session:
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
        is_new_session=is_new_session,
        status=_extract_status(data),
    )


def _extract_status(data: dict) -> str:
    status = data.get("status_enum") or data.get("status")
    if isinstance(status, str) and status:
        return status
    return "unknown"
