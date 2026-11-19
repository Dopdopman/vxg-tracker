"""2-Tier orchestrator: fast HTTPX first with automatic Playwright Chromium fallback."""

from __future__ import annotations

from types import TracebackType

from vxg_shared.db.models import Source
from vxg_shared.enums import FetchStatus, FetchStrategy
from vxg_worker.fetcher.browser_client import PlaywrightFetcher
from vxg_worker.fetcher.headers import DomainRateLimiter
from vxg_worker.fetcher.http_client import HttpxFetcher
from vxg_worker.fetcher.types import FetchResult

_FALLBACK_STATUS_CODES = frozenset({403, 429, 503})
_FALLBACK_FETCH_STATUSES = frozenset(
    {
        FetchStatus.BLOCKED,
        FetchStatus.TIMEOUT,
        FetchStatus.HTTP_ERROR,
    }
)


class TwoTierFetcher:
    """Unified source fetcher selecting HTTPX vs Playwright and falling back on blocks."""

    def __init__(
        self,
        *,
        http_fetcher: HttpxFetcher | None = None,
        browser_fetcher: PlaywrightFetcher | None = None,
        rate_limiter: DomainRateLimiter | None = None,
        proxy_url: str | None = None,
        playwright_timeout_ms: int = 30_000,
    ) -> None:
        self._http = http_fetcher or HttpxFetcher(proxy_url=proxy_url)
        self._browser = browser_fetcher or PlaywrightFetcher(proxy_url=proxy_url)
        self._rate_limiter = rate_limiter or DomainRateLimiter()
        self._playwright_timeout_ms = playwright_timeout_ms

    async def __aenter__(self) -> TwoTierFetcher:
        await self._browser.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._browser.__aexit__(exc_type, exc, tb)

    async def fetch_source(self, source: Source) -> FetchResult:
        """Fetch source payload respecting per-domain rate limit and automatic Tier 2 fallback."""
        return await self.fetch_url(
            source.endpoint_url,
            fetch_strategy=source.fetch_strategy,
            timeout_ms=source.timeout_ms,
            rate_limit_delay_ms=source.rate_limit_delay_ms,
            referer=source.base_url,
        )

    async def fetch_url(
        self,
        url: str,
        *,
        fetch_strategy: str,
        timeout_ms: int,
        rate_limit_delay_ms: int,
        referer: str | None = None,
    ) -> FetchResult:
        """Fetch an arbitrary URL (listing or 2-step detail page) using the 2-Tier strategy."""
        await self._rate_limiter.wait(url, rate_limit_delay_ms)

        if fetch_strategy == FetchStrategy.PLAYWRIGHT_JS.value:
            resolved_timeout = max(timeout_ms, self._playwright_timeout_ms)
            return await self._browser.fetch(url, timeout_ms=resolved_timeout)

        accept_xml = fetch_strategy == FetchStrategy.HTTP_XML.value
        tier1 = await self._http.fetch(
            url,
            timeout_ms=timeout_ms,
            accept_xml=accept_xml,
            referer=referer,
        )

        needs_fallback = (
            tier1.http_status in _FALLBACK_STATUS_CODES
            or tier1.fetch_status in _FALLBACK_FETCH_STATUSES
            or tier1.byte_size == 0
        ) and tier1.http_status != 404
        if not needs_fallback:
            return tier1

        tier2_timeout = max(timeout_ms, self._playwright_timeout_ms)
        tier2 = await self._browser.fetch(url, timeout_ms=tier2_timeout)
        tier2.fallback_triggered = True
        tier2.metadata["tier1_attempt"] = {
            "tier": tier1.tier_used.value,
            "http_status": tier1.http_status,
            "fetch_status": tier1.fetch_status.value,
            "response_time_ms": tier1.response_time_ms,
            "error_code": tier1.error_code,
            "error_message": tier1.error_message,
        }
        return tier2

