import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class Settings:
    devin_api_key: str
    devin_api_url: str
    github_webhook_secret: str
    trigger_label: str
    allowed_repos: frozenset[str]

    def is_repo_allowed(self, repository: str) -> bool:
        if not self.allowed_repos:
            return True
        return repository.lower() in self.allowed_repos

    @classmethod
    def from_env(cls) -> "Settings":
        devin_api_key = os.environ.get("DEVIN_API_KEY", "").strip()
        if not devin_api_key:
            raise ConfigError("DEVIN_API_KEY is not set")

        github_webhook_secret = os.environ.get("GITHUB_WEBHOOK_SECRET", "").strip()
        if not github_webhook_secret:
            raise ConfigError("GITHUB_WEBHOOK_SECRET is not set")

        return cls(
            devin_api_key=devin_api_key,
            devin_api_url=os.environ.get(
                "DEVIN_API_URL", "https://api.devin.ai/v1"
            ).rstrip("/"),
            github_webhook_secret=github_webhook_secret,
            trigger_label=os.environ.get("TRIGGER_LABEL", "devin").strip(),
            allowed_repos=frozenset(
                repo.strip().lower()
                for repo in os.environ.get("ALLOWED_REPOS", "").split(",")
                if repo.strip()
            ),
        )
