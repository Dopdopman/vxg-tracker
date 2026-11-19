"""Unit tests for browser headers, WAF challenge detection, and 2-tier fallback logic."""

from __future__ import annotations

import httpx
import pytest

from vxg_shared.db.models import Source
from vxg_shared.enums import CommodityCategory, FetchStatus, FetchStrategy, SourceTier
from vxg_worker.fetcher.browser_client import PlaywrightFetcher
from vxg_worker.fetcher.headers import build_browser_headers, detect_waf_or_empty_challenge
from vxg_worker.fetcher.http_client import HttpxFetcher
from vxg_worker.fetcher.two_tier import TwoTierFetcher
from vxg_worker.fetcher.types import FetcherTier, FetchResult


class _FakePlaywrightFetcher(PlaywrightFetcher):
    def __init__(self) -> None:
        super().__init__()
        self.called_urls: list[str] = []

    async def __aenter__(self) -> _FakePlaywrightFetcher:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def fetch(self, url: str, *, timeout_ms: int = 30_000) -> FetchResult:
        self.called_urls.append(url)
        return FetchResult(
            request_url=url,
            final_url=url,
            http_status=200,
            content_type="text/html; charset=utf-8",
            raw_bytes=b"<html><title>Rendered</title><body>Rendered table</body></html>",
            content_sha256="a" * 64,
            byte_size=62,
            response_time_ms=420,
            attempts=1,
            tier_used=FetcherTier.PLAYWRIGHT,
            fetch_status=FetchStatus.SUCCESS,
        )


def _make_source(
    *,
    code: str = "PNJ_HTML",
    url: str = "https://www.pnj.com.vn/blog/gia-vang/",
    strategy: FetchStrategy = FetchStrategy.HTTP_HTML,
) -> Source:
    return Source(
        id=3,
        code=code,
        name="Test Source",
        category=CommodityCategory.GOLD.value,
        tier=SourceTier.PRIMARY_OFFICIAL.value,
        base_url="https://www.pnj.com.vn",
        endpoint_url=url,
        fetch_strategy=strategy.value,
        parser_key="gold.pnj_html",
        schedule_cron="35 8 * * *",
        timeout_ms=5000,
        rate_limit_delay_ms=0,
        is_active=True,
    )


def test_browser_headers_and_waf_detection() -> None:
    html_headers = build_browser_headers("https://www.pnj.com.vn/blog/gia-vang/")
    assert "Chrome/" in html_headers["User-Agent"]
    assert html_headers["Accept-Language"].startswith("vi-VN")
    assert "br" not in html_headers["Accept-Encoding"]
    assert html_headers["Referer"] == "https://www.pnj.com.vn/"

    xml_headers = build_browser_headers(
        "https://sjc.com.vn/xml/tygiavang.xml",
        accept_xml=True,
        referer="https://sjc.com.vn",
    )
    assert xml_headers["Accept"].startswith("application/xml")
    assert xml_headers["Referer"] == "https://sjc.com.vn"

    assert detect_waf_or_empty_challenge(b"   ", "text/html") == "EMPTY_RESPONSE"
    assert (
        detect_waf_or_empty_challenge(
            b"<html><body><script>document.cookie='D1N=abc';window.location.reload(true);</script></body></html>",
            "text/html",
        )
        == "JS_COOKIE_CHALLENGE"
    )
    assert (
        detect_waf_or_empty_challenge(
            b"<html><title>Just a moment...</title><body>cf-browser-verification</body></html>",
            "text/html",
        )
        is not None
    )
    assert (
        detect_waf_or_empty_challenge(
            b"<html><head><title>Gia vang SJC</title></head><body><table><tr><td>85.000</td></tr></table></body></html>",
            "text/html",
        )
        is None
    )
    assert (
        detect_waf_or_empty_challenge(
            b'<?xml version="1.0"?><root><ratelist updated="14:30"></ratelist></root>',
            "application/xml",
        )
        is None
    )


@pytest.mark.asyncio
async def test_httpx_retries_on_503_and_stops_on_403() -> None:
    calls_503 = 0

    def handler_503(request: httpx.Request) -> httpx.Response:
        nonlocal calls_503
        calls_503 += 1
        if calls_503 == 1:
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><title>OK</title><body>Valid body content</body></html>",
        )

    fetcher_retry = HttpxFetcher(
        transport=httpx.MockTransport(handler_503),
        base_backoff_seconds=0.0,
    )
    res_ok = await fetcher_retry.fetch("https://example.vn/prices", max_attempts=3)
    assert res_ok.fetch_status == FetchStatus.SUCCESS
    assert res_ok.attempts == 2
    assert res_ok.tier_used == FetcherTier.HTTPX
    assert res_ok.content_sha256 is not None

    calls_403 = 0

    def handler_403(request: httpx.Request) -> httpx.Response:
        nonlocal calls_403
        calls_403 += 1
        return httpx.Response(403, text="Forbidden")

    fetcher_403 = HttpxFetcher(
        transport=httpx.MockTransport(handler_403),
        base_backoff_seconds=0.0,
    )
    res_blocked = await fetcher_403.fetch("https://example.vn/blocked", max_attempts=3)
    assert res_blocked.fetch_status == FetchStatus.BLOCKED
    assert res_blocked.http_status == 403
    assert res_blocked.attempts == 1


@pytest.mark.asyncio
async def test_two_tier_fallback_on_403_and_direct_playwright_strategy() -> None:
    def blocked_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Cloudflare 403")

    pw_stub = _FakePlaywrightFetcher()
    two_tier = TwoTierFetcher(
        http_fetcher=HttpxFetcher(
            transport=httpx.MockTransport(blocked_handler),
            base_backoff_seconds=0.0,
        ),
        browser_fetcher=pw_stub,
    )

    res = await two_tier.fetch_source(_make_source(strategy=FetchStrategy.HTTP_HTML))
    assert res.tier_used == FetcherTier.PLAYWRIGHT
    assert res.fetch_status == FetchStatus.SUCCESS
    assert res.fallback_triggered is True
    assert res.metadata["tier1_attempt"]["http_status"] == 403
    assert len(pw_stub.called_urls) == 1

    # PLAYWRIGHT_JS skips Tier 1 completely
    pw_stub2 = _FakePlaywrightFetcher()
    direct_fetcher = TwoTierFetcher(
        http_fetcher=HttpxFetcher(
            transport=httpx.MockTransport(
                lambda req: (_ for _ in ()).throw(AssertionError("HTTPX must not run"))
            ),
            base_backoff_seconds=0.0,
        ),
        browser_fetcher=pw_stub2,
    )
    res_direct = await direct_fetcher.fetch_source(
        _make_source(code="DOJI_BOARD", url="https://banggia.doji.vn", strategy=FetchStrategy.PLAYWRIGHT_JS)
    )
    assert res_direct.tier_used == FetcherTier.PLAYWRIGHT
    assert res_direct.fallback_triggered is False
    assert pw_stub2.called_urls == ["https://banggia.doji.vn"]

    # 503 after retries / empty body / timeout also fall back to Playwright
    def empty_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"")

    pw_stub3 = _FakePlaywrightFetcher()
    empty_fallback = TwoTierFetcher(
        http_fetcher=HttpxFetcher(
            transport=httpx.MockTransport(empty_handler),
            base_backoff_seconds=0.0,
        ),
        browser_fetcher=pw_stub3,
    )
    res_empty = await empty_fallback.fetch_source(_make_source(strategy=FetchStrategy.HTTP_HTML))
    assert res_empty.tier_used == FetcherTier.PLAYWRIGHT
    assert res_empty.fallback_triggered is True
    assert res_empty.metadata["tier1_attempt"]["error_code"] == "WAF_OR_EMPTY"
