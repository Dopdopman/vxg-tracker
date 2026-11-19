import type {
  AlertStatus,
  AuditSummaryResponse,
  CatalogResponse,
  CityCode,
  CommodityCategory,
  CrawlRunDetailResponse,
  CrawlRunsListResponse,
  CrawlRunStatus,
  CrawlScope,
  ErrorResponse,
  LatestPricesResponse,
  PriceEvidenceResponse,
  SourcesHealthResponse,
  TriggerCrawlRunResponse,
  AnomaliesResponse,
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

