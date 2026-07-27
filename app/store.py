import asyncio
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Any

# Devin session statuses considered terminal (session is no longer active).
TERMINAL_STATUSES = frozenset({"finished", "expired", "blocked", "stopped", "failed"})


@dataclass(frozen=True)
class TrackedSession:
    session_id: str
    url: str
    repository: str
    issue_number: int
    status: str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def is_active(self) -> bool:
        return self.status.lower() not in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_active"] = self.is_active
        return data


class SessionStore:
    """In-memory store of Devin sessions spun up from GitHub issues."""

    def __init__(self) -> None:
        self._sessions: dict[str, TrackedSession] = {}
        self._lock = asyncio.Lock()

    async def upsert(
        self,
        session_id: str,
        url: str,
        repository: str,
        issue_number: int,
        status: str,
    ) -> TrackedSession:
        async with self._lock:
            existing = self._sessions.get(session_id)
            now = time.time()
            if existing is None:
                session = TrackedSession(
                    session_id=session_id,
                    url=url,
                    repository=repository,
                    issue_number=issue_number,
                    status=status,
                )
            else:
                session = replace(existing, url=url, status=status, updated_at=now)
            self._sessions[session_id] = session
            return session

    async def set_status(self, session_id: str, status: str) -> TrackedSession | None:
        async with self._lock:
            existing = self._sessions.get(session_id)
            if existing is None:
                return None
            session = replace(existing, status=status, updated_at=time.time())
            self._sessions[session_id] = session
            return session

    async def list(self, active_only: bool = False) -> list[TrackedSession]:
        async with self._lock:
            sessions = list(self._sessions.values())
        if active_only:
            sessions = [s for s in sessions if s.is_active]
        return sorted(sessions, key=lambda s: s.created_at, reverse=True)

    async def get(self, session_id: str) -> TrackedSession | None:
        async with self._lock:
            return self._sessions.get(session_id)

    async def clear(self) -> None:
        async with self._lock:
            self._sessions.clear()


store = SessionStore()
