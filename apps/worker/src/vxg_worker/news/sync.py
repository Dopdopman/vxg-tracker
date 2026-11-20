"""Market RSS news sync orchestrator with multi-key dedup and PostgreSQL persistence."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from vxg_shared.db.models import MarketNewsSignal
from vxg_shared.enums import NewsImpactLevel
from vxg_shared.timezone import vn_now
from vxg_worker.config import WorkerSettings
from vxg_worker.history.rollup import fetch_with_polite_retry
from vxg_worker.locks import PostgresAdvisoryLock
from vxg_worker.news.gemini_summarizer import enrich_articles_with_gemini
from vxg_worker.news.lexicon import ScoredNewsArticle, score_article
from vxg_worker.news.rss_fetcher import DEFAULT_RSS_FEEDS, RssFeedSpec, parse_rss_feed

logger = logging.getLogger("vxg_worker.news.sync")

DEFAULT_NEWS_LOCK_KEY = 908_712_005

_IMPACT_PRIORITY: dict[NewsImpactLevel, int] = {
    NewsImpactLevel.HIGH: 2,
    NewsImpactLevel.MEDIUM: 1,
    NewsImpactLevel.LOW: 0,
}


@dataclass(slots=True)
class NewsSyncSummary:
    """Telemetry summary produced by a run of sync-news."""

    hours: int
    duration_ms: int
    feeds_ok: int
    feeds_failed: int
    raw_items: int
    relevant_items: int
    inserted_count: int
    gemini_stats: dict[str, int] = field(default_factory=dict)
    by_domain: dict[str, int] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)


def _article_rank(art: ScoredNewsArticle) -> tuple[int, int, str]:
    is_direct = 1 if art.publisher != "Google News" else 0
    return (_IMPACT_PRIORITY[art.impact_level], is_direct, art.published_at.isoformat())


def deduplicate_scored_articles(articles: list[ScoredNewsArticle]) -> list[ScoredNewsArticle]:
    """Deduplicate candidate news articles across canonical URL and normalized headline."""
    by_url: dict[str, ScoredNewsArticle] = {}
    for art in articles:
        existing = by_url.get(art.url_sha256)
        if existing is None or _article_rank(art) > _article_rank(existing):
            by_url[art.url_sha256] = art

    by_title: dict[str, ScoredNewsArticle] = {}
    for art in by_url.values():
        existing = by_title.get(art.title_dedup_key)
        if existing is None or _article_rank(art) > _article_rank(existing):
            by_title[art.title_dedup_key] = art

    return sorted(by_title.values(), key=lambda a: a.published_at, reverse=True)


async def _filter_unseen_articles(
    session: AsyncSession,
    articles: list[ScoredNewsArticle],
) -> list[ScoredNewsArticle]:
    deduped = deduplicate_scored_articles(articles)
    if not deduped:
        return []

    url_hashes = [a.url_sha256 for a in deduped]
    title_hashes = [a.title_dedup_key for a in deduped]
    existing_rows = (
        await session.execute(
            select(MarketNewsSignal.url_sha256, MarketNewsSignal.title_dedup_key).where(
                or_(
                    MarketNewsSignal.url_sha256.in_(url_hashes),
                    MarketNewsSignal.title_dedup_key.in_(title_hashes),
                )
            )
        )
    ).all()

    seen_urls = {r[0] for r in existing_rows}
    seen_titles = {r[1] for r in existing_rows}
    return [
        art
        for art in deduped
        if art.url_sha256 not in seen_urls and art.title_dedup_key not in seen_titles
    ]


async def persist_news_signals(
    session: AsyncSession,
    articles: list[ScoredNewsArticle],
) -> int:
    """Insert new unique news signals idempotently into `market_news_signals`."""
    unseen = await _filter_unseen_articles(session, articles)
    if not unseen:
        return 0

    now_vn = vn_now()
    rows = [
        {
            "domain": art.domain.value,
            "publisher": art.publisher[:80],
            "title": art.title,
            "summary_snippet": art.summary_snippet,
            "url": art.url,
            "url_sha256": art.url_sha256,
            "title_dedup_key": art.title_dedup_key,
            "content_sha256": art.content_sha256,
            "published_at": art.published_at,
            "fetched_at": now_vn,
            "polarity_score": art.polarity_score,
            "impact_level": art.impact_level.value,
            "catalysts": art.catalysts,
            "matched_keywords": art.matched_keywords,
            "llm_summary_vi": art.llm_summary_vi,
            "scoring_engine": art.scoring_engine.value,
        }
        for art in unseen
    ]
    stmt = (
        pg_insert(MarketNewsSignal)
        .values(rows)
        .on_conflict_do_nothing()
        .returning(MarketNewsSignal.id)
    )
    inserted_ids = (await session.execute(stmt)).scalars().all()
    return len(inserted_ids)


async def run_news_sync(
    engine: AsyncEngine,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    settings: WorkerSettings | None = None,
    hours: int = 72,
    feeds: tuple[RssFeedSpec, ...] = DEFAULT_RSS_FEEDS,
    enable_gemini: bool = True,
    advisory_lock_key: int = DEFAULT_NEWS_LOCK_KEY,
    http_client: httpx.AsyncClient | None = None,
    delay_ms: int = 250,
    reference_now_vn: object | None = None,
) -> NewsSyncSummary:
    """Fetch RSS feeds, score deterministically, optionally enrich via Gemini, and persist."""
    from datetime import datetime as _dt

    started = time.monotonic()
    cfg = settings or WorkerSettings()
    ref_now = reference_now_vn if isinstance(reference_now_vn, _dt) else vn_now()
    min_pub_at = ref_now - timedelta(hours=max(1, hours))
    max_pub_at = ref_now + timedelta(minutes=15)

    feeds_ok = 0
    feeds_failed = 0
    raw_items = 0
    scored: list[ScoredNewsArticle] = []
    errors: dict[str, str] = {}
    gemini_stats = {"attempted": 0, "enriched": 0, "fallback": 0}
    inserted_count = 0
    by_domain: dict[str, int] = {}

    async with PostgresAdvisoryLock(engine, advisory_lock_key) as acquired:
        if not acquired:
            raise RuntimeError(
                f"News sync lock {advisory_lock_key} is currently held by another run."
            )

        async def _execute_with_client(client: httpx.AsyncClient) -> None:
            nonlocal feeds_ok, feeds_failed, raw_items, inserted_count, gemini_stats
            for feed in feeds:
                try:
                    resp = await fetch_with_polite_retry(
                        client,
                        feed.url,
                        referer=None,
                        accept_json=False,
                        max_attempts=3,
                        delay_ms=delay_ms,
                    )
                    if resp.status_code != 200:
                        feeds_failed += 1
                        errors[feed.code] = f"HTTP {resp.status_code}"
                        continue
                    parsed = parse_rss_feed(resp.content, feed)
                    feeds_ok += 1
                    raw_items += len(parsed)
                    for item in parsed:
                        if not (min_pub_at <= item.published_at_vn <= max_pub_at):
                            continue
                        scored_item = score_article(item)
                        if scored_item is not None:
                            scored.append(scored_item)
                except Exception as exc:
                    feeds_failed += 1
                    errors[feed.code] = str(exc)
                    logger.warning("RSS feed %s failed: %s", feed.code, exc)

            deduped = deduplicate_scored_articles(scored)
            for art in deduped:
                by_domain[art.domain.value] = by_domain.get(art.domain.value, 0) + 1

            async with session_factory() as session:
                unseen = await _filter_unseen_articles(session, deduped)
                if enable_gemini and cfg.cleaned_gemini_api_key:
                    unseen, gemini_stats = await enrich_articles_with_gemini(
                        client,
                        unseen,
                        api_key=cfg.cleaned_gemini_api_key,
                        model=cfg.gemini_model,
                        max_articles=cfg.gemini_max_articles_per_run,
                    )
                inserted_count = await persist_news_signals(session, unseen)
                await session.commit()

        if http_client is not None:
            await _execute_with_client(http_client)
        else:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(18.0, connect=8.0),
                follow_redirects=True,
            ) as owned_client:
                await _execute_with_client(owned_client)

    duration_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "Completed news sync hours=%d duration_ms=%d feeds_ok=%d feeds_failed=%d "
        "raw=%d relevant=%d inserted=%d gemini=%s",
        hours,
        duration_ms,
        feeds_ok,
        feeds_failed,
        raw_items,
        sum(by_domain.values()),
        inserted_count,
        gemini_stats,
    )
    return NewsSyncSummary(
        hours=hours,
        duration_ms=duration_ms,
        feeds_ok=feeds_ok,
        feeds_failed=feeds_failed,
        raw_items=raw_items,
        relevant_items=sum(by_domain.values()),
        inserted_count=inserted_count,
        gemini_stats=gemini_stats,
        by_domain=by_domain,
        errors=errors,
    )

