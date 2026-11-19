"""Milestone 6 unit tests for production API key hardening and AlertNotifier."""

from __future__ import annotations

import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from vxg_api.config import ApiSettings, WorkerDispatchMode
from vxg_api.main import create_app
from vxg_api.services.admin_service import WorkflowDispatchError, dispatch_github_workflow
from vxg_shared.db.engine import create_async_db_engine, create_sync_db_engine
from vxg_shared.enums import CrawlRunStatus, CrawlScope, FetchStatus
from vxg_shared.settings import (
    SharedSettings,
    normalize_async_database_url,
    normalize_sync_database_url,
)
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


def test_neon_database_url_normalization_and_engine_resilience() -> None:
    neon_pooler_url = (
        "postgresql://neondb_owner:secret123@ep-cool-sea-123.ap-southeast-1.aws.neon.tech"
        "/neondb?sslmode=require&channel_binding=require"
    )
    neon_direct_url = (
        "postgres://neondb_owner:secret123@ep-cool-sea-123.ap-southeast-1.aws.neon.tech"
        "/neondb?sslmode=verify-full&channel_binding=require"
    )

    async_url = normalize_async_database_url(neon_pooler_url)
    assert async_url.startswith("postgresql+asyncpg://neondb_owner:secret123@")
    assert "channel_binding" not in async_url
    assert "sslmode" not in async_url
    assert "ssl=require" in async_url

    sync_url = normalize_sync_database_url(neon_pooler_url)
    assert sync_url.startswith("postgresql+psycopg://neondb_owner:secret123@")
    assert "sslmode=require" in sync_url
    assert "channel_binding=require" in sync_url

    local_url = "postgresql://vxg:vxg_dev_password@localhost:5432/vxg_tracker"
    assert normalize_async_database_url(local_url) == (
        "postgresql+asyncpg://vxg:vxg_dev_password@localhost:5432/vxg_tracker"
    )

    s_direct = SharedSettings.model_validate(
        {
            "DATABASE_URL": neon_pooler_url,
            "DATABASE_URL_SYNC": None,
            "DATABASE_URL_DIRECT": neon_direct_url,
        }
    )
    assert s_direct.async_database_url == async_url
    assert s_direct.sync_database_url.startswith("postgresql+psycopg://")
    assert "sslmode=verify-full" in s_direct.sync_database_url

    s_fallback = SharedSettings.model_validate(
        {
            "DATABASE_URL": neon_pooler_url,
            "DATABASE_URL_SYNC": None,
            "DATABASE_URL_DIRECT": "   ",
        }
    )
    assert "sslmode=require" in s_fallback.sync_database_url

    async_eng = create_async_db_engine(neon_pooler_url)
    sync_eng = create_sync_db_engine(neon_direct_url)
    try:
        assert async_eng.pool._pre_ping is True
        assert async_eng.pool._recycle == 300
        assert sync_eng.pool._pre_ping is True
        assert sync_eng.pool._recycle == 300
    finally:
        sync_eng.dispose()


def test_github_actions_dispatch_mode_production_validation() -> None:
    strong_key = "vxg_prod_9f8e7d6c5b4a3210fedcba987654"
    bad_mode = ApiSettings.model_validate(
        {
            "APP_ENV": "development",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "WORKER_DISPATCH_MODE": "redis_queue",
        }
    )
    with pytest.raises(ValueError, match="Invalid WORKER_DISPATCH_MODE"):
        bad_mode.validate_production_security()

    missing_gh = ApiSettings.model_validate(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": strong_key,
            "WORKER_DISPATCH_MODE": "github_actions",
            "GITHUB_REPO": "",
            "GITHUB_DISPATCH_TOKEN": "",
        }
    )
    with pytest.raises(RuntimeError, match="GITHUB_REPO"):
        missing_gh.validate_production_security()

    valid_gh = ApiSettings.model_validate(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": strong_key,
            "WORKER_DISPATCH_MODE": "github_actions",
            "GITHUB_REPO": "acme/vxg-tracker",
            "GITHUB_DISPATCH_TOKEN": "github_pat_test_token_value",
        }
    )
    assert valid_gh.dispatch_mode == WorkerDispatchMode.GITHUB_ACTIONS
    valid_gh.validate_production_security()


@pytest.mark.asyncio
async def test_dispatch_github_workflow_success_and_failures() -> None:
    settings = ApiSettings.model_validate(
        {
            "APP_ENV": "production",
            "DATABASE_URL": "postgresql+asyncpg://vxg:vxg@localhost:5432/vxg_tracker",
            "DATABASE_URL_SYNC": None,
            "ADMIN_API_KEY": "vxg_prod_9f8e7d6c5b4a3210fedcba987654",
            "WORKER_DISPATCH_MODE": "github_actions",
            "GITHUB_REPO": "acme/vxg-tracker",
            "GITHUB_WORKFLOW_FILE": "crawl-schedule.yml",
            "GITHUB_WORKFLOW_REF": "main",
            "GITHUB_DISPATCH_TOKEN": "ghp_mock_dispatch_secret",
        }
    )
    run_id = uuid.uuid4()
    seen: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def ok_handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                str(request.url),
                dict(request.headers),
                json.loads(request.content.decode("utf-8")),
            )
        )
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(ok_handler)) as client:
        await dispatch_github_workflow(
            settings=settings,
            run_id=run_id,
            scope=CrawlScope.GOLD,
            http_client=client,
        )

    assert len(seen) == 1
    url, headers, payload = seen[0]
    assert (
        url
        == "https://api.github.com/repos/acme/vxg-tracker/actions/workflows/crawl-schedule.yml/dispatches"
    )
    assert headers["authorization"] == "Bearer ghp_mock_dispatch_secret"
    assert payload == {
        "ref": "main",
        "inputs": {"scope": "GOLD", "run_id": str(run_id)},
    }

    def err_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Resource not accessible by personal access token")

    async with httpx.AsyncClient(transport=httpx.MockTransport(err_handler)) as client_err:
        with pytest.raises(WorkflowDispatchError) as exc_info:
            await dispatch_github_workflow(
                settings=settings,
                run_id=run_id,
                scope=CrawlScope.ALL,
                http_client=client_err,
            )
        assert exc_info.value.upstream_status == 403

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client_to:
        with pytest.raises(WorkflowDispatchError) as to_info:
            await dispatch_github_workflow(
                settings=settings,
                run_id=run_id,
                scope=CrawlScope.ALL,
                http_client=client_to,
            )
        assert to_info.value.upstream_status is None


@pytest.mark.asyncio
async def test_worker_cli_parses_run_id_and_empty_string(monkeypatch: pytest.MonkeyPatch) -> None:
    import vxg_worker.cli as cli_mod

    seen_calls: list[dict[str, object]] = []

    class _DummyEngine:
        async def dispose(self) -> None:
            return None

    class _FakeRunner:
        def __init__(self, **_kwargs: object) -> None:
            return None

        async def execute_run(
            self,
            *,
            scope: CrawlScope,
            trigger_type: object,
            existing_run_id: uuid.UUID | None = None,
            smoke_only: bool = False,
        ) -> CrawlRunSummary:
            seen_calls.append(
                {
                    "scope": scope,
                    "trigger_type": trigger_type,
                    "existing_run_id": existing_run_id,
                    "smoke_only": smoke_only,
                }
            )
            return _make_summary(status=CrawlRunStatus.COMPLETED)

    monkeypatch.setattr(cli_mod, "create_async_db_engine", lambda _url: _DummyEngine())
    monkeypatch.setattr(cli_mod, "create_session_factory", lambda _eng: object())
    monkeypatch.setattr(cli_mod, "CrawlRunner", _FakeRunner)

    target_id = uuid.uuid4()
    rc1 = await cli_mod._async_main(
        ["run", "--scope", "GOLD", "--run-id", str(target_id), "--trigger", "MANUAL_ADMIN"]
    )
    rc2 = await cli_mod._async_main(["run", "--scope", "ALL", "--run-id", ""])

    assert rc1 == 0
    assert rc2 == 0
    assert seen_calls[0]["existing_run_id"] == target_id
    assert seen_calls[0]["scope"] == CrawlScope.GOLD
    assert seen_calls[1]["existing_run_id"] is None
    assert seen_calls[1]["scope"] == CrawlScope.ALL



