export type CityCode = "HN" | "DN" | "HCM";
export type CommodityCategory = "GOLD" | "FUEL" | "GAS";
export type CrawlScope = "ALL" | "GOLD" | "FUEL" | "GAS";
export type MacroRegion = "NORTH" | "CENTRAL" | "SOUTH" | "NATIONAL";
export type PriceUnit = "VND_PER_LUONG" | "VND_PER_LITER" | "VND_PER_CYLINDER";
export type PricingType = "BUY_SELL" | "SINGLE_RETAIL";
export type SourceTier = "PRIMARY_OFFICIAL" | "SECONDARY_CROSSCHECK";
export type FetchStrategy = "HTTP_XML" | "HTTP_JSON" | "HTTP_HTML" | "PLAYWRIGHT_JS";
export type VerificationStatus =
  | "VERIFIED"
  | "CROSS_CHECKED"
  | "FLAGGED"
  | "REJECTED"
  | "OVERRIDDEN_MANUAL";
export type CrawlRunStatus =
  | "QUEUED"
  | "RUNNING"
  | "COMPLETED"
  | "PARTIAL_SUCCESS"
  | "FAILED";
export type TriggerType = "SCHEDULED" | "MANUAL_ADMIN" | "RETRY";
export type FetchStatus =
  | "SUCCESS"
  | "HTTP_ERROR"
  | "TIMEOUT"
  | "PARSE_ERROR"
  | "SCHEMA_DRIFT"
  | "BLOCKED";
export type SourceHealthStatus = "HEALTHY" | "DEGRADED" | "DOWN" | "UNKNOWN";
export type AlertStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED";
export type AlertSeverity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type AlertType =
  | "SCHEMA_DRIFT"
  | "SOURCE_DOWN_3X"
  | "PRICE_SPIKE"
  | "CROSS_CHECK_MISMATCH"
  | "VALIDATION_ERROR";

export interface ErrorResponse {
  code: string;
  message: string;
  details: unknown;
}

export interface CityCatalogItem {
  code: CityCode;
  name: string;
  macro_region: MacroRegion;
  fuel_zone_code: string | null;
  display_order: number;
}

export interface DomainCatalogItem {
  code: CommodityCategory;
  name: string;
  default_unit: PriceUnit;
}

export interface CommodityCatalogItem {
  code: string;
  category: CommodityCategory;
  brand: string;
  product_name: string;
  spec_detail: string | null;
  unit: PriceUnit;
  pricing_type: PricingType;
  display_order: number;
}

export interface SourceCatalogItem {
  code: string;
  name: string;
  category: CommodityCategory;
  tier: SourceTier;
  base_url: string;
  endpoint_url: string;
  fetch_strategy: FetchStrategy;
  schedule_cron: string;
  is_active: boolean;
}

export interface CatalogResponse {
  timezone: string;
  server_time_vn: string;
  cities: CityCatalogItem[];
  domains: DomainCatalogItem[];
  commodities: CommodityCatalogItem[];
  sources: SourceCatalogItem[];
}

export interface LatestPriceItem {
  record_id: string;
  domain: CommodityCategory;
  location_code: string;
  location_name: string;
  applied_zone: string | null;
  commodity_code: string;
  commodity_name: string;
  spec_detail: string | null;
  brand: string;
  source_code: string;
  source_name: string;
  pricing_type: PricingType;
  unit: PriceUnit;
  buy_price: number | null;
  sell_price: number | null;
  retail_price: number | null;
  effective_date: string;
  verification_status: VerificationStatus;
  cross_check_delta_pct: number | null;
  source_url: string;
  published_at_vn: string | null;
  fetched_at_vn: string;
  last_verified_at_vn: string;
}

export interface LatestPricesResponse {
  timezone: string;
  as_of_vn: string;
  location_filter: CityCode | null;
  domain_filter: CommodityCategory | null;
  fuel_zone_mapping: Record<string, string>;
  count: number;
  last_updated_vn: string | null;
  items: LatestPriceItem[];
}

export interface CrossCheckSourceInfo {
  source_code: string;
  source_name: string;
  source_url: string;
  delta_pct: number | null;
}

export interface PriceEvidenceResponse {
  record_id: string;
  domain: CommodityCategory;
  commodity_code: string;
  commodity_name: string;
  brand: string;
  location_code: string;
  location_name: string;
  unit: PriceUnit;
  buy_price: number | null;
  sell_price: number | null;
  retail_price: number | null;
  effective_date: string;
  verification_status: VerificationStatus;
  source_code: string;
  source_name: string;
  source_tier: SourceTier;
  source_url: string;
  run_id: string;
  run_code: string;
  source_log_id: string;
  fetch_strategy_used: string;
  http_status: number | null;
  content_sha256: string;
  snapshot_byte_size: number | null;
  snapshot_content_type: string | null;
  cross_check_delta_pct: number | null;
  cross_check_sources: CrossCheckSourceInfo[];
  published_at_vn: string | null;
  fetched_at_vn: string;
  last_verified_at_vn: string;
  validation_notes: Record<string, unknown>;
}

export interface CrawlRunItem {
  id: string;
  run_code: string;
  scope_category: CrawlScope;
  trigger_type: TriggerType;
  status: CrawlRunStatus;
  sources_queried: number;
  pages_fetched_ok: number;
  pages_fetched_failed: number;
  documents_parsed: number;
  records_extracted: number;
  records_verified: number;
  records_flagged: number;
  records_unchanged: number;
  started_at_vn: string;
  finished_at_vn: string | null;
  duration_ms: number | null;
  error_summary: string | null;
}

export interface AuditWindowMetrics {
  window_start_vn: string;
  total_runs: number;
  completed_runs: number;
  partial_success_runs: number;
  failed_runs: number;
  active_runs: number;
  sources_queried: number;
  pages_fetched_ok: number;
  pages_fetched_failed: number;
  documents_parsed: number;
  records_extracted: number;
  records_verified: number;
  records_flagged: number;
  records_unchanged: number;
}

export interface AuditSummaryResponse {
  timezone: string;
  as_of_vn: string;
  today_date_vn: string;
  active_sources_count: number;
  open_anomalies: number;
  today: AuditWindowMetrics;
  last_7_days: AuditWindowMetrics;
  latest_run: CrawlRunItem | null;
}

export interface CrawlRunsListResponse {
  total: number;
  limit: number;
  offset: number;
  items: CrawlRunItem[];
}

export interface CrawlSourceLogItem {
  id: string;
  source_code: string;
  source_name: string;
  source_category: CommodityCategory;
  source_tier: SourceTier;
  request_url: string;
  final_url: string | null;
  http_status: number | null;
  tier_used: string;
  fetch_status: FetchStatus;
  latency_ms: number | null;
  attempt_number: number;
  bytes_received: number | null;
  content_sha256: string | null;
  documents_parsed: number;
  records_extracted: number;
  records_verified: number;
  records_flagged: number;
  error_code: string | null;
  error_message: string | null;
  parser_metadata: Record<string, unknown>;
  created_at_vn: string;
}

export interface CrawlRunDetailResponse extends CrawlRunItem {
  source_logs: CrawlSourceLogItem[];
}

export interface SourceHealthItem {
  source_code: string;
  source_name: string;
  category: CommodityCategory;
  tier: SourceTier;
  endpoint_url: string;
  fetch_strategy: FetchStrategy;
  schedule_cron: string;
  current_status: SourceHealthStatus;
  consecutive_failures: number;
  total_checks_7d: number;
  success_checks_7d: number;
  failed_checks_7d: number;
  success_rate_7d_pct: number | null;
  avg_latency_ms_7d: number | null;
  last_success_at_vn: string | null;
  last_checked_at_vn: string | null;
  last_fetch_status: FetchStatus | null;
  last_http_status: number | null;
  last_error_message: string | null;
}

export interface SourcesHealthResponse {
  timezone: string;
  as_of_vn: string;
  window_days: number;
  total_active_sources: number;
  healthy_count: number;
  degraded_count: number;
  down_count: number;
  unknown_count: number;
  items: SourceHealthItem[];
}

export interface AnomalyAlertItem {
  id: string;
  alert_type: AlertType;
  severity: AlertSeverity;
  status: AlertStatus;
  message: string;
  crawl_run_id: string | null;
  run_code: string | null;
  source_log_id: string | null;
  source_code: string | null;
  source_name: string | null;
  price_record_id: string | null;
  payload: Record<string, unknown>;
  created_at_vn: string;
  resolved_at_vn: string | null;
}

export interface AnomaliesResponse {
  total: number;
  limit: number;
  offset: number;
  items: AnomalyAlertItem[];
}

export interface TriggerCrawlRunResponse {
  run_id: string;
  run_code: string;
  scope: CrawlScope;
  trigger_type: TriggerType;
  status: CrawlRunStatus;
  queued_at_vn: string;
}

