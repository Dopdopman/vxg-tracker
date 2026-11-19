"""Base adapter contract, schema drift error, and Milestone 2 smoke passthrough adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from lxml import etree
from selectolax.lexbor import LexborHTMLParser

from vxg_shared.db.models import Source
from vxg_worker.fetcher.headers import detect_soft_404
from vxg_worker.fetcher.types import FetchResult
from vxg_worker.validation.guardrails import PriceCandidate


class SchemaDriftError(RuntimeError):
    """Raised when a fetched source payload violates structural canary expectations."""


def assert_not_soft_404(fetch_result: FetchResult, source: Source) -> None:
    """Guard against HTTP 200 Soft-404 pages returning 'Không tìm thấy trang'."""
    if not fetch_result.raw_bytes or not fetch_result.raw_bytes.strip():
        raise SchemaDriftError(f"Empty payload returned by {source.code}")
    soft_404 = detect_soft_404(fetch_result.raw_bytes, fetch_result.content_type)
    if soft_404 is not None:
        raise SchemaDriftError(f"Soft-404 detected for {source.code}: {soft_404}")


@dataclass(slots=True)
class AdapterParseResult:
    """Standard output emitted by a source adapter parse pass."""

    documents_parsed: int
    candidates: list[PriceCandidate] = field(default_factory=list)
    parser_metadata: dict[str, Any] = field(default_factory=dict)


class BaseSourceAdapter(ABC):
    """Contract implemented by all Milestone 3 commodity adapters and test adapters."""

    parser_key: str

    def extract_detail_url(self, listing_result: FetchResult, source: Source) -> str | None:
        """Optional step 1 for 2-step sources: return detail notice/article URL to fetch next."""
        return None

    @abstractmethod
    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        """Parse fetched bytes into normalized PriceCandidates without touching DB."""


class SmokeFetchPassthroughAdapter(BaseSourceAdapter):
    """Milestone 2 smoke adapter verifying live connectivity & document readability only.

    Produces zero PriceCandidate entries so no mock/unparsed prices enter the database.
    """

    parser_key = "smoke.passthrough"

    def parse(self, fetch_result: FetchResult, source: Source) -> AdapterParseResult:
        assert_not_soft_404(fetch_result, source)

        metadata: dict[str, Any] = {
            "smoke_mode": True,
            "fallback_triggered": fetch_result.fallback_triggered,
        }
        if "tier1_attempt" in fetch_result.metadata:
            metadata["tier1_attempt"] = fetch_result.metadata["tier1_attempt"]

        ct = (fetch_result.content_type or "").lower()
        stripped = fetch_result.raw_bytes.lstrip()
        if "xml" in ct or stripped.startswith(b"<?xml"):
            try:
                parser = etree.XMLParser(resolve_entities=False, no_network=True, recover=False)
                root = etree.fromstring(fetch_result.raw_bytes, parser=parser)
                metadata["xml_root"] = str(root.tag)
            except etree.XMLSyntaxError as exc:
                raise SchemaDriftError(f"Invalid XML from {source.code}: {exc}") from exc
        else:
            tree = LexborHTMLParser(fetch_result.raw_bytes.decode("utf-8", errors="replace"))
            title_node = tree.css_first("title")
            if title_node is not None:
                metadata["document_title"] = title_node.text(strip=True)[:200]

        return AdapterParseResult(
            documents_parsed=1,
            candidates=[],
            parser_metadata=metadata,
        )


class AdapterRegistry:
    """Registry mapping source.parser_key strings to adapter instances."""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseSourceAdapter] = {}
        self._smoke_adapter = SmokeFetchPassthroughAdapter()

    def register(self, adapter: BaseSourceAdapter) -> None:
        self._adapters[adapter.parser_key] = adapter

    def resolve(self, parser_key: str, *, smoke_only: bool = False) -> BaseSourceAdapter:
        if smoke_only:
            return self._smoke_adapter
        adapter = self._adapters.get(parser_key)
        if adapter is None:
            return self._smoke_adapter
        return adapter
