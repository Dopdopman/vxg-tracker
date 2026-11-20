"""Yahoo Finance Chart v8 parser and rate-limited fetcher (GC=F, BZ=F, CL=F, DXY, VND=X)."""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from vxg_shared.enums import MacroIndicatorCode
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import vn_today
from vxg_worker.history.rollup import fetch_with_polite_retry
from vxg_worker.macro.types import (
    MACRO_UNITS,
    MacroPoint,
    attach_change_pct_1d,
    is_within_macro_bounds,
    to_finite_decimal,
)

logger = logging.getLogger("vxg_worker.macro.yahoo")

YAHOO_TICKER_MAP: dict[MacroIndicatorCode, str] = {
    MacroIndicatorCode.XAU_USD: "GC=F",
    MacroIndicatorCode.BRENT_USD: "BZ=F",
    MacroIndicatorCode.WTI_USD: "CL=F",
    MacroIndicatorCode.DXY: "DX-Y.NYB",
    MacroIndicatorCode.USD_VND_MARKET: "VND=X",
}

YAHOO_CHART_HOSTS: tuple[str, ...] = (
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
)


def _resolve_exchange_tz(tz_name: Any) -> tuple[ZoneInfo, str]:
    raw_name = str(tz_name or "UTC").strip() or "UTC"
    try:
        return ZoneInfo(raw_name), raw_name
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC"), "UTC"


def _valid_optional_ohlc(raw_val: Any, code: MacroIndicatorCode) -> Decimal | None:
    dec = to_finite_decimal(raw_val)
    if dec is None or not is_within_macro_bounds(code, dec):
        return None
    return dec


def parse_yahoo_chart_v8(
    raw_bytes: bytes,
    *,
    indicator_code: MacroIndicatorCode,
    source_url: str,
    max_date: date | None = None,
) -> list[MacroPoint]:
    """Parse Yahoo Finance v8 chart JSON into bounded daily MacroPoint records."""
    if indicator_code not in YAHOO_TICKER_MAP:
        raise ValueError(f"Unsupported Yahoo indicator code: {indicator_code}")

    payload: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
    chart = payload.get("chart")
    if not isinstance(chart, dict):
        raise ValueError("Yahoo Chart v8 payload missing 'chart' object")
    if chart.get("error"):
        raise ValueError(f"Yahoo Chart v8 API error: {chart.get('error')}")

    results = chart.get("result")
    if not isinstance(results, list) or not results:
        raise ValueError("Yahoo Chart v8 payload missing 'chart.result'")

    series_obj = results[0]
    meta = series_obj.get("meta") or {}
    timestamps = series_obj.get("timestamp") or []
    quotes = ((series_obj.get("indicators") or {}).get("quote") or [{}])[0]

    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []

    exchange_tz, tz_name = _resolve_exchange_tz(meta.get("exchangeTimezoneName"))
    upper_date = max_date or vn_today()
    sha256 = compute_content_sha256(raw_bytes)
    unit = MACRO_UNITS[indicator_code]
    symbol = str(meta.get("symbol") or YAHOO_TICKER_MAP[indicator_code])

    by_date: dict[date, MacroPoint] = {}
    for idx, ts_val in enumerate(timestamps):
        if ts_val is None or idx >= len(closes):
            continue
        close_dec = to_finite_decimal(closes[idx])
        if close_dec is None:
            continue
        if not is_within_macro_bounds(indicator_code, close_dec):
            logger.warning(
                "Rejected out-of-bounds Yahoo close for %s (%s): %s",
                indicator_code.value,
                symbol,
                close_dec,
            )
            continue

        obs_date = datetime.fromtimestamp(int(ts_val), tz=exchange_tz).date()
        if obs_date > upper_date:
            continue

        open_dec = _valid_optional_ohlc(opens[idx] if idx < len(opens) else None, indicator_code)
        high_dec = _valid_optional_ohlc(highs[idx] if idx < len(highs) else None, indicator_code)
        low_dec = _valid_optional_ohlc(lows[idx] if idx < len(lows) else None, indicator_code)

        by_date[obs_date] = MacroPoint(
            indicator_code=indicator_code,
            indicator_date=obs_date,
            value=close_dec,
            open_value=open_dec,
            high_value=high_dec,
            low_value=low_dec,
            change_pct_1d=None,
            unit=unit,
            source_code="YAHOO_FINANCE_V8",
            source_url=source_url,
            content_sha256=sha256,
            metadata_json={
                "symbol": symbol,
                "exchange_tz": tz_name,
                "currency": meta.get("currency"),
            },
        )

    # Active sessions often emit close=None for the latest day while regularMarketPrice is valid.
    reg_price = to_finite_decimal(meta.get("regularMarketPrice"))
    reg_time = meta.get("regularMarketTime")
    if reg_price is not None and reg_time is not None and is_within_macro_bounds(indicator_code, reg_price):
        reg_date = datetime.fromtimestamp(int(reg_time), tz=exchange_tz).date()
        if reg_date <= upper_date and reg_date not in by_date:
            by_date[reg_date] = MacroPoint(
                indicator_code=indicator_code,
                indicator_date=reg_date,
                value=reg_price,
                open_value=None,
                high_value=None,
                low_value=None,
                change_pct_1d=None,
                unit=unit,
                source_code="YAHOO_FINANCE_V8",
                source_url=source_url,
                content_sha256=sha256,
                metadata_json={
                    "symbol": symbol,
                    "exchange_tz": tz_name,
                    "currency": meta.get("currency"),
                    "intraday_regular_market": True,
                    "regular_market_time_utc": datetime.fromtimestamp(int(reg_time), tz=UTC).isoformat(),
                },
            )

    return attach_change_pct_1d(list(by_date.values()))


async def fetch_yahoo_indicator(
    client: httpx.AsyncClient,
    indicator_code: MacroIndicatorCode,
    *,
    range_str: str = "2y",
    delay_ms: int = 550,
    max_date: date | None = None,
) -> list[MacroPoint]:
    """Fetch one Yahoo Chart v8 series using browser headers, exponential backoff, and host fallback."""
    symbol = YAHOO_TICKER_MAP[indicator_code]
    encoded_symbol = urllib.parse.quote(symbol, safe="")
    last_error: Exception | None = None

    for host in YAHOO_CHART_HOSTS:
        url = f"{host}/v8/finance/chart/{encoded_symbol}?range={range_str}&interval=1d"
        try:
            resp = await fetch_with_polite_retry(
                client,
                url,
                referer="https://finance.yahoo.com/",
                accept_json=True,
                max_attempts=4,
                delay_ms=delay_ms,
            )
            if resp.status_code == 200:
                return parse_yahoo_chart_v8(
                    resp.content,
                    indicator_code=indicator_code,
                    source_url=str(resp.url),
                    max_date=max_date,
                )
            last_error = RuntimeError(f"Yahoo HTTP {resp.status_code} for {symbol} at {host}")
            logger.warning("%s", last_error)
        except Exception as exc:
            last_error = exc
            logger.warning("Yahoo host %s failed for %s: %s", host, symbol, exc)

    assert last_error is not None
    raise last_error

