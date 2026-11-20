"""Bounded news risk overlay: widens uncertainty bands & flags volatility without inventing prices."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from vxg_shared.enums import CommodityCategory, NewsImpactLevel, PredictedDirection
from vxg_worker.forecasting.features import slice_news_as_of
from vxg_worker.forecasting.metrics import round_price_tick
from vxg_worker.forecasting.types import NewsObs

HIGH_IMPACT_CATALYSTS: frozenset[str] = frozenset(
    {
        "FED_RATE",
        "OPEC_SUPPLY",
        "NHNN_GOLD_POLICY",
        "CP_ARAMCO",
        "GEOPOLITICAL",
        "MOIT_PRICE_CYCLE",
    }
)

_IMPACT_ORDER: dict[str, int] = {
    NewsImpactLevel.HIGH.value: 2,
    NewsImpactLevel.MEDIUM.value: 1,
    NewsImpactLevel.LOW.value: 0,
}


@dataclass(frozen=True, slots=True)
class NewsOverlayResult:
    """Risk-adjusted prediction interval, confidence score, and serialized top news metadata."""

    lower_bound_vnd: int
    upper_bound_vnd: int
    confidence_score: Decimal
    high_volatility_flag: bool
    band_widening_pct: float
    cp_aramco_signal: float
    top_news_payload: list[dict[str, Any]]


def apply_news_risk_overlay(
    *,
    domain: CommodityCategory,
    point_estimate_vnd: int,
    lower_bound_vnd: int,
    upper_bound_vnd: int,
    predicted_direction: PredictedDirection,
    base_confidence: Decimal,
    news_items: list[NewsObs],
    cutoff_dt_vn: datetime,
) -> NewsOverlayResult:
    """Strictly bound interval expansion (<=25%) and attach top news references <= cutoff_dt_vn."""
    lookback_hours = 336 if domain == CommodityCategory.GAS else 72
    window_news = slice_news_as_of(news_items, cutoff_dt_vn, lookback_hours=lookback_hours)

    relevant = [
        item
        for item in window_news
        if item.domain == domain.value or (domain == CommodityCategory.GOLD and item.domain == "MACRO")
    ]
    relevant.sort(
        key=lambda n: (
            _IMPACT_ORDER.get(n.impact_level, 0),
            1 if any(c in HIGH_IMPACT_CATALYSTS for c in n.catalysts) else 0,
            abs(float(n.polarity_score)),
            n.published_at_vn,
        ),
        reverse=True,
    )

    top_news = relevant[:5]
    high_catalyst_hits = [
        n
        for n in window_news
        if n.domain == domain.value
        and (
            n.impact_level == NewsImpactLevel.HIGH.value
            or any(c in HIGH_IMPACT_CATALYSTS for c in n.catalysts)
        )
    ]

    if len(high_catalyst_hits) >= 2:
        widening = 0.22
    elif len(high_catalyst_hits) == 1:
        widening = 0.12
    else:
        widening = 0.0

    widening = min(0.25, widening)
    high_vol = widening > 0.0

    adj_lower = lower_bound_vnd
    adj_upper = upper_bound_vnd
    step = 100_000 if domain == CommodityCategory.GOLD else (10 if domain == CommodityCategory.FUEL else 1_000)

    if widening > 0.0:
        down_dist = max(step, point_estimate_vnd - lower_bound_vnd)
        up_dist = max(step, upper_bound_vnd - point_estimate_vnd)
        adj_lower = round_price_tick(point_estimate_vnd - down_dist * (1.0 + widening), domain)
        adj_upper = round_price_tick(point_estimate_vnd + up_dist * (1.0 + widening), domain)
        adj_lower = min(adj_lower, point_estimate_vnd)
        adj_upper = max(adj_upper, point_estimate_vnd)
        if adj_lower == lower_bound_vnd:
            adj_lower = max(step, lower_bound_vnd - step)
        if adj_upper == upper_bound_vnd:
            adj_upper = upper_bound_vnd + step

    domain_only = [n for n in relevant if n.domain == domain.value]
    if domain_only:
        avg_polarity = sum(float(n.polarity_score) for n in domain_only[:8]) / min(len(domain_only), 8)
    else:
        avg_polarity = 0.0

    conf_val = float(base_confidence)
    if high_vol:
        conf_val -= 0.04
    if (predicted_direction == PredictedDirection.UP and avg_polarity <= -0.35) or (
        predicted_direction == PredictedDirection.DOWN and avg_polarity >= 0.35
    ):
        conf_val -= 0.08
    adj_confidence = Decimal(str(round(max(0.15, min(0.90, conf_val)), 3))).quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )

    cp_signals = [
        float(n.polarity_score)
        for n in window_news
        if n.domain == CommodityCategory.GAS.value and "CP_ARAMCO" in n.catalysts
    ]
    cp_aramco_signal = sum(cp_signals) / len(cp_signals) if cp_signals else 0.0
    cp_aramco_signal = max(-1.0, min(1.0, cp_aramco_signal))

    top_payload = [
        {
            "id": n.id,
            "domain": n.domain,
            "publisher": n.publisher,
            "title": n.title,
            "url": n.url,
            "published_at_vn": n.published_at_vn.isoformat(),
            "impact_level": n.impact_level,
            "polarity_score": float(n.polarity_score),
            "catalysts": n.catalysts,
            "summary": n.summary,
        }
        for n in top_news
    ]

    return NewsOverlayResult(
        lower_bound_vnd=adj_lower,
        upper_bound_vnd=adj_upper,
        confidence_score=adj_confidence,
        high_volatility_flag=high_vol,
        band_widening_pct=round(widening * 100.0, 1),
        cp_aramco_signal=round(cp_aramco_signal, 3),
        top_news_payload=top_payload,
    )

