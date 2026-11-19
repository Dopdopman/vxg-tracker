"use client";

import { useState } from "react";
import { ChevronLeft, ChevronRight, Play, RefreshCw, ShieldCheck } from "lucide-react";
import {
  fetchAnomalies,
  fetchAuditSummary,
  fetchCrawlRuns,
  fetchSourcesHealth,
} from "@/lib/api";
import {
  formatAlertTypeLabel,
  formatCount,
  formatDomainLabel,
  formatDuration,
  formatLatencyMs,
  formatPercent,
  formatScopeLabel,
  formatSeverityLabel,
  formatTriggerLabel,
  formatVnDateTime,
} from "@/lib/format";
import type {
  AlertStatus,
  AnomaliesResponse,
  AuditSummaryResponse,
  CrawlRunsListResponse,
  SourcesHealthResponse,
} from "@/lib/types";
import { ManualCrawlModal } from "./manual-crawl-modal";
import { RunDetailDrawer } from "./run-detail-drawer";
import { SiteHeader } from "./site-header";
import { AlertStatusBadge, RunStatusBadge, SourceHealthBadge } from "./status-badge";

type AuditTab = "runs" | "health" | "anomalies";
type AnomalyFilter = "OPEN" | "RESOLVED" | "ALL";

interface AuditDashboardProps {
  initialSummary: AuditSummaryResponse | null;
  initialRuns: CrawlRunsListResponse | null;
  initialHealth: SourcesHealthResponse | null;
  initialAnomalies: AnomaliesResponse | null;
  initialRunId: string | null;
  initialTab: AuditTab;
  initialError: string | null;
}

const RUNS_PAGE_SIZE = 15;

export function AuditDashboard({
  initialSummary,
  initialRuns,
  initialHealth,
  initialAnomalies,
  initialRunId,
  initialTab,
  initialError,
}: AuditDashboardProps) {
  const [timeframe, setTimeframe] = useState<"today" | "7d">("today");
  const [activeTab, setActiveTab] = useState<AuditTab>(initialTab);
  const [summary, setSummary] = useState<AuditSummaryResponse | null>(initialSummary);
  const [runsData, setRunsData] = useState<CrawlRunsListResponse | null>(initialRuns);
  const [healthData, setHealthData] = useState<SourcesHealthResponse | null>(initialHealth);
  const [anomaliesData, setAnomaliesData] = useState<AnomaliesResponse | null>(initialAnomalies);
  const [anomalyFilter, setAnomalyFilter] = useState<AnomalyFilter>("OPEN");
  const [runsOffset, setRunsOffset] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(initialRunId);
  const [manualModalOpen, setManualModalOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  const metrics = timeframe === "today" ? summary?.today : summary?.last_7_days;

  const refreshAll = async (nextOffset = runsOffset, nextAnomFilter = anomalyFilter) => {
    setRefreshing(true);
    setError(null);
    try {
      const statusParam: AlertStatus | undefined =
        nextAnomFilter === "ALL" ? undefined : nextAnomFilter;
      const [nextSummary, nextRuns, nextHealth, nextAnomalies] = await Promise.all([
        fetchAuditSummary(),
        fetchCrawlRuns({ limit: RUNS_PAGE_SIZE, offset: nextOffset }),
        fetchSourcesHealth(),
        fetchAnomalies({ status: statusParam, limit: 50, offset: 0 }),
      ]);
      setSummary(nextSummary);
      setRunsData(nextRuns);
      setHealthData(nextHealth);
      setAnomaliesData(nextAnomalies);
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Không thể làm mới dữ liệu kiểm toán từ API."
      );
    } finally {
      setRefreshing(false);
    }
  };

  const handleRunsPageChange = async (newOffset: number) => {
    setRunsOffset(newOffset);
    setRefreshing(true);
    setError(null);
    try {
      const nextRuns = await fetchCrawlRuns({ limit: RUNS_PAGE_SIZE, offset: newOffset });
      setRunsData(nextRuns);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Không thể tải trang lịch sử phiên cào.");
    } finally {
      setRefreshing(false);
    }
  };

  const handleAnomalyFilterChange = async (filter: AnomalyFilter) => {
    setAnomalyFilter(filter);
    setRefreshing(true);
    setError(null);
    try {
      const statusParam: AlertStatus | undefined = filter === "ALL" ? undefined : filter;
      const nextAnomalies = await fetchAnomalies({
        status: statusParam,
        limit: 50,
        offset: 0,
      });
      setAnomaliesData(nextAnomalies);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Không thể tải danh sách cảnh báo.");
    } finally {
      setRefreshing(false);
    }
  };

  const latestUpdateVn =
    summary?.latest_run?.finished_at_vn ?? summary?.latest_run?.started_at_vn ?? null;
  const totalRuns = runsData?.total ?? 0;
  const currentPage = Math.floor(runsOffset / RUNS_PAGE_SIZE) + 1;
  const totalPages = Math.max(1, Math.ceil(totalRuns / RUNS_PAGE_SIZE));

  return (
    <div className="min-h-screen bg-[#F8FAFC] text-[#0F172A]">
      <SiteHeader lastUpdatedVn={latestUpdateVn} />

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="mb-6 flex flex-col justify-between gap-4 border-b border-[#E2E8F0] pb-6 sm:flex-row sm:items-end">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-[#0F172A]">
              Minh bạch dữ liệu &amp; Kiểm toán
            </h1>
            <p className="mt-1 text-sm text-[#475569]">
              Theo dõi toàn bộ phiên thu thập, trạng thái 11 nguồn chính thức và cảnh báo an toàn
              theo chuẩn giờ Việt Nam (UTC+7).
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <div
              role="group"
              aria-label="Khung thời gian thống kê"
              className="inline-flex rounded border border-[#E2E8F0] bg-white p-0.5 text-xs"
            >
              <button
                type="button"
                onClick={() => setTimeframe("today")}
                aria-pressed={timeframe === "today"}
                className={`rounded-sm px-3 py-1.5 transition-colors ${
                  timeframe === "today"
                    ? "bg-[#0F172A] font-medium text-white"
                    : "text-[#475569] hover:text-[#0F172A]"
                }`}
              >
                Hôm nay
              </button>
              <button
                type="button"
                onClick={() => setTimeframe("7d")}
                aria-pressed={timeframe === "7d"}
                className={`rounded-sm px-3 py-1.5 transition-colors ${
                  timeframe === "7d"
                    ? "bg-[#0F172A] font-medium text-white"
                    : "text-[#475569] hover:text-[#0F172A]"
                }`}
              >
                7 ngày qua
              </button>
            </div>

            <button
              type="button"
              onClick={() => void refreshAll()}
              disabled={refreshing}
              aria-label="Làm mới số liệu kiểm toán"
              className="inline-flex items-center gap-1.5 rounded border border-[#E2E8F0] bg-white px-3 py-2 text-xs font-medium text-[#0F172A] hover:bg-[#F8FAFC] disabled:opacity-50"
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`}
                strokeWidth={1.75}
              />
              <span>Làm mới</span>
            </button>

            <button
              type="button"
              onClick={() => setManualModalOpen(true)}
              className="inline-flex items-center gap-1.5 rounded bg-[#0F172A] px-3.5 py-2 text-xs font-medium text-white transition-opacity hover:opacity-90"
            >
              <Play className="h-3.5 w-3.5" strokeWidth={1.75} />
              <span>Chạy cào thủ công</span>
            </button>
          </div>
        </div>

        {error && (
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded border border-rose-200 bg-rose-50/70 px-4 py-3 text-sm text-rose-800">
            <div>
              <p className="font-medium">Không thể tải dữ liệu kiểm toán</p>
              <p className="mt-0.5 text-xs text-rose-700">{error}</p>
            </div>
            <button
              type="button"
              onClick={() => void refreshAll()}
              className="inline-flex items-center gap-1.5 rounded border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-800 hover:bg-rose-50"
            >
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />
              Thử lại
            </button>
          </div>
        )}

        {/* Thanh 5 chỉ số minh bạch cốt lõi */}
        <section
          aria-label="Chỉ số kiểm toán tổng quan"
          className="mb-8 grid grid-cols-2 divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-white sm:grid-cols-5 sm:divide-x sm:divide-y-0"
        >
          <div className="p-4">
            <p className="text-xs font-medium text-[#475569]">Nguồn đã truy vấn</p>
            <p className="tabular-nums mt-1.5 text-2xl font-semibold text-[#0F172A]">
              {formatCount(metrics?.sources_queried)}
            </p>
            <p className="tabular-nums mt-1 text-xs text-[#475569]">
              {formatCount(metrics?.total_runs)} phiên chạy
            </p>
          </div>

          <div className="p-4">
            <p className="text-xs font-medium text-[#475569]">Trang tải OK / Lỗi</p>
            <p className="tabular-nums mt-1.5 text-2xl font-semibold text-[#0F172A]">
              <span>{formatCount(metrics?.pages_fetched_ok)}</span>
              <span className="mx-1 font-normal text-[#CBD5E1]">/</span>
              <span className={metrics?.pages_fetched_failed ? "text-rose-700" : "text-[#475569]"}>
                {formatCount(metrics?.pages_fetched_failed)}
              </span>
            </p>
            <p className="mt-1 text-xs text-[#475569]">Lượt truy cập HTTP/Browser</p>
          </div>

          <div className="p-4">
            <p className="text-xs font-medium text-[#475569]">Bài báo &amp; bảng giá</p>
            <p className="tabular-nums mt-1.5 text-2xl font-semibold text-[#0F172A]">
              {formatCount(metrics?.documents_parsed)}
            </p>
            <p className="mt-1 text-xs text-[#475569]">Tài liệu đã bóc tách</p>
          </div>

          <div className="p-4">
            <p className="text-xs font-medium text-[#475569]">Bản ghi đã xác thực</p>
            <p className="tabular-nums mt-1.5 text-2xl font-semibold text-emerald-700">
              {formatCount(metrics?.records_verified)}
            </p>
            <p className="tabular-nums mt-1 text-xs text-[#475569]">
              {formatCount(metrics?.records_unchanged)} bản ghi không đổi
            </p>
          </div>

          <div className="col-span-2 p-4 sm:col-span-1">
            <p className="text-xs font-medium text-[#475569]">Cảnh báo bất thường</p>
            <p
              className={`tabular-nums mt-1.5 text-2xl font-semibold ${
                (summary?.open_anomalies ?? 0) > 0 ? "text-rose-700" : "text-[#0F172A]"
              }`}
            >
              {formatCount(summary?.open_anomalies)}
            </p>
            <p className="mt-1 text-xs text-[#475569]">Cảnh báo đang mở</p>
          </div>
        </section>

        {/* 3 Tab phụ gọn gàng */}
        <div className="mb-4 flex flex-wrap items-center gap-2 border-b border-[#E2E8F0]">
          <button
            type="button"
            onClick={() => setActiveTab("runs")}
            className={`border-b-2 px-1 pb-2.5 text-sm transition-colors ${
              activeTab === "runs"
                ? "border-[#0F172A] font-semibold text-[#0F172A]"
                : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
            }`}
          >
            Lịch sử phiên cào
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("health")}
            className={`ml-5 border-b-2 px-1 pb-2.5 text-sm transition-colors ${
              activeTab === "health"
                ? "border-[#0F172A] font-semibold text-[#0F172A]"
                : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
            }`}
          >
            Sức khỏe nguồn dữ liệu ({healthData?.total_active_sources ?? 11})
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("anomalies")}
            className={`ml-5 border-b-2 px-1 pb-2.5 text-sm transition-colors ${
              activeTab === "anomalies"
                ? "border-[#0F172A] font-semibold text-[#0F172A]"
                : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
            }`}
          >
            Cảnh báo bất thường ({summary?.open_anomalies ?? 0})
          </button>
        </div>

        {/* Tab 1: Crawl Runs */}
        {activeTab === "runs" && (
          <section className="rounded border border-[#E2E8F0] bg-white">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC] text-xs font-medium uppercase tracking-wider text-[#475569]">
                    <th className="px-5 py-3">Mã phiên</th>
                    <th className="px-4 py-3">Phạm vi</th>
                    <th className="px-4 py-3">Kích hoạt</th>
                    <th className="px-4 py-3">Trạng thái</th>
                    <th className="px-4 py-3 text-right">OK / Lỗi</th>
                    <th className="px-4 py-3 text-right">Xác thực</th>
                    <th className="px-4 py-3 text-right">Thời lượng</th>
                    <th className="px-5 py-3 text-right">Thời điểm (UTC+7)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E2E8F0]">
                  {!runsData || runsData.items.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="px-5 py-8 text-center text-sm text-[#475569]">
                        Chưa có phiên thu thập nào được ghi nhận.
                      </td>
                    </tr>
                  ) : (
                    runsData.items.map((run) => (
                      <tr
                        key={run.id}
                        onClick={() => setSelectedRunId(run.id)}
                        className="cursor-pointer transition-colors hover:bg-[#F8FAFC]"
                      >
                        <td className="font-mono px-5 py-3.5 text-xs font-medium text-[#0F172A]">
                          {run.run_code}
                        </td>
                        <td className="px-4 py-3.5 text-xs font-medium text-[#0F172A]">
                          {formatScopeLabel(run.scope_category)}
                        </td>
                        <td className="px-4 py-3.5 text-xs text-[#475569]">
                          {formatTriggerLabel(run.trigger_type)}
                        </td>
                        <td className="px-4 py-3.5">
                          <RunStatusBadge status={run.status} />
                        </td>
                        <td className="tabular-nums px-4 py-3.5 text-right text-xs">
                          <span className="font-medium text-[#0F172A]">
                            {run.pages_fetched_ok}
                          </span>
                          <span className="mx-1 text-[#CBD5E1]">/</span>
                          <span
                            className={
                              run.pages_fetched_failed > 0 ? "font-medium text-rose-700" : "text-[#475569]"
                            }
                          >
                            {run.pages_fetched_failed}
                          </span>
                        </td>
                        <td className="tabular-nums px-4 py-3.5 text-right text-xs font-semibold text-emerald-700">
                          {formatCount(run.records_verified)}
                        </td>
                        <td className="tabular-nums px-4 py-3.5 text-right text-xs text-[#475569]">
                          {formatDuration(run.duration_ms)}
                        </td>
                        <td className="tabular-nums px-5 py-3.5 text-right text-xs text-[#475569]">
                          {formatVnDateTime(run.started_at_vn)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>

            {totalRuns > 0 && (
              <div className="flex flex-wrap items-center justify-between gap-3 border-t border-[#E2E8F0] px-5 py-3 text-xs text-[#475569]">
                <span className="tabular-nums">
                  Trang {currentPage} / {totalPages} · Tổng {formatCount(totalRuns)} phiên
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    disabled={runsOffset === 0 || refreshing}
                    onClick={() => void handleRunsPageChange(Math.max(0, runsOffset - RUNS_PAGE_SIZE))}
                    className="inline-flex items-center gap-1 rounded border border-[#E2E8F0] bg-white px-2.5 py-1 font-medium text-[#0F172A] hover:bg-[#F8FAFC] disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <ChevronLeft className="h-3.5 w-3.5" strokeWidth={1.75} />
                    Trước
                  </button>
                  <button
                    type="button"
                    disabled={runsOffset + RUNS_PAGE_SIZE >= totalRuns || refreshing}
                    onClick={() => void handleRunsPageChange(runsOffset + RUNS_PAGE_SIZE)}
                    className="inline-flex items-center gap-1 rounded border border-[#E2E8F0] bg-white px-2.5 py-1 font-medium text-[#0F172A] hover:bg-[#F8FAFC] disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    Sau
                    <ChevronRight className="h-3.5 w-3.5" strokeWidth={1.75} />
                  </button>
                </div>
              </div>
            )}
          </section>
        )}

        {/* Tab 2: Sources Health */}
        {activeTab === "health" && (
          <section className="rounded border border-[#E2E8F0] bg-white">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#E2E8F0] px-5 py-3 text-xs text-[#475569]">
              <span>
                Đang hoạt động:{" "}
                <strong className="font-semibold text-[#0F172A]">
                  {healthData?.healthy_count ?? 0}/{healthData?.total_active_sources ?? 0} ổn định
                </strong>
              </span>
              <span>Chu kỳ thống kê: {healthData?.window_days ?? 7} ngày gần nhất</span>
            </div>

            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC] text-xs font-medium uppercase tracking-wider text-[#475569]">
                    <th className="px-5 py-3">Nguồn dữ liệu</th>
                    <th className="px-4 py-3">Nhóm</th>
                    <th className="px-4 py-3">Trạng thái</th>
                    <th className="px-4 py-3 text-right">Tỷ lệ thành công (7d)</th>
                    <th className="px-4 py-3 text-right">Độ trễ TB</th>
                    <th className="px-5 py-3 text-right">Thành công gần nhất (UTC+7)</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-[#E2E8F0]">
                  {!healthData || healthData.items.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="px-5 py-8 text-center text-sm text-[#475569]">
                        Chưa có thông tin sức khỏe nguồn.
                      </td>
                    </tr>
                  ) : (
                    healthData.items.map((src) => (
                      <tr key={src.source_code} className="hover:bg-[#F8FAFC]">
                        <td className="px-5 py-3.5">
                          <p className="font-medium text-[#0F172A]">{src.source_name}</p>
                          <p className="font-mono mt-0.5 text-xs text-[#475569]">
                            {src.source_code} ·{" "}
                            {src.tier === "PRIMARY_OFFICIAL" ? "Chính thống" : "Đối chứng"}
                          </p>
                        </td>
                        <td className="px-4 py-3.5 text-xs font-medium text-[#0F172A]">
                          {formatDomainLabel(src.category)}
                        </td>
                        <td className="px-4 py-3.5">
                          <SourceHealthBadge status={src.current_status} />
                        </td>
                        <td className="tabular-nums px-4 py-3.5 text-right text-xs">
                          <span className="font-semibold text-[#0F172A]">
                            {formatPercent(src.success_rate_7d_pct, 1)}
                          </span>
                          <span className="ml-1.5 text-[#475569]">
                            ({src.success_checks_7d}/{src.total_checks_7d})
                          </span>
                        </td>
                        <td className="tabular-nums px-4 py-3.5 text-right text-xs text-[#475569]">
                          {formatLatencyMs(src.avg_latency_ms_7d)}
                        </td>
                        <td className="tabular-nums px-5 py-3.5 text-right text-xs text-[#475569]">
                          {formatVnDateTime(src.last_success_at_vn)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </section>
        )}

        {/* Tab 3: Anomalies */}
        {activeTab === "anomalies" && (
          <section className="rounded border border-[#E2E8F0] bg-white">
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#E2E8F0] px-5 py-3">
              <div className="inline-flex items-center gap-1.5 text-xs">
                {(
                  [
                    { code: "OPEN", label: "Đang mở" },
                    { code: "RESOLVED", label: "Đã xử lý" },
                    { code: "ALL", label: "Tất cả" },
                  ] as const
                ).map((item) => (
                  <button
                    key={item.code}
                    type="button"
                    onClick={() => void handleAnomalyFilterChange(item.code)}
                    className={`rounded border px-2.5 py-1 transition-colors ${
                      anomalyFilter === item.code
                        ? "border-[#0F172A] bg-[#0F172A] font-medium text-white"
                        : "border-[#E2E8F0] bg-white text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
              <span className="tabular-nums text-xs text-[#475569]">
                Tổng: {formatCount(anomaliesData?.total ?? 0)} cảnh báo
              </span>
            </div>

            {!anomaliesData || anomaliesData.items.length === 0 ? (
              <div className="flex flex-col items-center justify-center px-5 py-12 text-center">
                <ShieldCheck className="h-6 w-6 text-emerald-700" strokeWidth={1.75} />
                <p className="mt-2 text-sm font-medium text-[#0F172A]">
                  Không có cảnh báo bất thường nào
                </p>
                <p className="mt-1 text-xs text-[#475569]">
                  Toàn bộ dữ liệu giá gần đây đều vượt qua 6 tầng kiểm chứng an toàn.
                </p>
              </div>
            ) : (
              <div className="divide-y divide-[#E2E8F0]">
                {anomaliesData.items.map((alert) => (
                  <div key={alert.id} className="space-y-1.5 px-5 py-4 text-sm">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex flex-wrap items-center gap-2">
                        <AlertStatusBadge status={alert.status} />
                        <span className="font-medium text-[#0F172A]">
                          {formatAlertTypeLabel(alert.alert_type)}
                        </span>
                        <span className="rounded border border-[#E2E8F0] bg-[#F8FAFC] px-1.5 py-0.5 text-[11px] font-medium text-[#475569]">
                          Mức độ: {formatSeverityLabel(alert.severity)}
                        </span>
                      </div>
                      <span className="tabular-nums text-xs text-[#475569]">
                        {formatVnDateTime(alert.created_at_vn)} (UTC+7)
                      </span>
                    </div>

                    <p className="text-xs text-[#0F172A]">{alert.message}</p>

                    <div className="flex flex-wrap items-center gap-4 text-xs text-[#475569]">
                      {alert.source_name && <span>Nguồn: {alert.source_name}</span>}
                      {alert.crawl_run_id && alert.run_code && (
                        <button
                          type="button"
                          onClick={() => setSelectedRunId(alert.crawl_run_id)}
                          className="font-mono text-[#0F172A] underline underline-offset-4"
                        >
                          {alert.run_code}
                        </button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>
        )}
      </main>

      <RunDetailDrawer runId={selectedRunId} onClose={() => setSelectedRunId(null)} />

      <ManualCrawlModal
        open={manualModalOpen}
        onClose={() => setManualModalOpen(false)}
        onCompleted={() => void refreshAll(0, anomalyFilter)}
        onInspectRun={(runId) => {
          setActiveTab("runs");
          setSelectedRunId(runId);
        }}
      />
    </div>
  );
}

