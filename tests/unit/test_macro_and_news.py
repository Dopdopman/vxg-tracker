"""Offline unit tests for Milestone 8 macro fetchers, gold parity, RSS news & Gemini fallback."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from vxg_shared.enums import MacroIndicatorCode, NewsDomain, NewsImpactLevel, NewsScoringEngine
from vxg_shared.gold_parity import (
    GOLD_PARITY_DISCLAIMER_VI,
    compute_sjc_world_gold_spread,
    select_fx_rate_for_date,
)
from vxg_shared.timezone import VN_TZ, vn_now
from vxg_worker.macro.vietcombank_client import (
    MAX_VCB_LOOKBACK_DAYS,
    fetch_vcb_rates_window,
    parse_vcb_json_rate,
    parse_vcb_xml_rate,
)
from vxg_worker.macro.yahoo_client import parse_yahoo_chart_v8
from vxg_worker.news.gemini_summarizer import enrich_articles_with_gemini
from vxg_worker.news.lexicon import (
    ScoredNewsArticle,
    classify_domain,
    score_article,
)
from vxg_worker.news.rss_fetcher import (
    RssFeedSpec,
    canonicalize_url,
    parse_rss_datetime,
    parse_rss_feed,
)
from vxg_worker.news.sync import deduplicate_scored_articles

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"


def test_yahoo_chart_v8_parser_timezone_null_close_and_bounds() -> None:
    raw = (FIXTURES_DIR / "macro/yahoo_xau_chart_v8.json").read_bytes()
    pts = parse_yahoo_chart_v8(
        raw,
        indicator_code=MacroIndicatorCode.XAU_USD,
        source_url="https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?range=5d&interval=1d",
        max_date=date(2026, 11, 20),
    )

    # 5 timestamps: 18th (999999.0 out of bounds) rejected; 20th null close filled by regularMarketPrice
    assert [p.indicator_date for p in pts] == [
        date(2026, 11, 16),
        date(2026, 11, 17),
        date(2026, 11, 19),
        date(2026, 11, 20),
    ]
    assert pts[0].value == Decimal("3853.0000")
    assert pts[0].change_pct_1d is None
    assert pts[1].value == Decimal("3864.2000")
    assert pts[1].change_pct_1d == Decimal("0.2907")
    assert pts[-1].value == Decimal("3910.4000")
    assert pts[-1].metadata_json["intraday_regular_market"] is True
    assert pts[-1].content_sha256 is not None and len(pts[-1].content_sha256) == 64


@pytest.mark.asyncio
async def test_vietcombank_json_xml_parsers_and_lookback_cap() -> None:
    vcb_json_bytes = (FIXTURES_DIR / "macro/vcb_exchangerates.json").read_bytes()
    pt_json = parse_vcb_json_rate(
        vcb_json_bytes,
        requested_date=date(2026, 11, 20),
        source_url="https://www.vietcombank.com.vn/api/exchangerates?date=2026-11-20",
    )
    assert pt_json is not None
    assert pt_json.indicator_date == date(2026, 11, 20)
    assert pt_json.value == Decimal("25960.0000")
    assert pt_json.source_code == "VIETCOMBANK_API"
    assert pt_json.metadata_json["vcb_cash"] == "25550.0000"

    vcb_xml_bytes = (FIXTURES_DIR / "macro/vcb_exchangerates.xml").read_bytes()
    pt_xml = parse_vcb_xml_rate(vcb_xml_bytes, as_of_date=date(2026, 11, 20))
    assert pt_xml is not None
    assert pt_xml.indicator_date == date(2026, 11, 20)
    assert pt_xml.value == Decimal("25960.0000")
    assert pt_xml.source_code == "VIETCOMBANK_XML"

    # Reject out-of-bounds rate
    bad_json = json.dumps(
        {"Date": "2026-11-20", "Data": [{"currencyCode": "USD", "sell": "99999.00"}]}
    ).encode("utf-8")
    assert (
        parse_vcb_json_rate(bad_json, requested_date=date(2026, 11, 20), source_url="https://x")
        is None
    )

    # Hard cap <= 60 days and XML fallback when today's JSON fails
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        u = str(request.url)
        calls.append(u)
        if "pXML.aspx" in u:
            return httpx.Response(200, content=vcb_xml_bytes)
        req_date = request.url.params.get("date", "")
        if req_date == "2026-11-20":
            return httpx.Response(404)
        return httpx.Response(
            200,
            json={
                "Date": f"{req_date}T00:00:00",
                "Data": [
                    {
                        "currencyCode": "USD",
                        "cash": "25540.00",
                        "transfer": "25570.00",
                        "sell": "25950.00",
                    }
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        pts = await fetch_vcb_rates_window(
            client,
            lookback_days=500,
            existing_dates=set(),
            ref_today=date(2026, 11, 20),
            delay_ms=0,
        )

    json_calls = [u for u in calls if "api/exchangerates" in u]
    xml_calls = [u for u in calls if "pXML.aspx" in u]
    assert len(set(json_calls)) == MAX_VCB_LOOKBACK_DAYS
    assert len(xml_calls) == 1
    assert any(p.indicator_date == date(2026, 11, 20) and p.source_code == "VIETCOMBANK_XML" for p in pts)


def test_gold_parity_formula_and_fx_priority() -> None:
    spread = compute_sjc_world_gold_spread(
        xau_usd=Decimal("2650"),
        usd_vnd=Decimal("25500"),
        sjc_sell_vnd=85_500_000,
        fx_source="USD_VND_VCB",
        xau_date=date(2026, 11, 20),
        fx_date=date(2026, 11, 20),
        sjc_date=date(2026, 11, 20),
    )
    assert spread.world_vnd_per_tael == 81_472_001
    assert spread.spread_vnd == 4_027_999
    assert spread.spread_pct == 4.94
    assert spread.disclaimer_vi == GOLD_PARITY_DISCLAIMER_VI
    assert "37,5g" in spread.disclaimer_vi and "phí gia công" in spread.disclaimer_vi

    vcb_map = {date(2026, 11, 18): Decimal("25960")}
    mkt_map = {
        date(2026, 11, 18): Decimal("25600"),
        date(2026, 11, 19): Decimal("25620"),
    }
    hit_vcb = select_fx_rate_for_date(date(2026, 11, 18), vcb_by_date=vcb_map, market_by_date=mkt_map)
    assert hit_vcb == (Decimal("25960"), date(2026, 11, 18), "USD_VND_VCB")

    hit_mkt = select_fx_rate_for_date(date(2026, 11, 19), vcb_by_date=vcb_map, market_by_date=mkt_map)
    assert hit_mkt == (Decimal("25620"), date(2026, 11, 19), "USD_VND_MARKET")


def test_rss_parsers_canonical_url_timezone_and_dedup_collision() -> None:
    assert (
        canonicalize_url("https://www.VnExpress.net/abc-123.html/?utm_source=rss&fbclid=xyz&page=2#top")
        == "https://vnexpress.net/abc-123.html?page=2"
    )
    dt_tt = parse_rss_datetime("11/20/2026 10:57:00\u202fAM")
    assert dt_tt == datetime(2026, 11, 20, 10, 57, tzinfo=VN_TZ)

    dt_cafef = parse_rss_datetime("Fri, 20 Nov 26 11:42:00 +0700")
    assert dt_cafef == datetime(2026, 11, 20, 11, 42, tzinfo=VN_TZ)

    vne_feed = RssFeedSpec("VNEXPRESS_KD", "VnExpress", "https://vnexpress.net/rss/kinh-doanh.rss")
    tt_feed = RssFeedSpec("TUOITRE_KD", "Tuổi Trẻ", "https://tuoitre.vn/rss/kinh-doanh.rss")
    gn_feed = RssFeedSpec(
        "GNEWS_GOLD",
        "Google News",
        "https://news.google.com/rss/search?q=gold",
        domain_hint=NewsDomain.GOLD,
    )

    vne_raw = parse_rss_feed((FIXTURES_DIR / "news/vnexpress_sample.rss").read_bytes(), vne_feed)
    tt_raw = parse_rss_feed((FIXTURES_DIR / "news/tuoitre_thanhnien_sample.rss").read_bytes(), tt_feed)
    gn_raw = parse_rss_feed((FIXTURES_DIR / "news/gnews_gold_sample.rss").read_bytes(), gn_feed)

    assert len(vne_raw) == 4
    assert len(tt_raw) == 2
    assert tt_raw[1].title.startswith("Giá xăng RON 95")
    assert len(gn_raw) == 2
    # Google News publisher & suffix extracted cleanly to collide on title_dedup_key
    assert gn_raw[0].publisher == "VnExpress"
    assert gn_raw[0].title == vne_raw[0].title

    scored = [s for item in (vne_raw + tt_raw + gn_raw) if (s := score_article(item)) is not None]
    # Irrelevant Long Thanh airport article dropped; 1 duplicate Google News item collapsed
    titles = [s.title for s in deduplicate_scored_articles(scored)]
    assert all("Long Thành" not in t for t in titles)
    assert len(titles) == 6
    assert sum(1 for t in titles if t == vne_raw[0].title) == 1


def test_deterministic_lexicon_catalysts_and_bounds() -> None:
    noise_dom, _ = classify_domain(
        "Thị trường bất động sản TP.HCM đón nguồn cung mới",
        "Nhiều dự án chung cư mở bán trong quý IV.",
    )
    assert noise_dom is None

    extreme_raw = parse_rss_feed(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
  <title>Giá vàng SJC tăng vọt: Fed cắt giảm lãi suất, vàng thế giới lập đỉnh giữa căng thẳng Trung Đông</title>
  <description>Đồng USD suy yếu, nguồn cung khan hiếm và nhu cầu vàng vật chất tăng vọt.</description>
  <link>https://example.test/extreme-gold.html</link>
  <pubDate>{vn_now().strftime('%a, %d %b %Y %H:%M:%S +0700')}</pubDate>
</item></channel></rss>""".encode(),
        RssFeedSpec("EX", "Test", "https://example.test/rss"),
    )[0]

    s1 = score_article(extreme_raw)
    s2 = score_article(extreme_raw)
    assert s1 is not None and s2 is not None
    assert s1 == s2
    assert s1.domain == NewsDomain.GOLD
    assert s1.polarity_score == Decimal("1.000")
    assert s1.impact_level == NewsImpactLevel.HIGH
    assert s1.scoring_engine == NewsScoringEngine.LEXICON_V1
    assert {"FED_RATE", "WORLD_GOLD_MOVE", "GEOPOLITICAL"}.issubset(set(s1.catalysts))


@pytest.mark.asyncio
async def test_gemini_summarizer_tiers_and_graceful_fallback() -> None:
    sample = ScoredNewsArticle(
        domain=NewsDomain.GAS,
        publisher="VnExpress",
        title="Saudi Aramco chốt giá CP giảm mạnh",
        summary_snippet="Giá gas đầu tháng giảm theo giá CP thế giới.",
        url="https://vnexpress.net/gas-cp-giam.html",
        url_sha256="a" * 64,
        title_dedup_key="b" * 64,
        content_sha256="c" * 64,
        published_at=datetime.now(tz=UTC).astimezone(VN_TZ) - timedelta(hours=1),
        polarity_score=Decimal("-0.800"),
        impact_level=NewsImpactLevel.HIGH,
        catalysts=["CP_ARAMCO"],
        matched_keywords=["saudi aramco", "giá cp thế giới giảm"],
        llm_summary_vi=None,
        scoring_engine=NewsScoringEngine.LEXICON_V1,
    )

    called = 0

    def ok_handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called += 1
        assert request.headers.get("x-goog-api-key") == "secret-gemini-key"
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "summary_vi": "Giá CP Saudi Aramco giảm tạo dư địa hạ giá bán lẻ gas đầu tháng."
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)) as client:
        no_key_res, no_key_stats = await enrich_articles_with_gemini(
            client, [sample], api_key=None
        )
        assert called == 0
        assert no_key_stats == {"attempted": 0, "enriched": 0, "fallback": 0}
        assert no_key_res[0].scoring_engine == NewsScoringEngine.LEXICON_V1

        ok_res, ok_stats = await enrich_articles_with_gemini(
            client, [sample], api_key="secret-gemini-key"
        )
        assert called == 1
        assert ok_stats == {"attempted": 1, "enriched": 1, "fallback": 0}
        assert ok_res[0].scoring_engine == NewsScoringEngine.LEXICON_PLUS_GEMINI
        assert ok_res[0].llm_summary_vi is not None and "Saudi Aramco" in ok_res[0].llm_summary_vi

    def rate_limit_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"status": "RESOURCE_EXHAUSTED"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(rate_limit_handler)) as rl_client:
        rl_res, rl_stats = await enrich_articles_with_gemini(
            rl_client, [sample, sample], api_key="secret-gemini-key", max_articles=5
        )
        # Stops politely on 429 after 1st attempt and preserves Tier 1 scores cleanly
        assert rl_stats == {"attempted": 1, "enriched": 0, "fallback": 1}
        assert all(a.scoring_engine == NewsScoringEngine.LEXICON_V1 for a in rl_res)
        assert all(a.llm_summary_vi is None for a in rl_res)

    def broken_json_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "NOT_JSON"}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(broken_json_handler)) as bj_client:
        bj_res, bj_stats = await enrich_articles_with_gemini(
            bj_client, [sample], api_key="secret-gemini-key"
        )
        assert bj_stats == {"attempted": 1, "enriched": 0, "fallback": 1}
        assert bj_res[0].scoring_engine == NewsScoringEngine.LEXICON_V1
