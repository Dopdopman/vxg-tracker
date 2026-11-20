"""Schemas for admin trigger endpoints (/api/v1/admin/crawl-runs & /forecast-runs)."""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from vxg_api.schemas.common import VnDatetime
from vxg_shared.enums import CrawlRunStatus, CrawlScope, TriggerType


class TriggerCrawlRunRequest(BaseModel):
    """Payload to enqueue a manual crawl run for a selected scope."""

    scope: CrawlScope = CrawlScope.ALL


class TriggerCrawlRunResponse(BaseModel):
    """Confirmation payload returned with HTTP 202 Accepted when run is queued."""

    run_id: uuid.UUID
    run_code: str
    scope: CrawlScope
    trigger_type: TriggerType
    status: CrawlRunStatus
    queued_at_vn: VnDatetime


class TriggerForecastRunRequest(BaseModel):
    """Payload to enqueue a manual forecast + evaluation run for a selected scope."""

    scope: CrawlScope = CrawlScope.ALL


class TriggerForecastRunResponse(BaseModel):
    """Confirmation payload returned with HTTP 202 Accepted when forecast run is queued."""

    run_id: uuid.UUID
    run_code: str
    scope: CrawlScope
    trigger_type: TriggerType
    status: CrawlRunStatus
    queued_at_vn: VnDatetime

