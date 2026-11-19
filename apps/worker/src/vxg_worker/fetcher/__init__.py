"""2-tier HTTPX + Playwright fetcher package for vxg-worker."""

from vxg_worker.fetcher.browser_client import PlaywrightFetcher
from vxg_worker.fetcher.headers import (
    CHROME_USER_AGENT,
    DomainRateLimiter,
    build_browser_headers,
    detect_waf_or_empty_challenge,
)
from vxg_worker.fetcher.http_client import HttpxFetcher
from vxg_worker.fetcher.two_tier import TwoTierFetcher
from vxg_worker.fetcher.types import FetcherTier, FetchResult

__all__ = [
    "CHROME_USER_AGENT",
    "DomainRateLimiter",
    "FetchResult",
    "FetcherTier",
    "HttpxFetcher",
    "PlaywrightFetcher",
    "TwoTierFetcher",
    "build_browser_headers",
    "detect_waf_or_empty_challenge",
]

