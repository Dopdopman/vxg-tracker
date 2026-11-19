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

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${getApiBaseUrl()}${path}`;
  const response = await fetch(url, {
    ...init,
    cache: "no-store",
    headers: {
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });

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

