import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than 0")
    return value


@dataclass(frozen=True)
class Settings:
    devin_api_key: str
    devin_api_url: str
    devin_org_id: str
    github_webhook_secret: str
    trigger_label: str
    allowed_repos: frozenset[str]
    dep_scan_enabled: bool
    dep_scan_interval_seconds: float
    dep_scan_repos: tuple[str, ...]
    dep_audit_enabled: bool
    dep_audit_timeout_seconds: float
    github_token: str

    def is_repo_allowed(self, repository: str) -> bool:
        if not self.allowed_repos:
            return True
        return repository.lower() in self.allowed_repos

    @classmethod
    def from_env(cls) -> "Settings":
        devin_api_key = os.environ.get("DEVIN_API_KEY", "").strip()
        if not devin_api_key:
            raise ConfigError("DEVIN_API_KEY is not set")

        devin_org_id = os.environ.get("DEVIN_ORG_ID", "").strip()
        if not devin_org_id:
            raise ConfigError("DEVIN_ORG_ID is not set")

        github_webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip()
        if not github_webhook_secret:
            raise ConfigError("GITHUB_WEBHOOK_SECRET is not set")

        allowed_repos = frozenset(
            repo.strip().lower()
            for repo in os.environ.get("ALLOWED_REPOS", "").split(",")
            if repo.strip()
        )
        dep_scan_repos = tuple(
            repo.strip()
            for repo in os.environ.get("DEP_SCAN_REPOS", "").split(",")
            if repo.strip()
        ) or tuple(sorted(allowed_repos))

        return cls(
            devin_api_key=devin_api_key,
            devin_api_url=os.environ.get(
                "DEVIN_API_URL", "https://api.devin.ai"
            ).rstrip("/"),
            devin_org_id=devin_org_id,
            github_webhook_secret=github_webhook_secret,
            trigger_label=os.environ.get("TRIGGER_LABEL", "devin").strip(),
            allowed_repos=allowed_repos,
            dep_scan_enabled=_bool_env("DEP_SCAN_ENABLED", default=True),
            dep_scan_interval_seconds=_float_env(
                "DEP_SCAN_INTERVAL_SECONDS", default=150.0
            ),
            dep_scan_repos=dep_scan_repos,
            dep_audit_enabled=_bool_env("DEP_AUDIT_ENABLED", default=True),
            dep_audit_timeout_seconds=_float_env(
                "DEP_AUDIT_TIMEOUT_SECONDS", default=300.0
            ),
            github_token=os.environ.get("GITHUB_TOKEN", "").strip(),
        )
