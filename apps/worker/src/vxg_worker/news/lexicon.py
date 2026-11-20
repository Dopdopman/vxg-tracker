"""Tier 1 Deterministic Lexicon & Event Catalyst Engine (0-cost, offline testable)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from vxg_shared.enums import NewsDomain, NewsImpactLevel, NewsScoringEngine
from vxg_shared.hashing import compute_content_sha256
from vxg_shared.timezone import to_vn_tz
from vxg_worker.news.rss_fetcher import RawRssArticle

_TITLE_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)


@dataclass(frozen=True, slots=True)
class ScoredNewsArticle:
    """Structured market news signal scored by Lexicon v1 and optionally enriched by Gemini."""

    domain: NewsDomain
    publisher: str
    title: str
    summary_snippet: str | None
    url: str
    url_sha256: str
    title_dedup_key: str
    content_sha256: str
    published_at: datetime
    polarity_score: Decimal
    impact_level: NewsImpactLevel
    catalysts: list[str]
    matched_keywords: list[str]
    llm_summary_vi: str | None
    scoring_engine: NewsScoringEngine


_DOMAIN_ENTITY_KEYWORDS: Final[dict[NewsDomain, tuple[str, ...]]] = {
    NewsDomain.GOLD: (
        "giá vàng",
        "vàng miếng",
        "vàng sjc",
        "vàng nhẫn",
        "vàng thế giới",
        "kim loại quý",
        "vàng 9999",
        "xau",
        "doji",
        "pnj",
        "đấu thầu vàng",
        "bình ổn vàng",
    ),
    NewsDomain.FUEL: (
        "giá xăng",
        "giá dầu",
        "xăng dầu",
        "xăng ron 95",
        "ron 95",
        "e5 ron 92",
        "dầu diesel",
        "dầu hỏa",
        "dầu thô",
        "dầu brent",
        "dầu wti",
        "opec",
        "điều hành giá xăng",
        "kỳ điều hành",
    ),
    NewsDomain.GAS: (
        "giá gas",
        "bình gas",
        "gas petrolimex",
        "saigon petro",
        "pv gas",
        "saudi aramco",
        "giá cp",
        "lpg",
        "khí hóa lỏng",
        "12kg",
    ),
    NewsDomain.MACRO: (
        "cục dự trữ liên bang",
        "fed ",
        " lãi suất fed",
        "lãi suất mỹ",
        "chỉ số dxy",
        "chỉ số usd",
        "đồng bạc xanh",
        "tỷ giá usd",
        "tỷ giá trung tâm",
        "usd/vnd",
        "ngân hàng nhà nước",
        "lạm phát mỹ",
    ),
}


@dataclass(frozen=True, slots=True)
class _CatalystRule:
    code: str
    phrases: tuple[str, ...]
    default_polarity: float
    domain_polarity: dict[NewsDomain, float]
    allowed_domains: frozenset[NewsDomain] | None = None


_ALL_DOMAINS = frozenset(NewsDomain)

_CATALYST_RULES: Final[tuple[_CatalystRule, ...]] = (
    # Gold & Monetary Policy
    _CatalystRule(
        code="FED_RATE",
        phrases=(
            "fed cắt giảm lãi suất",
            "fed hạ lãi suất",
            "kỳ vọng fed giảm lãi suất",
            "kỳ vọng hạ lãi suất",
            "fed nới lỏng",
            "dovish",
        ),
        default_polarity=0.55,
        domain_polarity={NewsDomain.GOLD: 0.60, NewsDomain.MACRO: -0.45},
        allowed_domains=frozenset({NewsDomain.GOLD, NewsDomain.MACRO, NewsDomain.FUEL}),
    ),
    _CatalystRule(
        code="FED_RATE",
        phrases=(
            "fed giữ nguyên lãi suất",
            "fed trì hoãn hạ lãi suất",
            "fed tăng lãi suất",
            "fed diều hâu",
            "hawkish",
            "lãi suất cao kỷ lục",
        ),
        default_polarity=-0.55,
        domain_polarity={NewsDomain.GOLD: -0.60, NewsDomain.MACRO: 0.45},
        allowed_domains=frozenset({NewsDomain.GOLD, NewsDomain.MACRO, NewsDomain.FUEL}),
    ),
    _CatalystRule(
        code="NHNN_GOLD_POLICY",
        phrases=(
            "bán vàng bình ổn",
            "đấu thầu vàng",
            "nhnn tăng cung vàng",
            "thanh tra thị trường vàng",
            "thu hẹp chênh lệch giá vàng",
            "xóa độc quyền vàng miếng",
        ),
        default_polarity=-0.55,
        domain_polarity={NewsDomain.GOLD: -0.55},
        allowed_domains=frozenset({NewsDomain.GOLD}),
    ),
    _CatalystRule(
        code="NHNN_GOLD_POLICY",
        phrases=(
            "khan hiếm nguồn cung vàng",
            "nhu cầu vàng vật chất tăng vọt",
            "giới hạn bán vàng",
        ),
        default_polarity=0.35,
        domain_polarity={NewsDomain.GOLD: 0.35},
        allowed_domains=frozenset({NewsDomain.GOLD}),
    ),
    _CatalystRule(
        code="WORLD_GOLD_MOVE",
        phrases=(
            "vàng thế giới tăng mạnh",
            "vàng thế giới bật tăng",
            "vàng thế giới lập đỉnh",
            "vàng thế giới vượt mốc",
            "xau tăng vọt",
            "giá vàng tăng vọt",
        ),
        default_polarity=0.50,
        domain_polarity={NewsDomain.GOLD: 0.55},
        allowed_domains=frozenset({NewsDomain.GOLD}),
    ),
    _CatalystRule(
        code="WORLD_GOLD_MOVE",
        phrases=(
            "vàng thế giới lao dốc",
            "vàng thế giới giảm sâu",
            "vàng thế giới giảm mạnh",
            "vàng thế giới bốc hơi",
            "bán tháo vàng",
            "rơi tự do",
        ),
        default_polarity=-0.50,
        domain_polarity={NewsDomain.GOLD: -0.55},
        allowed_domains=frozenset({NewsDomain.GOLD}),
    ),
    # Fuel & OPEC & MOIT Cycles
    _CatalystRule(
        code="OPEC_SUPPLY",
        phrases=(
            "opec+ cắt giảm sản lượng",
            "opec cắt giảm",
            "siết nguồn cung dầu",
            "giảm sản lượng khai thác",
            "gia hạn cắt giảm sản lượng",
        ),
        default_polarity=0.65,
        domain_polarity={NewsDomain.FUEL: 0.70, NewsDomain.GAS: 0.45},
        allowed_domains=frozenset({NewsDomain.FUEL, NewsDomain.GAS, NewsDomain.MACRO}),
    ),
    _CatalystRule(
        code="OPEC_SUPPLY",
        phrases=(
            "opec+ tăng sản lượng",
            "opec tăng sản lượng",
            "nới lỏng nguồn cung dầu",
            "dư cung dầu thô",
            "nguồn cung dầu dồi dào",
        ),
        default_polarity=-0.60,
        domain_polarity={NewsDomain.FUEL: -0.65, NewsDomain.GAS: -0.40},
        allowed_domains=frozenset({NewsDomain.FUEL, NewsDomain.GAS, NewsDomain.MACRO}),
    ),
    _CatalystRule(
        code="MOIT_PRICE_CYCLE",
        phrases=(
            "giá xăng dầu đồng loạt tăng",
            "dự báo giá xăng tăng",
            "giá xăng có thể tăng",
            "điều chỉnh tăng giá xăng",
            "xăng ron 95 tăng",
        ),
        default_polarity=0.65,
        domain_polarity={NewsDomain.FUEL: 0.65},
        allowed_domains=frozenset({NewsDomain.FUEL}),
    ),
    _CatalystRule(
        code="MOIT_PRICE_CYCLE",
        phrases=(
            "giá xăng dầu đồng loạt giảm",
            "đồng loạt giảm",
            "dự báo giá xăng giảm",
            "giá xăng có thể giảm",
            "điều chỉnh giảm giá xăng",
            "xăng ron 95 giảm",
        ),
        default_polarity=-0.65,
        domain_polarity={NewsDomain.FUEL: -0.65},
        allowed_domains=frozenset({NewsDomain.FUEL}),
    ),
    _CatalystRule(
        code="US_INVENTORY",
        phrases=(
            "tồn kho dầu mỹ giảm",
            "tồn kho dầu thô giảm mạnh",
            "dự trữ dầu mỹ sụt giảm",
        ),
        default_polarity=0.40,
        domain_polarity={NewsDomain.FUEL: 0.45},
        allowed_domains=frozenset({NewsDomain.FUEL}),
    ),
    _CatalystRule(
        code="US_INVENTORY",
        phrases=(
            "tồn kho dầu mỹ tăng",
            "tồn kho dầu thô tăng mạnh",
            "dự trữ dầu mỹ tăng vọt",
        ),
        default_polarity=-0.40,
        domain_polarity={NewsDomain.FUEL: -0.45},
        allowed_domains=frozenset({NewsDomain.FUEL}),
    ),
    # Gas & Saudi Aramco CP
    _CatalystRule(
        code="CP_ARAMCO",
        phrases=(
            "giá cp thế giới tăng",
            "saudi aramco tăng",
            "chốt giá cp tăng",
            "giá gas tăng mạnh",
            "giá gas trong nước tăng",
            "giá gas đầu tháng tăng",
            "bình gas 12kg tăng",
        ),
        default_polarity=0.80,
        domain_polarity={NewsDomain.GAS: 0.80},
        allowed_domains=frozenset({NewsDomain.GAS}),
    ),
    _CatalystRule(
        code="CP_ARAMCO",
        phrases=(
            "giá cp thế giới giảm",
            "saudi aramco giảm",
            "chốt giá cp giảm",
            "giá gas giảm mạnh",
            "giá gas trong nước giảm",
            "giá gas đầu tháng giảm",
            "bình gas 12kg giảm",
        ),
        default_polarity=-0.80,
        domain_polarity={NewsDomain.GAS: -0.80},
        allowed_domains=frozenset({NewsDomain.GAS}),
    ),
    _CatalystRule(
        code="LPG_SEASONAL_DEMAND",
        phrases=(
            "nhu cầu sưởi ấm mùa đông",
            "nhu cầu lpg tăng cao",
            "khan hiếm nguồn cung gas",
        ),
        default_polarity=0.45,
        domain_polarity={NewsDomain.GAS: 0.50},
        allowed_domains=frozenset({NewsDomain.GAS}),
    ),
    # Cross-cutting Geopolitics & USD/FX
    _CatalystRule(
        code="GEOPOLITICAL",
        phrases=(
            "xung đột trung đông",
            "căng thẳng trung đông",
            "căng thẳng địa chính trị",
            "eo biển hormuz",
            "biển đỏ",
            "tấn công tàu dầu",
            "trừng phạt dầu",
        ),
        default_polarity=0.55,
        domain_polarity={NewsDomain.GOLD: 0.55, NewsDomain.FUEL: 0.65, NewsDomain.GAS: 0.50},
        allowed_domains=_ALL_DOMAINS,
    ),
    _CatalystRule(
        code="USD_FX",
        phrases=(
            "đồng usd tăng mạnh",
            "dxy tăng vọt",
            "đồng bạc xanh mạnh lên",
            "tỷ giá usd tăng nóng",
            "usd vượt đỉnh",
        ),
        default_polarity=0.35,
        domain_polarity={
            NewsDomain.GOLD: -0.45,
            NewsDomain.FUEL: 0.30,
            NewsDomain.GAS: 0.30,
            NewsDomain.MACRO: 0.40,
        },
        allowed_domains=_ALL_DOMAINS,
    ),
    _CatalystRule(
        code="USD_FX",
        phrases=(
            "đồng usd suy yếu",
            "usd lao dốc",
            "dxy giảm sâu",
            "đồng bạc xanh hạ nhiệt",
            "tỷ giá hạ nhiệt",
        ),
        default_polarity=-0.35,
        domain_polarity={
            NewsDomain.GOLD: 0.45,
            NewsDomain.FUEL: -0.25,
            NewsDomain.GAS: -0.25,
            NewsDomain.MACRO: -0.40,
        },
        allowed_domains=_ALL_DOMAINS,
    ),
)

_HIGH_PRIORITY_CATALYSTS: Final[frozenset[str]] = frozenset(
    {"CP_ARAMCO", "OPEC_SUPPLY", "NHNN_GOLD_POLICY", "FED_RATE", "GEOPOLITICAL", "MOIT_PRICE_CYCLE"}
)

_MILD_POSITIVE_CUES: Final[tuple[str, ...]] = (
    "tăng mạnh",
    "bật tăng",
    "lập đỉnh",
    "vượt mốc",
    "tăng vọt",
    "tăng nhẹ",
    "đi lên",
    "nhích tăng",
)

_MILD_NEGATIVE_CUES: Final[tuple[str, ...]] = (
    "giảm mạnh",
    "giảm sâu",
    "lao dốc",
    "rơi tự do",
    "bốc hơi",
    "giảm nhẹ",
    "hạ nhiệt",
    "đi xuống",
    "suy giảm",
)


def normalize_dedup_title(title: str) -> str:
    """Normalize headline for cross-feed duplicate detection."""
    folded = unicodedata.normalize("NFC", title).lower()
    stripped = _TITLE_PUNCT_RE.sub(" ", folded)
    return " ".join(stripped.split())


def build_dedup_keys(
    domain: NewsDomain,
    title: str,
    summary_snippet: str | None,
    canonical_url: str,
    published_at_vn: datetime,
) -> tuple[str, str, str]:
    """Build deterministic (url_sha256, title_dedup_key, content_sha256) hex digests."""
    vn_date = to_vn_tz(published_at_vn).date().isoformat()
    url_sha256 = compute_content_sha256(canonical_url)
    title_dedup_key = compute_content_sha256(
        f"{domain.value}|{vn_date}|{normalize_dedup_title(title)}"
    )
    content_sha256 = compute_content_sha256(
        f"{title}|{summary_snippet or ''}|{canonical_url}"
    )
    return url_sha256, title_dedup_key, content_sha256


def classify_domain(
    title: str,
    summary_snippet: str,
    *,
    domain_hint: NewsDomain | None = None,
) -> tuple[NewsDomain | None, list[str]]:
    """Classify article into GOLD/FUEL/GAS/MACRO, rejecting irrelevant general news."""
    norm_title = f" {unicodedata.normalize('NFC', title).lower()} "
    norm_snippet = f" {unicodedata.normalize('NFC', summary_snippet).lower()} "

    scores: dict[NewsDomain, float] = {}
    hits_by_domain: dict[NewsDomain, list[str]] = {}
    has_title_hit: dict[NewsDomain, bool] = {}

    for domain, keywords in _DOMAIN_ENTITY_KEYWORDS.items():
        score = 0.0
        matched: list[str] = []
        title_hit = False
        for kw in keywords:
            in_title = kw in norm_title
            in_snippet = kw in norm_snippet
            if in_title:
                score += 2.0
                title_hit = True
            elif in_snippet:
                score += 1.0
            if in_title or in_snippet:
                matched.append(kw.strip())
        if matched:
            scores[domain] = score
            hits_by_domain[domain] = matched
            has_title_hit[domain] = title_hit

    if not scores:
        return None, []

    if domain_hint is not None and domain_hint in scores:
        scores[domain_hint] += 0.5

    best_domain = max(scores, key=lambda d: scores[d])
    best_score = scores[best_domain]
    if best_score < 2.0 and not has_title_hit.get(best_domain, False):
        return None, []

    return best_domain, sorted(set(hits_by_domain[best_domain]))


def score_article(raw: RawRssArticle) -> ScoredNewsArticle | None:
    """Apply deterministic Tier 1 relevance filter, catalysts, polarity, and impact score."""
    domain, entity_hits = classify_domain(
        raw.title,
        raw.summary_snippet,
        domain_hint=raw.domain_hint,
    )
    if domain is None:
        return None

    combined_text = unicodedata.normalize(
        "NFC",
        f"{raw.title} {raw.summary_snippet}",
    ).lower()

    total_polarity = Decimal("0")
    catalysts: list[str] = []
    matched_keywords: list[str] = list(entity_hits)

    for rule in _CATALYST_RULES:
        if rule.allowed_domains is not None and domain not in rule.allowed_domains:
            continue
        matched_phrase = next((p for p in rule.phrases if p in combined_text), None)
        if matched_phrase is None:
            continue
        catalysts.append(rule.code)
        matched_keywords.append(matched_phrase)
        weight = rule.domain_polarity.get(domain, rule.default_polarity)
        total_polarity += Decimal(str(weight))

    if not catalysts:
        pos_hits = [cue for cue in _MILD_POSITIVE_CUES if cue in combined_text]
        neg_hits = [cue for cue in _MILD_NEGATIVE_CUES if cue in combined_text]
        if pos_hits and not neg_hits:
            total_polarity += Decimal("0.28")
            matched_keywords.extend(pos_hits[:2])
        elif neg_hits and not pos_hits:
            total_polarity -= Decimal("0.28")
            matched_keywords.extend(neg_hits[:2])
        elif pos_hits and neg_hits:
            matched_keywords.extend(pos_hits[:1] + neg_hits[:1])

    clamped = max(Decimal("-1.000"), min(Decimal("1.000"), total_polarity)).quantize(
        Decimal("0.001"),
        rounding=ROUND_HALF_UP,
    )

    unique_catalysts = sorted(set(catalysts))
    abs_score = abs(clamped)
    if (
        abs_score >= Decimal("0.550")
        or len(unique_catalysts) >= 2
        or (any(c in _HIGH_PRIORITY_CATALYSTS for c in unique_catalysts) and abs_score >= Decimal("0.450"))
    ):
        impact = NewsImpactLevel.HIGH
    elif abs_score >= Decimal("0.250") or len(unique_catalysts) >= 1:
        impact = NewsImpactLevel.MEDIUM
    else:
        impact = NewsImpactLevel.LOW

    url_sha256, title_dedup_key, content_sha256 = build_dedup_keys(
        domain,
        raw.title,
        raw.summary_snippet,
        raw.canonical_url,
        raw.published_at_vn,
    )

    return ScoredNewsArticle(
        domain=domain,
        publisher=raw.publisher,
        title=raw.title,
        summary_snippet=raw.summary_snippet or None,
        url=raw.canonical_url,
        url_sha256=url_sha256,
        title_dedup_key=title_dedup_key,
        content_sha256=content_sha256,
        published_at=to_vn_tz(raw.published_at_vn),
        polarity_score=clamped,
        impact_level=impact,
        catalysts=unique_catalysts,
        matched_keywords=sorted(set(matched_keywords))[:12],
        llm_summary_vi=None,
        scoring_engine=NewsScoringEngine.LEXICON_V1,
    )

