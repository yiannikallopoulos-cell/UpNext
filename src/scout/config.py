"""Configuration loaded from environment variables.

Centralized settings so the rest of the codebase never reads os.environ directly.
Values come from `.env` (development) or the actual environment (production).
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment.

    All defaults are intentionally absent for secrets — the app fails loudly
    if required values are missing rather than running with empty credentials.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Database
    database_url: str = Field(..., description="Supabase Postgres connection string")

    # External APIs
    apify_api_token: str = Field(..., description="Apify API token")
    anthropic_api_key: str = Field(..., description="Anthropic Claude API key")

    # Operational
    log_level: str = Field(default="INFO", description="Logging level")
    environment: str = Field(default="development", description="Environment name")

    # Scoring constants. Centralized here so they're easy to find and tune.
    follower_band_min: int = 5_000
    follower_band_max: int = 50_000
    graduation_threshold: int = 50_000
    dormant_days_threshold: int = 60
    settlement_post_age_days: int = 14
    seed_diversity_max_share: float = 0.05  # no single neighbors-seed >5% of harvest

    # Tracking tier cadences in days.
    watchlist_cadence_days: int = 7
    active_cadence_days: int = 14
    long_tail_cadence_days: int = 30
    graduated_cadence_days: int = 7


# Singleton accessor. Import this rather than instantiating Settings directly
# so we get a single, cached configuration object across the app.
_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the singleton Settings instance, loading on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
