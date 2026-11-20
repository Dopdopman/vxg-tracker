"""Vietnamese market calendars locked to Asia/Ho_Chi_Minh (UTC+7)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from vxg_shared.enums import ForecastHorizon
from vxg_shared.timezone import to_vn_tz


def next_gold_target_date(as_of: date, horizon: ForecastHorizon) -> date:
    """Return calendar target date for Gold T+1 or T+7 forecasts."""
    if horizon == ForecastHorizon.T_PLUS_1:
        return as_of + timedelta(days=1)
    if horizon == ForecastHorizon.T_PLUS_7:
        return as_of + timedelta(days=7)
    raise ValueError(f"Unsupported Gold horizon: {horizon}")


def next_fuel_adjustment_date(as_of_dt_vn: datetime) -> date:
    """Return the next MOIT Thursday 15:00 Asia/Ho_Chi_Minh fuel price adjustment date."""
    vn_dt = to_vn_tz(as_of_dt_vn)
    weekday = vn_dt.weekday()  # Monday=0 .. Thursday=3 .. Sunday=6
    if weekday == 3:
        if vn_dt.hour < 15:
            return vn_dt.date()
        return vn_dt.date() + timedelta(days=7)
    days_ahead = (3 - weekday) % 7
    if days_ahead == 0:
        days_ahead = 7
    return vn_dt.date() + timedelta(days=days_ahead)


def next_gas_month_date(as_of: date) -> date:
    """Return the 1st calendar day of the next month (Vietnamese Gas adjustment date)."""
    if as_of.month == 12:
        return date(as_of.year + 1, 1, 1)
    return date(as_of.year, as_of.month + 1, 1)

