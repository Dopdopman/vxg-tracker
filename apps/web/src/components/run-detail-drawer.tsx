"use client";

import { useEffect, useState } from "react";
import { Check, Copy, ExternalLink, RefreshCw, X } from "lucide-react";
import { fetchCrawlRunDetail } from "@/lib/api";
import {
  formatBytes,
  formatCount,
  formatDomainLabel,
  formatDuration,
  formatLatencyMs,
  formatScopeLabel,
  formatTriggerLabel,
  formatVnDateTimeFull,
} from "@/lib/format";
import type { CrawlRunDetailResponse } from "@/lib/types";
import { FetchStatusBadge, RunStatusBadge } from "./status-badge";

interface RunDetailDrawerProps {
  runId: string | null;
  onClose: () => void;
}

export function RunDetailDrawer({ runId, onClose }: RunDetailDrawerProps) {
  const [detail, setDetail] = useState<CrawlRunDetailResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedShaId, setCopiedShaId] = useState<string | null>(null);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    if (!runId) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [runId, onClose]);

  useEffect(() => {
    if (!runId) {
      setDetail(null);
      setError(null);
      return;
    }

    let active = true;
    setLoading(true);
    setError(null);
    setCopiedShaId(null);

    fetchCrawlRunDetail(runId)
      .then((res) => {
        if (active) {
          setDetail(res);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setError(err instanceof Error ? err.message : "Không thể tải chi tiết phiên thu thập.");
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [runId, retryTick]);

  if (!runId) {
    return null;
  }

  const handleCopySha = async (logId: string, sha: string) => {
    try {
      await navigator.clipboard.writeText(sha);
      setCopiedShaId(logId);
      window.setTimeout(() => setCopiedShaId((cur) => (cur === logId ? null : cur)), 2000);
    } catch {
      setCopiedShaId(null);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-900/25"
      role="dialog"
      aria-modal="true"
      aria-labelledby="run-detail-drawer-title"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-2xl flex-col border-l border-[#E2E8F0] bg-white"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[#E2E8F0] px-5 py-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-[#475569]">
              Nhật ký từng nguồn dữ liệu
            </p>
            <h2
              id="run-detail-drawer-title"
              className="mt-0.5 text-base font-semibold text-[#0F172A]"
            >
              Chi tiết phiên thu thập
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng chi tiết phiên cào"
            className="rounded border border-[#E2E8F0] p-1.5 text-[#475569] transition-colors hover:bg-[#F8FAFC] hover:text-[#0F172A]"
          >
            <X className="h-4 w-4" strokeWidth={1.75} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {loading && (
            <div className="space-y-4">
              <div className="h-24 animate-pulse rounded border border-[#E2E8F0] bg-[#F8FAFC]" />
              <div className="h-40 animate-pulse rounded border border-[#E2E8F0] bg-[#F8FAFC]" />
            </div>
          )}

          {!loading && error && (
            <div className="rounded border border-rose-200 bg-rose-50/60 p-4 text-sm text-rose-800">
              <p className="font-medium">Không tải được chi tiết phiên chạy</p>
              <p className="mt-1 text-xs text-rose-700">{error}</p>
              <button
                type="button"
                onClick={() => setRetryTick((n) => n + 1)}
                className="mt-3 inline-flex items-center gap-1.5 rounded border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-800 hover:bg-rose-50"
              >
                <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />
                Thử lại
              </button>
            </div>
          )}

          {!loading && !error && detail && (
            <div className="space-y-6">
              <section className="rounded border border-[#E2E8F0] bg-[#F8FAFC] p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <p className="font-mono text-sm font-semibold text-[#0F172A]">
                      {detail.run_code}
                    </p>
                    <p className="mt-0.5 text-xs text-[#475569]">
                      Phạm vi: {formatScopeLabel(detail.scope_category)} · Kích hoạt:{" "}
                      {formatTriggerLabel(detail.trigger_type)}
                    </p>
                  </div>
                  <RunStatusBadge status={detail.status} />
                </div>

                <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-[#E2E8F0] pt-3 text-xs sm:grid-cols-4">
                  <div>
                    <dt className="text-[#475569]">Trang OK / Lỗi</dt>
                    <dd className="tabular-nums mt-0.5 font-semibold text-[#0F172A]">
                      {formatCount(detail.pages_fetched_ok)} / {formatCount(detail.pages_fetched_failed)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[#475569]">Bản ghi xác thực</dt>
                    <dd className="tabular-nums mt-0.5 font-semibold text-emerald-700">
                      {formatCount(detail.records_verified)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[#475569]">Bản ghi bị chặn</dt>
                    <dd className="tabular-nums mt-0.5 font-semibold text-[#0F172A]">
                      {formatCount(detail.records_flagged)}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-[#475569]">Thời lượng</dt>
                    <dd className="tabular-nums mt-0.5 font-semibold text-[#0F172A]">
                      {formatDuration(detail.duration_ms)}
                    </dd>
                  </div>
                </dl>

                <div className="tabular-nums mt-3 space-y-1 border-t border-[#E2E8F0] pt-2.5 text-xs text-[#475569]">
                  <p>Bắt đầu: {formatVnDateTimeFull(detail.started_at_vn)}</p>
                  <p>Kết thúc: {formatVnDateTimeFull(detail.finished_at_vn)}</p>
                </div>

                {detail.error_summary && (
                  <p className="mt-3 rounded border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800">
                    {detail.error_summary}
                  </p>
                )}
              </section>

              <section className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
                    Nhật ký từng nguồn ({detail.source_logs.length})
                  </h3>
                </div>

                {detail.source_logs.length === 0 ? (
                  <p className="rounded border border-[#E2E8F0] bg-white px-4 py-6 text-center text-sm text-[#475569]">
                    Phiên này chưa ghi nhận nhật ký nguồn nào.
                  </p>
                ) : (
                  <div className="divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-white">
                    {detail.source_logs.map((log) => (
                      <div key={log.id} className="space-y-2.5 p-4 text-xs">
                        <div className="flex flex-wrap items-start justify-between gap-2">
                          <div>
                            <p className="text-sm font-medium text-[#0F172A]">
                              {log.source_name}
                            </p>
                            <p className="mt-0.5 text-[#475569]">
                              <span className="font-mono">{log.source_code}</span> ·{" "}
                              {formatDomainLabel(log.source_category)}
                            </p>
                          </div>
                          <FetchStatusBadge status={log.fetch_status} />
                        </div>

                        <a
                          href={log.final_url || log.request_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="inline-flex items-center gap-1 break-all text-[#475569] underline decoration-[#CBD5E1] underline-offset-4 hover:text-[#0F172A]"
                        >
                          <span>{log.final_url || log.request_url}</span>
                          <ExternalLink className="h-3 w-3 shrink-0" strokeWidth={1.75} />
                        </a>

                        <div className="tabular-nums flex flex-wrap items-center gap-x-4 gap-y-1 text-[#475569]">
                          <span>
                            HTTP:{" "}
                            <strong className="font-medium text-[#0F172A]">
                              {log.http_status ?? "—"}
                            </strong>
                          </span>
                          <span>
                            Tier:{" "}
                            <strong className="font-medium text-[#0F172A]">{log.tier_used}</strong>
                          </span>
                          <span>
                            Độ trễ:{" "}
                            <strong className="font-medium text-[#0F172A]">
                              {formatLatencyMs(log.latency_ms)}
                            </strong>
                          </span>
                          <span>
                            Dung lượng:{" "}
                            <strong className="font-medium text-[#0F172A]">
                              {formatBytes(log.bytes_received)}
                            </strong>
                          </span>
                          <span>
                            Bóc/Xác thực:{" "}
                            <strong className="font-medium text-[#0F172A]">
                              {log.records_extracted}/{log.records_verified}
                            </strong>
                          </span>
                        </div>

                        {log.content_sha256 && (
                          <div className="flex flex-wrap items-center justify-between gap-2 rounded bg-[#F8FAFC] px-2.5 py-1.5">
                            <span className="font-mono truncate text-[11px] text-[#475569]">
                              SHA-256: {log.content_sha256}
                            </span>
                            <button
                              type="button"
                              onClick={() => handleCopySha(log.id, log.content_sha256 ?? "")}
                              className="inline-flex shrink-0 items-center gap-1 text-[11px] font-medium text-[#0F172A] hover:underline"
                            >
                              {copiedShaId === log.id ? (
                                <>
                                  <Check className="h-3 w-3 text-emerald-700" strokeWidth={1.75} />
                                  Đã sao chép
                                </>
                              ) : (
                                <>
                                  <Copy className="h-3 w-3" strokeWidth={1.75} />
                                  Sao chép
                                </>
                              )}
                            </button>
                          </div>
                        )}

                        {log.error_message && (
                          <p className="rounded border border-rose-200 bg-rose-50 px-2.5 py-1.5 text-rose-800">
                            {log.error_code ? `${log.error_code}: ` : ""}
                            {log.error_message}
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

