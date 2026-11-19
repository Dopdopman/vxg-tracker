"""Lightweight alert notifier supporting Telegram Bot and Discord Webhook channels."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from vxg_shared.enums import CrawlRunStatus, FetchStatus
from vxg_shared.timezone import vn_now
from vxg_worker.config import WorkerSettings

if TYPE_CHECKING:
    from vxg_worker.runner import CrawlRunSummary

logger = logging.getLogger("vxg_worker.notifier")

_MAX_MESSAGE_LEN = 1800
_TRIGGER_FETCH_STATUSES = frozenset({FetchStatus.SCHEMA_DRIFT.value, FetchStatus.BLOCKED.value})


@dataclass(slots=True)
class NotifyResult:
    """Outcome counters returned after evaluating or delivering run alerts."""

    triggered: bool
    skipped_unconfigured: bool = False
    telegram_sent: bool = False
    discord_sent: bool = False
    errors: list[str] = field(default_factory=list)
    message: str | None = None


class AlertNotifier:
    """Delivers concise VN-timezone failure/anomaly alerts without crashing the worker."""

    def __init__(
        self,
        *,
        telegram_bot_token: str | None = None,
        telegram_chat_id: str | None = None,
        discord_webhook_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._telegram_bot_token = (telegram_bot_token or "").strip() or None
        self._telegram_chat_id = (telegram_chat_id or "").strip() or None
        self._discord_webhook_url = (discord_webhook_url or "").strip() or None
        self._http_client = http_client
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_settings(cls, settings: WorkerSettings) -> AlertNotifier:
        """Build notifier from WorkerSettings alert environment variables."""
        return cls(
            telegram_bot_token=settings.alert_telegram_bot_token,
            telegram_chat_id=settings.alert_telegram_chat_id,
            discord_webhook_url=settings.alert_discord_webhook_url,
        )

    @property
    def has_telegram(self) -> bool:
        return bool(self._telegram_bot_token and self._telegram_chat_id)

    @property
    def has_discord(self) -> bool:
        return bool(self._discord_webhook_url)

    @property
    def is_configured(self) -> bool:
        return self.has_telegram or self.has_discord

    @staticmethod
    def build_run_alert_message(
        summary: CrawlRunSummary,
        *,
        anomaly_messages: list[str] | None = None,
    ) -> str | None:
        """Build formatted alert text if run failed, hit drift/403 block, or flagged prices."""
        problem_sources = [
            log
            for log in summary.source_logs
            if log.fetch_status in _TRIGGER_FETCH_STATUSES or log.http_status == 403
        ]
        anomalies = anomaly_messages or []
        should_alert = (
            summary.status == CrawlRunStatus.FAILED.value
            or bool(problem_sources)
            or summary.records_flagged_count > 0
            or bool(anomalies)
        )
        if not should_alert:
            return None

        ts_vn = vn_now().strftime("%H:%M:%S %d/%m/%Y UTC+7")
        lines = [
            f"[VXG TRACKER] Cảnh báo thu thập dữ liệu ({ts_vn})",
            f"Phiên: {summary.run_code} | Phạm vi: {summary.scope} | Trạng thái: {summary.status}",
            (
                f"Nguồn OK/Lỗi: {summary.pages_fetched_success}/{summary.pages_fetched_failed} "
                f"| Xác thực: {summary.records_verified_count} "
                f"| Bị chặn (FLAGGED): {summary.records_flagged_count}"
            ),
        ]

        if problem_sources:
            lines.append("Nguồn gặp sự cố nghiêm trọng:")
            for log in problem_sources[:5]:
                http_part = f"HTTP {log.http_status}" if log.http_status is not None else "HTTP -"
                detail = log.error_message or log.fetch_status
                lines.append(f"- {log.source_code} ({log.fetch_status}, {http_part}): {detail[:160]}")

        if anomalies:
            lines.append(f"Cảnh báo bất thường ({len(anomalies)}):")
            for msg in anomalies[:5]:
                lines.append(f"- {msg[:180]}")

        rendered = "\n".join(lines)
        if len(rendered) > _MAX_MESSAGE_LEN:
            return rendered[: _MAX_MESSAGE_LEN - 3] + "..."
        return rendered

    async def notify_crawl_run(
        self,
        summary: CrawlRunSummary,
        *,
        anomaly_messages: list[str] | None = None,
    ) -> NotifyResult:
        """Send alert to configured channels when a CrawlRun hits severe errors or anomalies."""
        message = self.build_run_alert_message(summary, anomaly_messages=anomaly_messages)
        if message is None:
            return NotifyResult(triggered=False)

        if not self.is_configured:
            logger.info(
                "Alert skipped (no Telegram/Discord channels configured) for run %s",
                summary.run_code,
            )
            return NotifyResult(triggered=True, skipped_unconfigured=True, message=message)

        if self._http_client is not None:
            return await self._deliver(self._http_client, message)

        async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
            return await self._deliver(client, message)

    async def _deliver(self, client: httpx.AsyncClient, message: str) -> NotifyResult:
        result = NotifyResult(triggered=True, message=message)

        if self.has_telegram:
            assert self._telegram_bot_token is not None
            assert self._telegram_chat_id is not None
            tg_url = f"https://api.telegram.org/bot{self._telegram_bot_token}/sendMessage"
            try:
                resp = await client.post(
                    tg_url,
                    json={
                        "chat_id": self._telegram_chat_id,
                        "text": message,
                        "disable_web_page_preview": True,
                    },
                )
                resp.raise_for_status()
                result.telegram_sent = True
            except Exception as exc:
                err_msg = f"Telegram delivery failed: {exc}"
                logger.warning(err_msg)
                result.errors.append(err_msg)

        if self.has_discord:
            assert self._discord_webhook_url is not None
            try:
                resp = await client.post(
                    self._discord_webhook_url,
                    json={"content": message},
                )
                resp.raise_for_status()
                result.discord_sent = True
            except Exception as exc:
                err_msg = f"Discord delivery failed: {exc}"
                logger.warning(err_msg)
                result.errors.append(err_msg)

        return result

