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
  prev_change_vnd?: number | null;
  prev_change_pct?: number | null;
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

export type HistoryRange = "30d" | "90d" | "1y" | "2y";
export type HistoryLocationCode = "HN" | "DN" | "HCM" | "VUNG_1" | "VUNG_2";
export type SeriesOrigin = string;

export interface PriceHistoryPoint {
  date: string;
  buy_price: number | null;
  sell_price: number | null;
  retail_price: number | null;
  reference_price: number;
  origin: SeriesOrigin;
  is_adjustment_day: boolean;
  source_code: string;
  source_url: string;
}

export interface PriceHistoryStats {
  min_vnd: number | null;
  max_vnd: number | null;
  latest_vnd: number | null;
  change_vnd: number | null;
  change_pct: number | null;
  total_points: number;
}

export interface PriceHistoryResponse {
  timezone: string;
  as_of_vn: string;
  commodity_code: string;
  commodity_name: string;
  brand: string;
  domain: CommodityCategory;
  pricing_type: PricingType;
  unit: PriceUnit;
  location_code: string;
  location_name: string;
  applied_zone: string | null;
  range: HistoryRange;
  start_date: string;
  end_date: string;
  stats: PriceHistoryStats;
  points: PriceHistoryPoint[];
}

export type MacroIndicatorCode =
  | "XAU_USD"
  | "BRENT_USD"
  | "WTI_USD"
  | "DXY"
  | "USD_VND_VCB"
  | "USD_VND_MARKET";

export interface MacroSeriesPoint {
  date: string;
  value: number;
  change_pct_1d: number | null;
  source_code: string;
}

export interface MacroLatestItem {
  indicator_code: MacroIndicatorCode;
  label_vi: string;
  unit: string;
  date: string;
  value: number;
  open_value: number | null;
  high_value: number | null;
  low_value: number | null;
  change_pct_1d: number | null;
  change_pct_7d: number | null;
  source_code: string;
  source_url: string;
  metadata: Record<string, unknown>;
}

export interface GoldWorldSpreadItem {
  sjc_sell_vnd: number;
  sjc_date: string;
  xau_usd: number;
  xau_date: string;
  usd_vnd: number;
  fx_date: string;
  fx_source: string;
  conversion_factor: number;
  world_vnd_per_tael: number;
  spread_vnd: number;
  spread_pct: number;
  disclaimer_vi: string;
}

export interface GoldWorldSpreadPoint {
  date: string;
  sjc_sell_vnd: number;
  world_vnd_per_tael: number;
  spread_vnd: number;
  spread_pct: number;
  fx_source: string;
}

export interface MacroAnalyticsResponse {
  timezone: string;
  as_of_vn: string;
  range: HistoryRange;
  start_date: string;
  end_date: string;
  latest: Record<string, MacroLatestItem>;
  sjc_world_spread: GoldWorldSpreadItem | null;
  sjc_world_spread_series: GoldWorldSpreadPoint[];
  series: Record<string, MacroSeriesPoint[]>;
}

export type NewsDomain = "GOLD" | "FUEL" | "GAS" | "MACRO";
export type NewsDomainFilter = "ALL" | NewsDomain;
export type NewsImpactLevel = "HIGH" | "MEDIUM" | "LOW";

export interface NewsSignalItem {
  id: string;
  domain: NewsDomain;
  source_name: string;
  title: string;
  url: string;
  published_at: string;
  impact_level: NewsImpactLevel;
  polarity_score: number;
  catalysts: string[];
  matched_keywords: string[];
  summary: string | null;
  llm_summary_vi: string | null;
  scoring_engine: string;
}

export interface NewsWindowSummary {
  total_articles: number;
  high_impact_count: number;
  avg_polarity_score: number | null;
  top_catalysts: string[];
}

export interface NewsAnalyticsResponse {
  timezone: string;
  as_of_vn: string;
  domain_filter: NewsDomainFilter;
  hours: number;
  total: number;
  summary: NewsWindowSummary;
  items: NewsSignalItem[];
}

export type ForecastHorizon = "T_PLUS_1" | "T_PLUS_7" | "NEXT_FUEL_CYCLE" | "NEXT_GAS_MONTH";
export type ForecastStatus = "READY" | "INSUFFICIENT_DATA";
export type PredictedDirection = "UP" | "DOWN" | "FLAT" | "UNKNOWN";
export type ForecastEvaluationType = "LIVE" | "BACKTEST";
export type ForecastEvaluationTypeFilter = "ALL" | ForecastEvaluationType;

export interface ForecastNewsRef {
  id: string;
  domain: string;
  publisher: string;
  title: string;
  url: string;
  published_at_vn: string | null;
  impact_level: NewsImpactLevel | string;
  polarity_score: number;
  catalysts: string[];
  summary: string | null;
}

export interface ForecastFeatureDriver {
  key: string;
  label_vi: string;
  value: number | null;
  weight_pct: number;
  direction: string;
}

export interface ForecastBacktestMetrics {
  sample_count: number;
  mae_vnd: number | null;
  mape_pct: number | null;
  naive_mae_vnd: number | null;
  naive_mape_pct: number | null;
  directional_accuracy_pct: number | null;
  interval_coverage_pct: number | null;
}

export interface ForecastItem {
  forecast_id: string | null;
  domain: CommodityCategory;
  commodity_code: string;
  commodity_name: string;
  brand: string;
  unit: PriceUnit;
  pricing_type: PricingType;
  location_code: string;
  location_name: string;
  applied_zone: string | null;
  horizon_type: ForecastHorizon;
  model_version: string;
  status: ForecastStatus;
  insufficient_reason: string | null;
  forecast_date: string;
  target_date: string;
  baseline_price: number | null;
  point_estimate: number | null;
  lower_bound: number | null;
  upper_bound: number | null;
  expected_change_vnd: number | null;
  expected_change_pct: number | null;
  predicted_direction: PredictedDirection;
  confidence_score: number | null;
  news_adjustment_pct: number;
  high_volatility_flag: boolean;
  band_widening_pct: number;
  feature_cutoff_vn: string | null;
  backtest_summary: ForecastBacktestMetrics | null;
  feature_drivers: ForecastFeatureDriver[];
  news_signals: ForecastNewsRef[];
  drivers_json: Record<string, unknown>;
}

export interface LatestForecastsResponse {
  timezone: string;
  as_of_vn: string;
  domain_filter: CommodityCategory | null;
  location_filter: string;
  applied_zone: string | null;
  disclaimer: string;
  count: number;
  items: ForecastItem[];
}

export interface MacroHighlightCard {
  key: string;
  label_vi: string;
  value_display: string;
  value: number | null;
  unit: string;
  change_pct_1d: number | null;
  change_pct_7d: number | null;
  note_vi: string | null;
}

export interface ForecastDetailResponse {
  timezone: string;
  as_of_vn: string;
  disclaimer: string;
  commodity_code: string;
  location_code: string;
  applied_zone: string | null;
  available_horizons: ForecastHorizon[];
  forecast: ForecastItem;
  history_range: HistoryRange;
  history_stats: PriceHistoryStats;
  history_points: PriceHistoryPoint[];
  macro_highlights: MacroHighlightCard[];
  macro_drivers: ForecastFeatureDriver[];
  news_signals: ForecastNewsRef[];
  backtest_summary: ForecastBacktestMetrics | null;
}

export interface AuditEvaluationMetrics {
  evaluation_type: ForecastEvaluationType;
  total_evaluated: number;
  mae_vnd: number | null;
  mape_pct: number | null;
  naive_mae_vnd: number | null;
  naive_mape_pct: number | null;
  direction_accuracy_pct: number | null;
  interval_coverage_pct: number | null;
}

export interface AuditForecastEvaluationItem {
  evaluation_id: string;
  evaluation_type: ForecastEvaluationType;
  forecast_id: string | null;
  domain: CommodityCategory;
  commodity_code: string;
  commodity_name: string;
  location_code: string;
  horizon_type: ForecastHorizon;
  model_version: string;
  forecast_date: string;
  target_date: string;
  actual_date: string;
  baseline_price: number;
  predicted_price: number;
  lower_bound: number | null;
  upper_bound: number | null;
  actual_price: number;
  abs_error_vnd: number;
  pct_error: number;
  predicted_direction: PredictedDirection;
  actual_direction: PredictedDirection;
  direction_correct: boolean;
  within_interval: boolean;
  evaluated_at_vn: string;
}

export interface LatestForecastRunSummary {
  id: string;
  run_code: string;
  scope_category: CrawlScope;
  trigger_type: TriggerType;
  status: CrawlRunStatus;
  forecasts_generated: number;
  evaluations_created: number;
  started_at_vn: string;
  finished_at_vn: string | null;
  duration_ms: number | null;
  error_summary: string | null;
}

export interface AuditForecastsResponse {
  timezone: string;
  as_of_vn: string;
  evaluation_type_filter: ForecastEvaluationTypeFilter;
  domain_filter: CommodityCategory | null;
  horizon_filter: ForecastHorizon | null;
  live_summary: AuditEvaluationMetrics;
  backtest_summary: AuditEvaluationMetrics;
  latest_forecast_run: LatestForecastRunSummary | null;
  total: number;
  limit: number;
  offset: number;
  items: AuditForecastEvaluationItem[];
}

export interface TriggerForecastRunResponse {
  run_id: string;
  run_code: string;
  scope: CrawlScope;
  trigger_type: TriggerType;
  status: CrawlRunStatus;
  queued_at_vn: string;
}

