"""Environment-driven configuration for the Flock Energy API.

Nothing here is hardcoded that a deployer would need to change. Credentials in
particular are never given a default — the app must fail loudly at startup if they are
missing, rather than silently trying an empty login against the real portal.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="URJA_", extra="ignore")

    # -- Legacy portal connection --------------------------------------------------
    portal_base_url: str = "https://urja-ops.flockenergy.tech"
    portal_email: str
    portal_password: str

    # -- HTTP behaviour ---------------------------------------------------------
    request_timeout_seconds: float = 10.0
    # Candidate JSON API paths tried before falling back to HTML parsing. See
    # PROTOCOL.md §4 for why this is a probe-then-fallback strategy rather than a
    # single assumed shape.
    prefer_json_api: bool = True

    # -- API metadata -------------------------------------------------------------
    api_title: str = "Flock Energy — Urja Meter Ops API"
    api_version: str = "1.0.0"
    api_description: str = (
        "Clean REST API proxy layer over the legacy Urja Meter Ops portal. "
        "Handles authentication, session management, and HTML/JSON normalisation "
        "so downstream clients never talk to the legacy portal directly."
    )


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — read once, reused for the life of the process."""
    return Settings()


settings = get_settings()
