"""Tier 2 Optional Gemini REST API summarizer with strict JSON and safe Tier 1 fallback."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from typing import Any

import httpx

from vxg_shared.enums import NewsImpactLevel, NewsScoringEngine
from vxg_worker.news.lexicon import ScoredNewsArticle

logger = logging.getLogger("vxg_worker.news.gemini")

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

_IMPACT_RANK: dict[NewsImpactLevel, int] = {
    NewsImpactLevel.HIGH: 2,
    NewsImpactLevel.MEDIUM: 1,
    NewsImpactLevel.LOW: 0,
}


def _extract_gemini_summary(payload: dict[str, Any]) -> str | None:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    parts = ((candidates[0].get("content") or {}).get("parts")) or []
    if not isinstance(parts, list) or not parts:
        return None
    raw_text = str(parts[0].get("text") or "").strip()
    if not raw_text:
        return None
    parsed = json.loads(raw_text)
    if not isinstance(parsed, dict):
        return None
    summary = " ".join(str(parsed.get("summary_vi") or "").split())
    if not summary:
        return None
    return summary[:280]


async def enrich_articles_with_gemini(
    client: httpx.AsyncClient,
    articles: list[ScoredNewsArticle],
    *,
    api_key: str | None,
    model: str = "gemini-2.0-flash",
    max_articles: int = 6,
    timeout_seconds: float = 12.0,
) -> tuple[list[ScoredNewsArticle], dict[str, int]]:
    """Optionally add 1-sentence Vietnamese summaries via Gemini; never fails on missing key/429."""
    stats = {"attempted": 0, "enriched": 0, "fallback": 0}
    cleaned_key = (api_key or "").strip()
    if not articles or not cleaned_key or max_articles <= 0:
        return list(articles), stats

    candidate_indices = [
        idx
        for idx, art in enumerate(articles)
        if art.impact_level in (NewsImpactLevel.HIGH, NewsImpactLevel.MEDIUM)
    ]
    candidate_indices.sort(
        key=lambda idx: (_IMPACT_RANK[articles[idx].impact_level], articles[idx].published_at),
        reverse=True,
    )
    selected_indices = candidate_indices[:max_articles]

    updated = list(articles)
    endpoint = f"{GEMINI_API_BASE_URL}/{model}:generateContent"
    headers = {
        "x-goog-api-key": cleaned_key,
        "Content-Type": "application/json",
    }

    for idx in selected_indices:
        art = updated[idx]
        stats["attempted"] += 1
        prompt = (
            "Hãy tóm tắt đúng 1 câu ngắn gọn bằng tiếng Việt (tối đa 200 ký tự, không khuyên đầu tư) "
            f"nêu tác động chính của bản tin tới thị trường {art.domain.value} Việt Nam.\n"
            f"Nguồn: {art.publisher}\n"
            f"Tiêu đề: {art.title}\n"
            f"Tóm tắt: {art.summary_snippet or ''}\n"
            f"Catalysts: {', '.join(art.catalysts) or 'NONE'}"
        )
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {"summary_vi": {"type": "STRING"}},
                    "required": ["summary_vi"],
                },
                "temperature": 0.1,
                "maxOutputTokens": 180,
            },
        }
        try:
            resp = await client.post(endpoint, headers=headers, json=body, timeout=timeout_seconds)
            if resp.status_code == 429:
                logger.warning("Gemini API quota exhausted (HTTP 429); falling back to Tier 1 lexicon")
                stats["fallback"] += 1
                break
            if resp.status_code != 200:
                logger.warning("Gemini API HTTP %s; falling back cleanly to Tier 1", resp.status_code)
                stats["fallback"] += 1
                continue

            summary_vi = _extract_gemini_summary(resp.json())
            if not summary_vi:
                logger.warning("Gemini API returned invalid/empty JSON summary; keeping Tier 1")
                stats["fallback"] += 1
                continue

            updated[idx] = replace(
                art,
                llm_summary_vi=summary_vi,
                scoring_engine=NewsScoringEngine.LEXICON_PLUS_GEMINI,
            )
            stats["enriched"] += 1
        except (httpx.TimeoutException, httpx.TransportError, ValueError) as exc:
            logger.warning("Gemini enrichment skipped (%s); falling back to Tier 1", exc)
            stats["fallback"] += 1

    return updated, stats

