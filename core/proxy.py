"""Proxy rotation *interface* — no provider wired.

This is intentionally a no-op pass-through. It exists so scrapers/validators
can request a session without knowing whether a proxy pool is configured.

IMPORTANT (design note): committing to a specific proxy provider, and using
proxy rotation specifically to defeat bot protection on third-party sites, is
a decision with legal/ToS implications. That decision is deferred and should
be made explicitly, not defaulted into. Until a provider is chosen this
returns direct (un-proxied) connections.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.config import get_settings
from core.logging import get_logger

log = get_logger("core.proxy")


@dataclass
class ProxyLease:
    """A leased outbound identity. `proxies` maps to requests/httpx format."""

    proxies: dict[str, str] | None  # None => direct connection
    label: str


class ProxyProvider:
    """Pluggable interface. The default implementation does nothing."""

    def acquire(self) -> ProxyLease:  # pragma: no cover - trivial default
        return ProxyLease(proxies=None, label="direct")

    def report_result(self, lease: ProxyLease, *, success: bool) -> None:
        """Feedback hook so a real provider can rotate on failure/blocks."""
        return None


def get_proxy_provider() -> ProxyProvider:
    """Factory. Returns the no-op provider unless one is explicitly configured."""
    settings = get_settings()
    if settings.proxy_provider and settings.proxy_provider != "none":
        log.warning(
            "proxy_provider.configured_but_unimplemented",
            provider=settings.proxy_provider,
            note="No concrete provider is wired; falling back to direct.",
        )
    return ProxyProvider()
