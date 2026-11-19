"""Milestone 6 unit tests for production API key hardening and AlertNotifier."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from vxg_api.config import ApiSettings
from vxg_api.main import create_app
from vxg_shared.enums import CrawlRunStatus, FetchStatus
from vxg_worker.config import WorkerSettings
from vxg_worker.notifier import AlertNotifier
from vxg_worker.runner import CrawlRunSummary, SourceLogSummary


@pytest.mark.parametrize(
    "weak_key",
    [
        "",
        "   ",
        "change-me-local-admin-key",
        "change-me-long-enough-key-over-24",
        "short-key-under-24",
        "admin",
    ],
)
def test_production_app_refuses_weak_or_default_admin_key(weak_key: str) -> None:
    settings = ApiSettings.model_validate(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": weak_key,
        }
    )
    assert settings.is_production is True
    assert settings.is_admin_key_strong() is False
    with pytest.raises(RuntimeError, match="Refusing to start vxg-api in production"):
        create_app(settings)


def test_production_app_accepts_strong_admin_key_and_allows_dev_default() -> None:
    prod_settings = ApiSettings.model_validate(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": "vxg_prod_9f8e7d6c5b4a3210fedcba987654",
        }
    )
    assert prod_settings.is_admin_key_strong() is True
    app = create_app(prod_settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    dev_settings = ApiSettings.model_validate(
        {
            "APP_ENV": "development",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": "change-me-local-admin-key",
        }
    )
    assert dev_settings.is_production is False
    dev_app = create_app(dev_settings)
    assert dev_app.title == "vxg-tracker API"


def test_worker_settings_alert_and_retention_aliases() -> None:
    ws = WorkerSettings.model_validate(
        {
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ALERT_TELEGRAM_BOT_TOKEN": " 12345:tg-token ",
            "ALERT_TELEGRAM_CHAT_ID": " -100999 ",
            "ALERT_DISCORD_WEBHOOK_URL": " https://discord.com/api/webhooks/1/abc ",
            "SNAPSHOT_RETENTION_DAYS": 45,
        }
    )
    assert ws.alert_telegram_bot_token == "12345:tg-token"
    assert ws.alert_telegram_chat_id == "-100999"
    assert ws.alert_discord_webhook_url == "https://discord.com/api/webhooks/1/abc"
    assert ws.snapshot_retention_days == 45


def _make_summary(
    *,
    status: CrawlRunStatus = CrawlRunStatus.COMPLETED,
    flagged: int = 0,
    source_logs: list[SourceLogSummary] | None = None,
) -> CrawlRunSummary:
    return CrawlRunSummary(
        run_id=uuid.uuid4(),
        run_code="RUN-20261119-140000-ALL-ABCD",
        scope="ALL",
        trigger_type="SCHEDULED",
        status=status.value,
        sources_queried_count=11,
        pages_fetched_success=10 if status != CrawlRunStatus.FAILED else 0,
        pages_fetched_failed=1 if status != CrawlRunStatus.COMPLETED else 0,
        documents_parsed_count=10,
        records_extracted_count=40,
        records_verified_count=39,
        records_flagged_count=flagged,
        records_unchanged_count=0,
        duration_ms=4200,
        source_logs=source_logs or [],
    )


@pytest.mark.asyncio
async def test_alert_notifier_noop_unconfigured_and_clean_runs() -> None:
    notifier = AlertNotifier()
    assert notifier.is_configured is False

    clean_res = await notifier.notify_crawl_run(_make_summary(status=CrawlRunStatus.COMPLETED))
    assert clean_res.triggered is False
    assert clean_res.skipped_unconfigured is False

    failed_res = await notifier.notify_crawl_run(_make_summary(status=CrawlRunStatus.FAILED))
    assert failed_res.triggered is True
    assert failed_res.skipped_unconfigured is True
    assert failed_res.message is not None
    assert "RUN-20261119-140000-ALL-ABCD" in failed_res.message


@pytest.mark.asyncio
async def test_alert_notifier_sends_telegram_and_discord_with_resilience() -> None:
    captured_requests: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append((str(request.url), body))
        if "api.telegram.org" in str(request.url):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(204)

    summary = _make_summary(
        status=CrawlRunStatus.PARTIAL_SUCCESS,
        flagged=2,
        source_logs=[
            SourceLogSummary(
                source_code="PVOIL_FUEL",
                request_url="https://www.pvoil.com.vn/tin-gia-xang-dau",
                tier_used="PLAYWRIGHT",
                fetch_status=FetchStatus.BLOCKED.value,
                http_status=403,
                response_time_ms=800,
                byte_size=0,
                content_sha256=None,
                fallback_triggered=True,
                documents_parsed=0,
                records_extracted=0,
                records_verified=0,
                records_flagged=0,
                error_message="Cloudflare WAF 403",
            )
        ],
    )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        notifier = AlertNotifier(
            telegram_bot_token="bot-token-123",
            telegram_chat_id="-100123456",
            discord_webhook_url="https://discord.com/api/webhooks/test/secret",
            http_client=client,
        )
        res = await notifier.notify_crawl_run(
            summary,
            anomaly_messages=["GOLD_SJC_BAR_SJC/HCM: Historical spike +25% exceeded threshold"],
        )

    assert res.triggered is True
    assert res.skipped_unconfigured is False
    assert res.telegram_sent is True
    assert res.discord_sent is True
    assert res.errors == []
    assert len(captured_requests) == 2

    tg_url, tg_payload = captured_requests[0]
    assert tg_url == "https://api.telegram.org/botbot-token-123/sendMessage"
    assert tg_payload["chat_id"] == "-100123456"
    assert "PVOIL_FUEL" in str(tg_payload["text"])
    assert "UTC+7" in str(tg_payload["text"])

    dc_url, dc_payload = captured_requests[1]
    assert dc_url == "https://discord.com/api/webhooks/test/secret"
    assert "Historical spike +25%" in str(dc_payload["content"])

    # Partial failure: Telegram HTTP 500 should NOT block Discord or raise
    def partial_fail_handler(request: httpx.Request) -> httpx.Response:
        if "api.telegram.org" in str(request.url):
            return httpx.Response(500, text="Upstream error")
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(partial_fail_handler)) as client2:
        notifier2 = AlertNotifier(
            telegram_bot_token="bot-token-123",
            telegram_chat_id="-100123456",
            discord_webhook_url="https://discord.com/api/webhooks/test/secret",
            http_client=client2,
        )
        res2 = await notifier2.notify_crawl_run(summary)

    assert res2.triggered is True
    assert res2.telegram_sent is False
    assert res2.discord_sent is True
    assert len(res2.errors) == 1

