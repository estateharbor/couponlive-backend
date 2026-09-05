"""Central, env-driven configuration.

Everything the app needs to know about *this* environment lives here and is
loaded once. Dev vs prod behaviour (scrape cadence, throttles, validation
on/off) is expressed as data, not as scattered `if ENV == "prod"` checks.
"""
from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
    dev = "dev"
    prod = "prod"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="",
        extra="ignore",
        case_sensitive=False,
    )

    # Runtime
    environment: Environment = Field(Environment.dev, alias="COUPONLIVE_ENV")

    # Database
    database_url: str = Field(
        "postgresql+psycopg://postgres:postgres@localhost:5432/couponlive",
        alias="DATABASE_URL",
    )

    # Redis / Celery
    redis_url: str = Field("redis://localhost:6379/0", alias="REDIS_URL")

    # Scraping cadence / throttles
    scrape_frequency_multiplier: float = Field(4.0, alias="SCRAPE_FREQUENCY_MULTIPLIER")
    scrape_max_sources_per_run: int = Field(1, alias="SCRAPE_MAX_SOURCES_PER_RUN")
    scrape_min_delay_seconds: float = Field(2.0, alias="SCRAPE_MIN_DELAY_SECONDS")
    scrape_max_delay_seconds: float = Field(8.0, alias="SCRAPE_MAX_DELAY_SECONDS")

    # Validation cadence (Phase 4)
    validate_top_merchant_revalidate_hours: int = Field(
        4, alias="VALIDATE_TOP_MERCHANT_REVALIDATE_HOURS"
    )
    validation_enabled: bool = Field(False, alias="VALIDATION_ENABLED")

    # Freshness / staleness policy
    serve_freshness_hours: int = Field(4, alias="SERVE_FRESHNESS_HOURS")
    stale_expire_hours: int = Field(48, alias="STALE_EXPIRE_HOURS")

    # Proxy hook (interface only — no provider wired)
    proxy_provider: str = Field("", alias="PROXY_PROVIDER")
    proxy_pool_url: str = Field("", alias="PROXY_POOL_URL")

    # Affiliate API keys (preferred ingestion path)
    cuelinks_api_key: str = Field("", alias="CUELINKS_API_KEY")
    # Cuelinks Offers API (coupon/deal feed across 400+ merchants, incl. Amazon).
    # Endpoint is env-overridable so we can correct it against the live response.
    cuelinks_api_url: str = Field(
        "https://www.cuelinks.com/api/v2/offers.json", alias="CUELINKS_API_URL"
    )
    cuelinks_sync_frequency_minutes: int = Field(
        60, alias="CUELINKS_SYNC_FREQUENCY_MINUTES"
    )
    # Feedico coupon-catalog API (free tier: 1000 req/mo, 40k+ merchants,
    # deduped codes). "Discovery only" — codes but NO affiliate tracking link.
    feedico_api_key: str = Field("", alias="FEEDICO_API_KEY")      # bearer token, e.g. fdco_...
    feedico_api_url: str = Field(
        "https://feedico.io/api/v1/catalog/coupons", alias="FEEDICO_API_URL"
    )
    feedico_country: str = Field("IN", alias="FEEDICO_COUNTRY")    # focus on India; "" = all
    feedico_sync_frequency_minutes: int = Field(
        720, alias="FEEDICO_SYNC_FREQUENCY_MINUTES"               # 12h — respect the 1000/mo cap
    )
    inrdeals_api_key: str = Field("", alias="INRDEALS_API_KEY")   # INRDeals API token
    inrdeals_username: str = Field("", alias="INRDEALS_USERNAME")  # INRDeals `id` param
    earnkaro_api_key: str = Field("", alias="EARNKARO_API_KEY")
    admitad_client_id: str = Field("", alias="ADMITAD_CLIENT_ID")
    admitad_client_secret: str = Field("", alias="ADMITAD_CLIENT_SECRET")
    vcommission_api_key: str = Field("", alias="VCOMMISSION_API_KEY")

    # LinkMyDeals coupon-feed API (structured coupon aggregator)
    linkmydeals_api_url: str = Field(
        "https://feed.linkmydeals.com/getOffers/", alias="LINKMYDEALS_API_URL"
    )
    linkmydeals_api_key: str = Field("", alias="LINKMYDEALS_API_KEY")
    linkmydeals_sync_frequency_minutes: int = Field(
        30, alias="LINKMYDEALS_SYNC_FREQUENCY_MINUTES"
    )

    # Alerting
    alert_webhook_url: str = Field("", alias="ALERT_WEBHOOK_URL")
    alert_min_success_rate: float = Field(0.5, alias="ALERT_MIN_SUCCESS_RATE")
    source_stale_hours: int = Field(24, alias="SOURCE_STALE_HOURS")

    # Feedback abuse prevention: salt for hashing submitter IPs (never store raw IP).
    feedback_ip_salt: str = Field("change-me-in-prod", alias="FEEDBACK_IP_SALT")

    # API server
    api_host: str = Field("0.0.0.0", alias="API_HOST")
    api_port: int = Field(8000, alias="API_PORT")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # CORS: comma-separated origins allowed to call the API from a browser.
    # The static frontend is served from a different origin, so it MUST be listed.
    cors_origins: str = Field(
        "https://couponlive.in,https://www.couponlive.in,http://localhost:3000",
        alias="CORS_ORIGINS",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @field_validator("database_url")
    @classmethod
    def _normalize_db_driver(cls, v: str) -> str:
        """Managed hosts (Railway/Neon/Supabase) hand out `postgres://` or
        `postgresql://` URLs; SQLAlchemy needs the psycopg3 driver spelled out.
        Rewrite the scheme so the platform-provided URL works as-is."""
        for prefix in ("postgresql+psycopg://", "postgresql+"):
            if v.startswith(prefix):
                return v  # already has an explicit driver
        if v.startswith("postgres://"):
            return "postgresql+psycopg://" + v[len("postgres://"):]
        if v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://"):]
        return v

    @property
    def is_prod(self) -> bool:
        return self.environment is Environment.prod

    def effective_scrape_interval_minutes(self, source_frequency_minutes: int) -> float:
        """Apply the env multiplier to a source's configured cadence.

        dev slows everything down; prod typically uses multiplier=1.0.
        """
        return source_frequency_minutes * self.scrape_frequency_multiplier


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Import this, don't instantiate Settings directly."""
    return Settings()
