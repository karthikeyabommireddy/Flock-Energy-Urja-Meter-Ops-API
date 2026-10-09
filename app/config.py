"""Environment-driven configuration for the Flock Energy API.

Nothing here is hardcoded that a deployer would need to change. Credentials in
particular are never given a default — the app must fail loudly at startup if they are
missing, rather than silently trying an empty login against the real portal.
"""

from functools import lru_cache
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class Settings(BaseSettings):
    # hide_input_in_errors: a start-up validation error must never echo the password back.
    model_config = SettingsConfigDict(env_file=".env", env_prefix="URJA_", extra="ignore", hide_input_in_errors=True)

    # -- Legacy portal connection --------------------------------------------------
    portal_base_url: str = "https://urja-ops.flockenergy.tech"
    portal_email: str
    # A SecretStr never shows its value in a repr, a log line or a settings dump; main.py unwraps it once, to log in.
    portal_password: SecretStr

    # -- HTTP behaviour ---------------------------------------------------------
    request_timeout_seconds: float = 10.0
    # IANA zone the portal's naive timestamps ("23/06/2026 23:30") are read in before they are
    # converted to UTC. The portal states none: Asia/Kolkata is an assumption (the network is in
    # Jaipur), recorded in PROTOCOL.md. Set it if that turns out to be wrong.
    portal_timezone: str = "Asia/Kolkata"

    # -- Robustness towards the portal ---------------------------------------------
    max_retries: int = Field(default=2, ge=0)  # retries for timeouts, connection errors and 429/5xx
    max_concurrency: int = Field(default=4, ge=1)  # requests in flight to the portal at any moment

    # -- Cache freshness, in seconds ------------------------------------------------
    cache_ttl_seconds: int = Field(default=300, ge=1)  # meter list, meter detail, hierarchy
    cache_ttl_consumption_seconds: int = Field(default=60, ge=1)
    # How long past the TTL stale data may still be served while the portal is down.
    cache_max_stale_seconds: int = Field(default=3600, ge=0)
    # Coordinates cost one portal request per meter and effectively never change.
    cache_ttl_geo_seconds: int = Field(default=3600, ge=1)

    # -- Browser clients -----------------------------------------------------------
    # Comma-separated origins allowed to call this API from a browser (the web/ client in development).
    # GET only. Empty disables CORS.
    cors_allow_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # -- Logging -------------------------------------------------------------------
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # -- API metadata -------------------------------------------------------------
    api_title: str = "Flock Energy — Urja Meter Ops API"
    api_version: str = "1.1.0"
    api_description: str = (
        "Clean REST API proxy layer over the legacy Urja Meter Ops portal. "
        "Handles authentication, session management and response normalisation "
        "so downstream clients never talk to the legacy portal directly."
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_allow_origins.split(",") if origin.strip()]

    @field_validator("portal_email", "portal_password", mode="before")
    @classmethod
    def _credentials_not_blank(cls, value: Any) -> Any:
        """A blank credential must stop the app at start-up, never become an empty login attempt."""
        if isinstance(value, str) and not value.strip():
            raise ValueError("must not be blank (copy .env.example to .env and fill it in)")
        return value

    @field_validator("portal_base_url")
    @classmethod
    def _https_unless_local(cls, value: str) -> str:
        """The password travels to this URL: plain http is only accepted for a mock portal on this machine."""
        parts = urlsplit(value)
        if parts.scheme == "https" and parts.hostname:
            return value
        if parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS:
            return value
        raise ValueError("must be an https:// URL (plain http is only accepted for localhost)")


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance — read once, reused for the life of the process."""
    return Settings()


settings = get_settings()
