import type {
  AlertStatus,
  AnomaliesResponse,
  AuditForecastsResponse,
  AuditSummaryResponse,
  CatalogResponse,
  CityCode,
  CommodityCategory,
  CrawlRunDetailResponse,
  CrawlRunsListResponse,
  CrawlRunStatus,
  CrawlScope,
  ErrorResponse,
  ForecastDetailResponse,
  ForecastEvaluationTypeFilter,
  ForecastHorizon,
  HistoryLocationCode,
  HistoryRange,
  LatestForecastsResponse,
  LatestPricesResponse,
  MacroAnalyticsResponse,
  MacroIndicatorCode,
  NewsAnalyticsResponse,
  NewsDomainFilter,
  PriceEvidenceResponse,
  PriceHistoryResponse,
  SourcesHealthResponse,
  TriggerCrawlRunResponse,
  TriggerForecastRunResponse,
} from "./types";

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export function getApiBaseUrl(): string {
  const raw =
    typeof window === "undefined"
      ? process.env.INTERNAL_API_URL ||
        process.env.INTERNAL_API_BASE_URL ||
        process.env.NEXT_PUBLIC_API_URL ||
        process.env.NEXT_PUBLIC_API_BASE_URL ||
        "http://localhost:8000"
      : process.env.NEXT_PUBLIC_API_URL ||
        process.env.NEXT_PUBLIC_API_BASE_URL ||
        "http://localhost:8000";

  return raw.replace(/\/+$/, "");
}

const DEFAULT_API_TIMEOUT_MS = 55_000;

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      cache: "no-store",
      signal: init?.signal ?? AbortSignal.timeout(DEFAULT_API_TIMEOUT_MS),
      headers: {
        Accept: "application/json",
        ...(init?.headers ?? {}),
      },
    });
  } catch (err: unknown) {
    const isTimeout =
      err instanceof Error && (err.name === "TimeoutError" || err.name === "AbortError");
    throw new ApiError(
      503,
      isTimeout ? "API_COLD_START_TIMEOUT" : "API_NETWORK_ERROR",
      isTimeout
        ? "Máy chủ API mất quá lâu để phản hồi (có thể đang thức dậy từ trạng thái nghỉ trên Render Free ~25–45s). Vui lòng thử lại."
        : "Không thể kết nối tới máy chủ API. Nếu dùng Render Free, hệ thống có thể đang khởi động lại (vui lòng đợi 25–45s rồi thử lại).",
      null
    );
  }

  if (!response.ok) {
    let parsed: Partial<ErrorResponse> | null = null;
    try {
      parsed = (await response.json()) as Partial<ErrorResponse>;
    } catch {
      parsed = null;
    }
    throw new ApiError(
      response.status,
      parsed?.code ?? `HTTP_${response.status}`,
      parsed?.message ?? `Không thể kết nối máy chủ API (HTTP ${response.status}).`,
      parsed?.details ?? null
    );
  }

  return (await response.json()) as T;
}

export async function fetchCatalog(): Promise<CatalogResponse> {
  return apiFetch<CatalogResponse>("/api/v1/catalog");
}

export async function fetchLatestPrices(params?: {
  location?: CityCode;
  domain?: CommodityCategory;
}): Promise<LatestPricesResponse> {
  const query = new URLSearchParams();
  if (params?.location) {
    query.set("location", params.location);
  }
  if (params?.domain) {
    query.set("domain", params.domain);
  }
  const qs = query.toString();
  return apiFetch<LatestPricesResponse>(`/api/v1/prices/latest${qs ? `?${qs}` : ""}`);
}

export async function fetchPriceEvidence(recordId: string): Promise<PriceEvidenceResponse> {
  return apiFetch<PriceEvidenceResponse>(
    `/api/v1/prices/${encodeURIComponent(recordId)}/evidence`
  );
}

export async function fetchAuditSummary(): Promise<AuditSummaryResponse> {
  return apiFetch<AuditSummaryResponse>("/api/v1/audit/summary");
}

export async function fetchCrawlRuns(params?: {
  limit?: number;
  offset?: number;
  scope?: CrawlScope;
  status?: CrawlRunStatus;
}): Promise<CrawlRunsListResponse> {
  const query = new URLSearchParams();
  if (params?.limit !== undefined) {
    query.set("limit", String(params.limit));
  }
  if (params?.offset !== undefined) {
    query.set("offset", String(params.offset));
  }
  if (params?.scope) {
    query.set("scope", params.scope);
  }
  if (params?.status) {
    query.set("status", params.status);
  }
  const qs = query.toString();
  return apiFetch<CrawlRunsListResponse>(`/api/v1/audit/runs${qs ? `?${qs}` : ""}`);
}

export async function fetchCrawlRunDetail(runId: string): Promise<CrawlRunDetailResponse> {
  return apiFetch<CrawlRunDetailResponse>(`/api/v1/audit/runs/${encodeURIComponent(runId)}`);
}

export async function fetchSourcesHealth(): Promise<SourcesHealthResponse> {
  return apiFetch<SourcesHealthResponse>("/api/v1/sources/health");
}

export async function fetchAnomalies(params?: {
  status?: AlertStatus;
  limit?: number;
  offset?: number;
}): Promise<AnomaliesResponse> {
  const query = new URLSearchParams();
  if (params?.status) {
    query.set("status", params.status);
  }
  if (params?.limit !== undefined) {
    query.set("limit", String(params.limit));
  }
  if (params?.offset !== undefined) {
    query.set("offset", String(params.offset));
  }
  const qs = query.toString();
  return apiFetch<AnomaliesResponse>(`/api/v1/audit/anomalies${qs ? `?${qs}` : ""}`);
}

export async function triggerManualCrawlRun(
  scope: CrawlScope,
  adminKey: string
): Promise<TriggerCrawlRunResponse> {
  return apiFetch<TriggerCrawlRunResponse>("/api/v1/admin/crawl-runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-API-Key": adminKey,
    },
    body: JSON.stringify({ scope }),
  });
}

export async function fetchPriceHistory(params: {
  commodity_code: string;
  location: HistoryLocationCode;
  range?: HistoryRange;
}): Promise<PriceHistoryResponse> {
  const query = new URLSearchParams();
  query.set("commodity_code", params.commodity_code);
  query.set("location", params.location);
  if (params.range) {
    query.set("range", params.range);
  }
  return apiFetch<PriceHistoryResponse>(`/api/v1/prices/history?${query.toString()}`);
}

export async function fetchMacroAnalytics(params?: {
  indicators?: MacroIndicatorCode[];
  range?: HistoryRange;
}): Promise<MacroAnalyticsResponse> {
  const query = new URLSearchParams();
  if (params?.indicators && params.indicators.length > 0) {
    query.set("indicators", params.indicators.join(","));
  }
  if (params?.range) {
    query.set("range", params.range);
  }
  const qs = query.toString();
  return apiFetch<MacroAnalyticsResponse>(`/api/v1/analytics/macro${qs ? `?${qs}` : ""}`);
}

export async function fetchNewsAnalytics(params?: {
  domain?: NewsDomainFilter;
  hours?: 24 | 72 | 168;
  limit?: number;
}): Promise<NewsAnalyticsResponse> {
  const query = new URLSearchParams();
  if (params?.domain) {
    query.set("domain", params.domain);
  }
  if (params?.hours !== undefined) {
    query.set("hours", String(params.hours));
  }
  if (params?.limit !== undefined) {
    query.set("limit", String(params.limit));
  }
  const qs = query.toString();
  return apiFetch<NewsAnalyticsResponse>(`/api/v1/analytics/news${qs ? `?${qs}` : ""}`);
}

export async function fetchLatestForecasts(params?: {
  domain?: CommodityCategory;
  location?: CityCode;
  horizon?: ForecastHorizon;
}): Promise<LatestForecastsResponse> {
  const query = new URLSearchParams();
  if (params?.domain) {
    query.set("domain", params.domain);
  }
  if (params?.location) {
    query.set("location", params.location);
  }
  if (params?.horizon) {
    query.set("horizon", params.horizon);
  }
  const qs = query.toString();
  return apiFetch<LatestForecastsResponse>(`/api/v1/forecasts/latest${qs ? `?${qs}` : ""}`);
}

export async function fetchForecastDetail(params: {
  commodity_code: string;
  location: HistoryLocationCode;
  horizon?: ForecastHorizon;
  history_range?: HistoryRange;
}): Promise<ForecastDetailResponse> {
  const query = new URLSearchParams();
  query.set("commodity_code", params.commodity_code);
  query.set("location", params.location);
  if (params.horizon) {
    query.set("horizon", params.horizon);
  }
  if (params.history_range) {
    query.set("history_range", params.history_range);
  }
  return apiFetch<ForecastDetailResponse>(`/api/v1/forecasts/detail?${query.toString()}`);
}

export async function fetchAuditForecasts(params?: {
  evaluation_type?: ForecastEvaluationTypeFilter;
  domain?: CommodityCategory;
  horizon?: ForecastHorizon;
  limit?: number;
  offset?: number;
}): Promise<AuditForecastsResponse> {
  const query = new URLSearchParams();
  if (params?.evaluation_type) {
    query.set("evaluation_type", params.evaluation_type);
  }
  if (params?.domain) {
    query.set("domain", params.domain);
  }
  if (params?.horizon) {
    query.set("horizon", params.horizon);
  }
  if (params?.limit !== undefined) {
    query.set("limit", String(params.limit));
  }
  if (params?.offset !== undefined) {
    query.set("offset", String(params.offset));
  }
  const qs = query.toString();
  return apiFetch<AuditForecastsResponse>(`/api/v1/audit/forecasts${qs ? `?${qs}` : ""}`);
}

export async function triggerManualForecastRun(
  scope: CrawlScope,
  adminKey: string
): Promise<TriggerForecastRunResponse> {
  return apiFetch<TriggerForecastRunResponse>("/api/v1/admin/forecast-runs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-API-Key": adminKey,
    },
    body: JSON.stringify({ scope }),
  });
}

