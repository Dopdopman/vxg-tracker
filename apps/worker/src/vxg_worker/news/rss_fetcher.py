"""RSS feed ingestion, URL canonicalization, datetime normalization, and HTML cleanup."""

from __future__ import annotations

import email.utils
import html
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from lxml import etree as lxml_etree

from vxg_shared.enums import NewsDomain
from vxg_shared.timezone import VN_TZ, to_vn_tz
from vxg_worker.adapters.common import parse_html

_TRACKING_QUERY_PARAMS: Final[frozenset[str]] = frozenset(
    {
        "fbclid",
        "gclid",
        "dclid",
        "mc_cid",
        "mc_eid",
        "oc",
        "ref",
        "ref_src",
        "ved",
        "usg",
    }
)


@dataclass(frozen=True, slots=True)
class RssFeedSpec:
    """Configuration for a single Vietnamese economic or Google News RSS feed."""

    code: str
    publisher: str
    url: str
    domain_hint: NewsDomain | None = None


@dataclass(frozen=True, slots=True)
class RawRssArticle:
    """Normalized raw RSS article item before domain classification and scoring."""

    feed_code: str
    publisher: str
    title: str
    summary_snippet: str
    url: str
    canonical_url: str
    published_at_vn: datetime
    domain_hint: NewsDomain | None = None


DEFAULT_RSS_FEEDS: tuple[RssFeedSpec, ...] = (
    RssFeedSpec(
        code="VNEXPRESS_KD",
        publisher="VnExpress",
        url="https://vnexpress.net/rss/kinh-doanh.rss",
    ),
    RssFeedSpec(
        code="CAFEF_TT",
        publisher="CafeF",
        url="https://cafef.vn/thi-truong.rss",
    ),
    RssFeedSpec(
        code="CAFEF_VM",
        publisher="CafeF",
        url="https://cafef.vn/vi-mo-dau-tu.rss",
    ),
    RssFeedSpec(
        code="CAFEF_QT",
        publisher="CafeF",
        url="https://cafef.vn/tai-chinh-quoc-te.rss",
    ),
    RssFeedSpec(
        code="TUOITRE_KD",
        publisher="Tuổi Trẻ",
        url="https://tuoitre.vn/rss/kinh-doanh.rss",
    ),
    RssFeedSpec(
        code="THANHNIEN_KT",
        publisher="Thanh Niên",
        url="https://thanhnien.vn/rss/kinh-te.rss",
    ),
    RssFeedSpec(
        code="GNEWS_GOLD",
        publisher="Google News",
        url=(
            "https://news.google.com/rss/search?"
            "q=(%22gi%C3%A1+v%C3%A0ng%22+OR+%22v%C3%A0ng+SJC%22+OR+%22v%C3%A0ng+th%E1%BA%BF+gi%E1%BB%9Bi%22)+when:3d"
            "&hl=vi&gl=VN&ceid=VN:vi"
        ),
        domain_hint=NewsDomain.GOLD,
    ),
    RssFeedSpec(
        code="GNEWS_FUEL",
        publisher="Google News",
        url=(
            "https://news.google.com/rss/search?"
            "q=(%22gi%C3%A1+x%C4%83ng+d%E1%BA%A7u%22+OR+%22d%E1%BA%A7u+Brent%22+OR+OPEC)+when:7d"
            "&hl=vi&gl=VN&ceid=VN:vi"
        ),
        domain_hint=NewsDomain.FUEL,
    ),
    RssFeedSpec(
        code="GNEWS_GAS",
        publisher="Google News",
        url=(
            "https://news.google.com/rss/search?"
            "q=(%22gi%C3%A1+gas%22+OR+%22b%C3%ACnh+gas%22+OR+%22Saudi+Aramco%22)+when:14d"
            "&hl=vi&gl=VN&ceid=VN:vi"
        ),
        domain_hint=NewsDomain.GAS,
    ),
)


def canonicalize_url(raw_url: str) -> str:
    """Normalize article URL and remove common marketing/tracking query parameters."""
    cleaned = raw_url.strip()
    if not cleaned:
        return ""

    parsed = urllib.parse.urlsplit(cleaned)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")

    kept_params: list[tuple[str, str]] = []
    for k, v in urllib.parse.parse_qsl(parsed.query, keep_blank_values=False):
        kl = k.lower()
        if kl.startswith("utm_") or kl in _TRACKING_QUERY_PARAMS:
            continue
        kept_params.append((k, v))
    kept_params.sort()
    query = urllib.parse.urlencode(kept_params)

    return urllib.parse.urlunsplit((scheme, netloc, path, query, ""))


def clean_rss_text(raw: str | None, *, max_len: int | None = None) -> str:
    """Strip HTML tags/entities and collapse whitespace into clean single-line text."""
    if not raw:
        return ""
    unescaped = html.unescape(raw)
    if "<" in unescaped and ">" in unescaped:
        text = parse_html(unescaped.encode("utf-8")).text(separator=" ", strip=True)
        unescaped = html.unescape(text)
    normalized = " ".join(unescaped.split())
    if max_len is not None and len(normalized) > max_len:
        return normalized[: max_len - 1].rstrip() + "…"
    return normalized


def parse_rss_datetime(raw: str | None) -> datetime | None:
    """Parse RFC 2822, CafeF 2-digit year, Tuoi Tre AM/PM, or ISO datetimes in UTC+7."""
    if not raw:
        return None
    cleaned = " ".join(raw.replace("\u202f", " ").replace("\xa0", " ").split()).strip()
    if not cleaned:
        return None

    try:
        dt = email.utils.parsedate_to_datetime(cleaned)
        if dt is not None:
            if dt.tzinfo is None:
                return dt.replace(tzinfo=VN_TZ)
            return to_vn_tz(dt)
    except (TypeError, ValueError):
        pass

    for fmt in (
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %I:%M:%S %p",
    ):
        try:
            naive = datetime.strptime(cleaned, fmt)
            return naive.replace(tzinfo=VN_TZ)
        except ValueError:
            continue

    try:
        iso_dt = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
        if iso_dt.tzinfo is None:
            return iso_dt.replace(tzinfo=VN_TZ)
        return to_vn_tz(iso_dt)
    except ValueError:
        return None


def _strip_google_news_publisher_suffix(title: str, publisher: str) -> str:
    suffix = f" - {publisher}"
    if publisher and title.endswith(suffix) and len(title) > len(suffix) + 6:
        return title[: -len(suffix)].strip()
    return title


def parse_rss_feed(raw_bytes: bytes, feed: RssFeedSpec) -> list[RawRssArticle]:
    """Parse RSS 2.0 XML bytes safely with tolerant XML recovery fallback."""
    if not raw_bytes.strip():
        return []

    try:
        root = ET.fromstring(raw_bytes)
        items = list(root.findall(".//item"))
    except ET.ParseError:
        recovered = lxml_etree.fromstring(
            raw_bytes,
            parser=lxml_etree.XMLParser(recover=True, resolve_entities=False),
        )
        items = list(recovered.findall(".//item"))  # type: ignore[assignment]

    parsed_articles: list[RawRssArticle] = []
    for item in items:
        raw_title = item.findtext("title")
        raw_link = item.findtext("link")
        raw_desc = item.findtext("description")
        raw_pub = item.findtext("pubDate")
        source_node = item.find("source")
        source_text = (source_node.text or "").strip() if source_node is not None else ""

        title = clean_rss_text(raw_title)
        link = (raw_link or "").strip()
        canonical = canonicalize_url(link)
        pub_dt = parse_rss_datetime(raw_pub)
        if not title or not canonical or pub_dt is None:
            continue

        publisher = clean_rss_text(source_text) or feed.publisher
        if feed.code.startswith("GNEWS_") and source_text:
            title = _strip_google_news_publisher_suffix(title, publisher)

        snippet = clean_rss_text(raw_desc, max_len=400)
        parsed_articles.append(
            RawRssArticle(
                feed_code=feed.code,
                publisher=publisher[:80],
                title=title,
                summary_snippet=snippet,
                url=canonical,
                canonical_url=canonical,
                published_at_vn=pub_dt,
                domain_hint=feed.domain_hint,
            )
        )
    return parsed_articles
