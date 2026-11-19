"""Tier 1 asynchronous HTTP fetcher using httpx with browser headers & jittered retry."""

from __future__ import annotations

import asyncio
import random
import time

import httpx

from vxg_shared.enums import FetchStatus
from vxg_shared.hashing import compute_content_sha256
from vxg_worker.fetcher.headers import build_browser_headers, detect_waf_or_empty_challenge
from vxg_worker.fetcher.types import FetcherTier, FetchResult

_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
_BLOCKED_STATUS_CODES = frozenset({403, 429})


class HttpxFetcher:
    """Fast Tier 1 HTTP fetcher for XML, JSON, and static/SSR HTML endpoints."""

    def __init__(
        self,
        *,
        proxy_url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        base_backoff_seconds: float = 0.8,
    ) -> None:
        self._proxy_url = proxy_url
        self._transport = transport
        self._base_backoff_seconds = base_backoff_seconds

    async def fetch(
        self,
        url: str,
        *,
        timeout_ms: int = 15_000,
        accept_xml: bool = False,
        referer: str | None = None,
        max_attempts: int = 3,
    ) -> FetchResult:
        headers = build_browser_headers(url, accept_xml=accept_xml, referer=referer)
        timeout = httpx.Timeout(timeout_ms / 1000.0)
        start_mono = time.monotonic()
        last_status: int | None = None
        last_final_url: str | None = None
        last_error_code: str | None = None
        last_error_msg: str | None = None

        async with httpx.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
            verify=True,
            proxy=self._proxy_url,
            transport=self._transport,
        ) as client:
            for attempt in range(1, max_attempts + 1):
                try:
                    response = await client.get(url)
                    elapsed_ms = int((time.monotonic() - start_mono) * 1000)
                    last_status = response.status_code
                    last_final_url = str(response.url)
                    content_type = response.headers.get("content-type", "application/octet-stream")
                    raw_bytes = response.content

                    if last_status in _RETRYABLE_STATUS_CODES and attempt < max_attempts:
                        await self._sleep_backoff(attempt)
                        continue

                    if last_status in _BLOCKED_STATUS_CODES:
                        return FetchResult(
                            request_url=url,
                            final_url=last_final_url,
                            http_status=last_status,
                            content_type=content_type,
                            raw_bytes=raw_bytes,
                            content_sha256=(
                                compute_content_sha256(raw_bytes) if raw_bytes else None
                            ),
                            byte_size=len(raw_bytes),
                            response_time_ms=elapsed_ms,
                            attempts=attempt,
                            tier_used=FetcherTier.HTTPX,
                            fetch_status=FetchStatus.BLOCKED,
                            error_code=f"HTTP_{last_status}",
                            error_message=f"Blocked with HTTP {last_status}",
                        )

                    if last_status >= 400:
                        return FetchResult(
                            request_url=url,
                            final_url=last_final_url,
                            http_status=last_status,
                            content_type=content_type,
                            raw_bytes=raw_bytes,
                            content_sha256=(
                                compute_content_sha256(raw_bytes) if raw_bytes else None
                            ),
                            byte_size=len(raw_bytes),
                            response_time_ms=elapsed_ms,
                            attempts=attempt,
                            tier_used=FetcherTier.HTTPX,
                            fetch_status=FetchStatus.HTTP_ERROR,
                            error_code=f"HTTP_{last_status}",
                            error_message=f"HTTP error status {last_status}",
                        )

                    waf_reason = detect_waf_or_empty_challenge(raw_bytes, content_type)
                    if waf_reason is not None:
                        return FetchResult(
                            request_url=url,
                            final_url=last_final_url,
                            http_status=last_status,
                            content_type=content_type,
                            raw_bytes=raw_bytes,
                            content_sha256=(
                                compute_content_sha256(raw_bytes) if raw_bytes else None
                            ),
                            byte_size=len(raw_bytes),
                            response_time_ms=elapsed_ms,
                            attempts=attempt,
                            tier_used=FetcherTier.HTTPX,
                            fetch_status=FetchStatus.BLOCKED,
                            error_code="WAF_OR_EMPTY",
                            error_message=waf_reason,
                        )

                    return FetchResult(
                        request_url=url,
                        final_url=last_final_url,
                        http_status=last_status,
                        content_type=content_type,
                        raw_bytes=raw_bytes,
                        content_sha256=compute_content_sha256(raw_bytes),
                        byte_size=len(raw_bytes),
                        response_time_ms=elapsed_ms,
                        attempts=attempt,
                        tier_used=FetcherTier.HTTPX,
                        fetch_status=FetchStatus.SUCCESS,
                    )

                except httpx.TimeoutException as exc:
                    last_error_code = type(exc).__name__
                    last_error_msg = str(exc) or "HTTP request timed out"
                    if attempt < max_attempts:
                        await self._sleep_backoff(attempt)
                        continue
                    elapsed_ms = int((time.monotonic() - start_mono) * 1000)
                    return FetchResult(
                        request_url=url,
                        final_url=last_final_url,
                        http_status=last_status,
                        content_type="text/plain",
                        raw_bytes=b"",
                        content_sha256=None,
                        byte_size=0,
                        response_time_ms=elapsed_ms,
                        attempts=attempt,
                        tier_used=FetcherTier.HTTPX,
                        fetch_status=FetchStatus.TIMEOUT,
                        error_code=last_error_code,
                        error_message=last_error_msg,
                    )

                except httpx.HTTPError as exc:
                    last_error_code = type(exc).__name__
                    last_error_msg = str(exc) or "HTTP client error"
                    if attempt < max_attempts:
                        await self._sleep_backoff(attempt)
                        continue
                    elapsed_ms = int((time.monotonic() - start_mono) * 1000)
                    return FetchResult(
                        request_url=url,
                        final_url=last_final_url,
                        http_status=last_status,
                        content_type="text/plain",
                        raw_bytes=b"",
                        content_sha256=None,
                        byte_size=0,
                        response_time_ms=elapsed_ms,
                        attempts=attempt,
                        tier_used=FetcherTier.HTTPX,
                        fetch_status=FetchStatus.HTTP_ERROR,
                        error_code=last_error_code,
                        error_message=last_error_msg,
                    )

        elapsed_ms = int((time.monotonic() - start_mono) * 1000)
        return FetchResult(
            request_url=url,
            final_url=last_final_url,
            http_status=last_status,
            content_type="text/plain",
            raw_bytes=b"",
            content_sha256=None,
            byte_size=0,
            response_time_ms=elapsed_ms,
            attempts=max_attempts,
            tier_used=FetcherTier.HTTPX,
            fetch_status=FetchStatus.HTTP_ERROR,
            error_code=last_error_code or "HTTP_FAILED",
            error_message=last_error_msg or "HTTP request failed after retries",
        )

    async def _sleep_backoff(self, attempt: int) -> None:
        if self._base_backoff_seconds <= 0:
            return
        delay = self._base_backoff_seconds * (2 ** (attempt - 1)) + random.uniform(0.2, 0.7)
        await asyncio.sleep(delay)

