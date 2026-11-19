"""Tier 2 headless Chromium fetcher via Playwright for JS-rendered & WAF-protected sites."""

from __future__ import annotations

import contextlib
import time
from types import TracebackType

from playwright.async_api import (
    Browser,
    Playwright,
    Route,
    async_playwright,
)
from playwright.async_api import (
    Error as PlaywrightError,
)
from playwright.async_api import (
    TimeoutError as PlaywrightTimeoutError,
)

from vxg_shared.enums import FetchStatus
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import VN_TZ_NAME
from vxg_worker.fetcher.headers import CHROME_USER_AGENT, detect_waf_or_empty_challenge
from vxg_worker.fetcher.types import FetcherTier, FetchResult

_BLOCKED_RESOURCE_TYPES = frozenset({"image", "media", "font"})
_BLOCKED_TRACKER_DOMAINS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "connect.facebook.net",
)


async def _abort_heavy_assets(route: Route) -> None:
    req = route.request
    if req.resource_type in _BLOCKED_RESOURCE_TYPES or any(
        tracker in req.url for tracker in _BLOCKED_TRACKER_DOMAINS
    ):
        await route.abort()
        return
    await route.continue_()


class PlaywrightFetcher:
    """Lazy-initialized headless Chromium context manager with strict asset & memory guards."""

    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> PlaywrightFetcher:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        if self._browser is not None:
            with contextlib.suppress(Exception):
                await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            with contextlib.suppress(Exception):
                await self._playwright.stop()
            self._playwright = None

    async def _ensure_browser(self) -> Browser:
        if self._browser is not None:
            return self._browser
        self._playwright = await async_playwright().start()
        launch_args = [
            "--disable-dev-shm-usage",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]
        if self._proxy_url:
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=launch_args,
                proxy={"server": self._proxy_url},
            )
        else:
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=launch_args,
            )
        return self._browser

    async def fetch(
        self,
        url: str,
        *,
        timeout_ms: int = 30_000,
    ) -> FetchResult:
        start_mono = time.monotonic()
        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=CHROME_USER_AGENT,
            locale="vi-VN",
            timezone_id=VN_TZ_NAME,
            viewport={"width": 1366, "height": 768},
            extra_http_headers={
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        page = await context.new_page()
        await page.route("**/*", _abort_heavy_assets)

        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            with contextlib.suppress(PlaywrightTimeoutError):
                await page.wait_for_load_state("networkidle", timeout=4_000)

            html_str = ""
            for _ in range(3):
                try:
                    html_str = await page.content()
                except PlaywrightError:
                    await page.wait_for_timeout(800)
                    continue
                is_tiny_or_js_challenge = (
                    len(html_str.strip()) < 1_000
                    or detect_waf_or_empty_challenge(html_str.encode("utf-8"), "text/html")
                    == "JS_COOKIE_CHALLENGE"
                )
                if not is_tiny_or_js_challenge:
                    break
                with contextlib.suppress(PlaywrightTimeoutError):
                    await page.wait_for_load_state("load", timeout=4_000)
                await page.wait_for_timeout(1_200)

            raw_bytes = html_str.encode("utf-8")
            elapsed_ms = int((time.monotonic() - start_mono) * 1000)
            http_status = response.status if response is not None else None
            final_url = page.url or url
            content_type = (
                response.headers.get("content-type", "text/html; charset=utf-8")
                if response is not None
                else "text/html; charset=utf-8"
            )

            if http_status is not None and http_status in (403, 429):
                return FetchResult(
                    request_url=url,
                    final_url=final_url,
                    http_status=http_status,
                    content_type=content_type,
                    raw_bytes=raw_bytes,
                    content_sha256=compute_content_sha256(raw_bytes) if raw_bytes else None,
                    byte_size=len(raw_bytes),
                    response_time_ms=elapsed_ms,
                    attempts=1,
                    tier_used=FetcherTier.PLAYWRIGHT,
                    fetch_status=FetchStatus.BLOCKED,
                    error_code=f"HTTP_{http_status}",
                    error_message=f"Playwright blocked with HTTP {http_status}",
                )

            if http_status is not None and http_status >= 400:
                return FetchResult(
                    request_url=url,
                    final_url=final_url,
                    http_status=http_status,
                    content_type=content_type,
                    raw_bytes=raw_bytes,
                    content_sha256=compute_content_sha256(raw_bytes) if raw_bytes else None,
                    byte_size=len(raw_bytes),
                    response_time_ms=elapsed_ms,
                    attempts=1,
                    tier_used=FetcherTier.PLAYWRIGHT,
                    fetch_status=FetchStatus.HTTP_ERROR,
                    error_code=f"HTTP_{http_status}",
                    error_message=f"Playwright received HTTP {http_status}",
                )

            waf_reason = detect_waf_or_empty_challenge(raw_bytes, content_type)
            if waf_reason is not None:
                return FetchResult(
                    request_url=url,
                    final_url=final_url,
                    http_status=http_status,
                    content_type=content_type,
                    raw_bytes=raw_bytes,
                    content_sha256=compute_content_sha256(raw_bytes) if raw_bytes else None,
                    byte_size=len(raw_bytes),
                    response_time_ms=elapsed_ms,
                    attempts=1,
                    tier_used=FetcherTier.PLAYWRIGHT,
                    fetch_status=FetchStatus.BLOCKED,
                    error_code="WAF_OR_EMPTY",
                    error_message=waf_reason,
                )

            return FetchResult(
                request_url=url,
                final_url=final_url,
                http_status=http_status,
                content_type=content_type,
                raw_bytes=raw_bytes,
                content_sha256=compute_content_sha256(raw_bytes),
                byte_size=len(raw_bytes),
                response_time_ms=elapsed_ms,
                attempts=1,
                tier_used=FetcherTier.PLAYWRIGHT,
                fetch_status=FetchStatus.SUCCESS,
            )

        except PlaywrightTimeoutError as exc:
            elapsed_ms = int((time.monotonic() - start_mono) * 1000)
            return FetchResult(
                request_url=url,
                final_url=page.url or url,
                http_status=None,
                content_type="text/html",
                raw_bytes=b"",
                content_sha256=None,
                byte_size=0,
                response_time_ms=elapsed_ms,
                attempts=1,
                tier_used=FetcherTier.PLAYWRIGHT,
                fetch_status=FetchStatus.TIMEOUT,
                error_code="PlaywrightTimeoutError",
                error_message=str(exc) or "Playwright navigation timed out",
            )
        except PlaywrightError as exc:
            elapsed_ms = int((time.monotonic() - start_mono) * 1000)
            return FetchResult(
                request_url=url,
                final_url=page.url or url,
                http_status=None,
                content_type="text/html",
                raw_bytes=b"",
                content_sha256=None,
                byte_size=0,
                response_time_ms=elapsed_ms,
                attempts=1,
                tier_used=FetcherTier.PLAYWRIGHT,
                fetch_status=FetchStatus.HTTP_ERROR,
                error_code=type(exc).__name__,
                error_message=str(exc) or "Playwright navigation error",
            )
        finally:
            with contextlib.suppress(Exception):
                await page.close()
            with contextlib.suppress(Exception):
                await context.close()
