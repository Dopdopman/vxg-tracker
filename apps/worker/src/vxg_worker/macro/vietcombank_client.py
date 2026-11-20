"""Vietcombank official USD/VND JSON & XML clients bounded to recent windows."""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from typing import Any

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

logger = logging.getLogger("vxg_worker.macro.vcb")

VCB_JSON_API_URL = "https://www.vietcombank.com.vn/api/exchangerates"
VCB_XML_API_URL = "https://portal.vietcombank.com.vn/Usercontrols/TVPortal.TyGia/pXML.aspx"
MAX_VCB_LOOKBACK_DAYS = 60


def _parse_iso_date_prefix(raw_date: Any, fallback: date) -> date:
    if isinstance(raw_date, str) and len(raw_date.strip()) >= 10:
        try:
            return date.fromisoformat(raw_date.strip()[:10])
        except ValueError:
            return fallback
    return fallback


def parse_vcb_json_rate(
    raw_bytes: bytes,
    *,
    requested_date: date,
    source_url: str,
) -> MacroPoint | None:
    """Parse Vietcombank daily JSON exchange rate response into USD_VND_VCB."""
    payload: dict[str, Any] = json.loads(raw_bytes.decode("utf-8"))
    rows = payload.get("Data")
    if not isinstance(rows, list):
        return None

    usd_entry = next(
        (row for row in rows if str(row.get("currencyCode") or "").strip().upper() == "USD"),
        None,
    )
    if usd_entry is None:
        return None

    sell_dec = to_finite_decimal(usd_entry.get("sell"))
    if sell_dec is None or not is_within_macro_bounds(MacroIndicatorCode.USD_VND_VCB, sell_dec):
        if sell_dec is not None:
            logger.warning("Rejected out-of-bounds VCB JSON sell rate: %s", sell_dec)
        return None

    cash_dec = to_finite_decimal(usd_entry.get("cash"))
    transfer_dec = to_finite_decimal(usd_entry.get("transfer"))
    obs_date = _parse_iso_date_prefix(payload.get("Date"), requested_date)

    return MacroPoint(
        indicator_code=MacroIndicatorCode.USD_VND_VCB,
        indicator_date=obs_date,
        value=sell_dec,
        open_value=None,
        high_value=None,
        low_value=None,
        change_pct_1d=None,
        unit=MACRO_UNITS[MacroIndicatorCode.USD_VND_VCB],
        source_code="VIETCOMBANK_API",
        source_url=source_url,
        content_sha256=compute_content_sha256(raw_bytes),
        metadata_json={
            "vcb_cash": str(cash_dec) if cash_dec is not None else None,
            "vcb_transfer": str(transfer_dec) if transfer_dec is not None else None,
            "vcb_sell": str(sell_dec),
            "updated_date": payload.get("UpdatedDate"),
        },
    )


def parse_vcb_xml_rate(
    raw_bytes: bytes,
    *,
    as_of_date: date | None = None,
    source_url: str = VCB_XML_API_URL,
) -> MacroPoint | None:
    """Parse Vietcombank pXML.aspx fallback XML feed into USD_VND_VCB."""
    root = ET.fromstring(raw_bytes)
    dt_text = (root.findtext("DateTime") or "").strip()
    obs_date = as_of_date or vn_today()
    for fmt in ("%m/%d/%Y %I:%M:%S %p", "%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        if dt_text:
            try:
                obs_date = datetime.strptime(dt_text, fmt).date()
                break
            except ValueError:
                continue

    usd_node = next(
        (
            el
            for el in root.findall("Exrate")
            if (el.attrib.get("CurrencyCode") or "").strip().upper() == "USD"
        ),
        None,
    )
    if usd_node is None:
        return None

    sell_dec = to_finite_decimal(usd_node.attrib.get("Sell"))
    if sell_dec is None or not is_within_macro_bounds(MacroIndicatorCode.USD_VND_VCB, sell_dec):
        if sell_dec is not None:
            logger.warning("Rejected out-of-bounds VCB XML sell rate: %s", sell_dec)
        return None

    cash_dec = to_finite_decimal(usd_node.attrib.get("Buy"))
    transfer_dec = to_finite_decimal(usd_node.attrib.get("Transfer"))

    return MacroPoint(
        indicator_code=MacroIndicatorCode.USD_VND_VCB,
        indicator_date=obs_date,
        value=sell_dec,
        open_value=None,
        high_value=None,
        low_value=None,
        change_pct_1d=None,
        unit=MACRO_UNITS[MacroIndicatorCode.USD_VND_VCB],
        source_code="VIETCOMBANK_XML",
        source_url=source_url,
        content_sha256=compute_content_sha256(raw_bytes),
        metadata_json={
            "vcb_cash": str(cash_dec) if cash_dec is not None else None,
            "vcb_transfer": str(transfer_dec) if transfer_dec is not None else None,
            "vcb_sell": str(sell_dec),
            "xml_datetime": dt_text,
        },
    )


async def fetch_vcb_rates_window(
    client: httpx.AsyncClient,
    *,
    lookback_days: int,
    existing_dates: set[date] | None = None,
    ref_today: date | None = None,
    delay_ms: int = 350,
) -> list[MacroPoint]:
    """Fetch bounded recent USD_VND_VCB window politely, skipping already-stored past dates."""
    today = ref_today or vn_today()
    clamped_days = max(1, min(int(lookback_days), MAX_VCB_LOOKBACK_DAYS))
    existing = existing_dates or set()
    refresh_floor = today - timedelta(days=1)

    collected: list[MacroPoint] = []
    start_date = today - timedelta(days=clamped_days - 1)
    for offset in range(clamped_days):
        target_date = start_date + timedelta(days=offset)
        if target_date in existing and target_date < refresh_floor:
            continue

        url = f"{VCB_JSON_API_URL}?date={target_date.isoformat()}"
        try:
            resp = await fetch_with_polite_retry(
                client,
                url,
                referer="https://www.vietcombank.com.vn/",
                accept_json=True,
                max_attempts=3,
                delay_ms=delay_ms,
            )
            if resp.status_code == 200:
                pt = parse_vcb_json_rate(
                    resp.content,
                    requested_date=target_date,
                    source_url=str(resp.url),
                )
                if pt is not None:
                    collected.append(pt)
                    continue
        except Exception as exc:
            logger.warning("VCB JSON failed for %s: %s", target_date.isoformat(), exc)

    if not any(pt.indicator_date == today for pt in collected):
        try:
            xml_resp = await fetch_with_polite_retry(
                client,
                VCB_XML_API_URL,
                referer="https://portal.vietcombank.com.vn/",
                accept_json=False,
                max_attempts=2,
                delay_ms=delay_ms,
            )
            if xml_resp.status_code == 200:
                pt_xml = parse_vcb_xml_rate(
                    xml_resp.content,
                    as_of_date=today,
                    source_url=str(xml_resp.url),
                )
                if pt_xml is not None:
                    collected.append(pt_xml)
        except Exception as exc:
            logger.warning("VCB XML fallback failed: %s", exc)

    return attach_change_pct_1d(collected)

