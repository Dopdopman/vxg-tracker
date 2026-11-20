"""Two-tier market news RSS ingestion, deterministic lexicon, and optional Gemini summarizer."""

from vxg_worker.news.gemini_summarizer import enrich_articles_with_gemini
from vxg_worker.news.lexicon import (
    ScoredNewsArticle,
    build_dedup_keys,
    classify_domain,
    normalize_dedup_title,
    score_article,
)
from vxg_worker.news.rss_fetcher import (
    DEFAULT_RSS_FEEDS,
    RawRssArticle,
    RssFeedSpec,
    canonicalize_url,
    clean_rss_text,
    parse_rss_datetime,
    parse_rss_feed,
)
from vxg_worker.news.sync import (
    DEFAULT_NEWS_LOCK_KEY,
    NewsSyncSummary,
    deduplicate_scored_articles,
    persist_news_signals,
    run_news_sync,
)

__all__ = [
    "DEFAULT_NEWS_LOCK_KEY",
    "DEFAULT_RSS_FEEDS",
    "NewsSyncSummary",
    "RawRssArticle",
    "RssFeedSpec",
    "ScoredNewsArticle",
    "build_dedup_keys",
    "canonicalize_url",
    "classify_domain",
    "clean_rss_text",
    "deduplicate_scored_articles",
    "enrich_articles_with_gemini",
    "normalize_dedup_title",
    "parse_rss_datetime",
    "parse_rss_feed",
    "persist_news_signals",
    "run_news_sync",
    "score_article",
]

