"""Realistic Chrome browser headers, WAF challenge detection, and rate limiting."""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from urllib.parse import urlparse

CHROME_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_WAF_MARKERS: tuple[str, ...] = (
    "just a moment...",
    "cf-browser-verification",
    "attention required! | cloudflare",
    "checking your browser before accessing",
    "enable javascript and cookies to continue",
    "__cf_chl_opt",
    "incapsula incident id",
    "datadome",
)


def build_browser_headers(
    url: str,
    *,
    accept_xml: bool = False,
    referer: str | None = None,
) -> dict[str, str]:
    """Build browser-authentic HTTP headers avoiding brotli ('br') encoding pitfalls."""
    parsed = urlparse(url)
    origin_referer = referer or f"{parsed.scheme}://{parsed.netloc}/"
    accept_header = (
        "application/xml,text/xml;q=0.9,application/xhtml+xml;q=0.8,*/*;q=0.5"
        if accept_xml
        else "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    )
    return {
        "User-Agent": CHROME_USER_AGENT,
        "Accept": accept_header,
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate",
        "Referer": origin_referer,
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if referer else "none",
        "Sec-Fetch-User": "?1",
    }


def detect_waf_or_empty_challenge(raw_bytes: bytes, content_type: str) -> str | None:
    """Detect empty payloads or common WAF/Cloudflare anti-bot interstitial pages."""
    if not raw_bytes or not raw_bytes.strip():
        return "EMPTY_RESPONSE"

    ct_lower = (content_type or "").lower()
    if "html" not in ct_lower and not raw_bytes.lstrip().startswith(b"<"):
        return None

    snippet = raw_bytes[:16_384].decode("utf-8", errors="ignore").lower()
    for marker in _WAF_MARKERS:
        if marker in snippet:
            return f"WAF_CHALLENGE:{marker}"

    if len(raw_bytes) < 16_384 and (
        "<title>access denied" in snippet or "<title>403 forbidden" in snippet
    ):
        return "WAF_ACCESS_DENIED_HTML"

    if len(raw_bytes) < 2_048 and "document.cookie" in snippet and (
        "window.location" in snippet or "location.reload" in snippet
    ):
        return "JS_COOKIE_CHALLENGE"

    return None


_SOFT_404_MARKERS: tuple[str, ...] = (
    "không tìm thấy trang",
    "trang không tồn tại",
    "lỗi 404",
    "404 not found",
    "page not found",
)
_TITLE_OR_H1_RE = re.compile(r"<(?:title|h1)\b[^>]*>(.*?)</(?:title|h1)>", re.I | re.S)


def detect_soft_404(raw_bytes: bytes, content_type: str) -> str | None:
    """Detect HTTP 200 Soft-404 error pages by inspecting <title> and <h1> headings."""
    if not raw_bytes:
        return "EMPTY_RESPONSE"
    ct_lower = (content_type or "").lower()
    if "html" not in ct_lower and not raw_bytes.lstrip().startswith(b"<"):
        return None
    snippet = raw_bytes[:32_768].decode("utf-8", errors="ignore")
    for match in _TITLE_OR_H1_RE.finditer(snippet):
        heading = re.sub(r"<[^>]+>", " ", match.group(1)).strip().lower()
        for marker in _SOFT_404_MARKERS:
            if marker in heading:
                return f"SOFT_404:{marker}"
    return None


class DomainRateLimiter:
    """Per-domain async delay lock honoring each source's rate_limit_delay_ms."""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_request_ts: dict[str, float] = {}

    async def wait(self, url: str, delay_ms: int) -> None:
        if delay_ms <= 0:
            return
        domain = urlparse(url).netloc.lower()
        delay_sec = delay_ms / 1000.0
        async with self._locks[domain]:
            now = time.monotonic()
            last_ts = self._last_request_ts.get(domain)
            if last_ts is not None:
                elapsed = now - last_ts
                if elapsed < delay_sec:
                    await asyncio.sleep(delay_sec - elapsed)
            self._last_request_ts[domain] = time.monotonic()

