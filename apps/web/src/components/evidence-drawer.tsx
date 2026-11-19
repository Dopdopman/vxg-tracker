"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { ArrowUpRight, Check, Copy, ExternalLink, RefreshCw, ShieldCheck, X } from "lucide-react";
import { fetchPriceEvidence } from "@/lib/api";
import {
  formatBrandLabel,
  formatBytes,
  formatPercent,
  formatVnDate,
  formatVnDateTimeFull,
  formatVnd,
} from "@/lib/format";
import type { PriceEvidenceResponse } from "@/lib/types";
import { VerificationBadge } from "./status-badge";

interface EvidenceDrawerProps {
  recordId: string | null;
  goldUnit: "LUONG" | "CHI";
  onClose: () => void;
}

const LAYER_LABELS: Record<string, string> = {
  "1_structure_types": "Kiểm tra cấu trúc & kiểu dữ liệu",
  "2_positive_non_null": "Giá trị dương & đầy đủ trường bắt buộc",
  "3_business_logic": "Quy tắc nghiệp vụ & biên độ mua/bán",
  "4_reasonable_bounds": "Nằm trong biên độ an toàn thị trường",
  "5_historical_delta": "So sánh biến động với kỳ xác thực trước",
  "6_timezone_idempotency": "Chuẩn hóa múi giờ UTC+7 & chống trùng lặp",
};

export function EvidenceDrawer({ recordId, goldUnit, onClose }: EvidenceDrawerProps) {
  const [data, setData] = useState<PriceEvidenceResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copiedSha, setCopiedSha] = useState(false);
  const [retryTick, setRetryTick] = useState(0);

  useEffect(() => {
    if (!recordId) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [recordId, onClose]);

  useEffect(() => {
    if (!recordId) {
      setData(null);
      setError(null);
      return;
    }

    let active = true;
    setLoading(true);
    setError(null);
    setCopiedSha(false);

    fetchPriceEvidence(recordId)
      .then((res) => {
        if (active) {
          setData(res);
          setLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (active) {
          setError(
            err instanceof Error ? err.message : "Không thể tải bằng chứng kiểm chứng."
          );
          setLoading(false);
        }
      });

    return () => {
      active = false;
    };
  }, [recordId, retryTick]);

  if (!recordId) {
    return null;
  }

  const handleCopySha = async () => {
    if (!data?.content_sha256) {
      return;
    }
    try {
      await navigator.clipboard.writeText(data.content_sha256);
      setCopiedSha(true);
      window.setTimeout(() => setCopiedSha(false), 2000);
    } catch {
      setCopiedSha(false);
    }
  };

  const goldDivisor = data?.domain === "GOLD" && goldUnit === "CHI" ? 10 : 1;
  const layerEntries = Object.entries(
    ((data?.validation_notes?.layers as Record<string, string> | undefined) ?? {})
  ).sort(([a], [b]) => a.localeCompare(b));

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-900/25"
      role="dialog"
      aria-modal="true"
      aria-labelledby="evidence-drawer-title"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-lg flex-col border-l border-[#E2E8F0] bg-white"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[#E2E8F0] px-5 py-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-[#475569]">
              Hồ sơ truy xuất nguồn gốc
            </p>
            <h2
              id="evidence-drawer-title"
              className="mt-0.5 text-base font-semibold text-[#0F172A]"
            >
              Bằng chứng &amp; Kiểm chứng dữ liệu
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng ngăn kéo bằng chứng"
            className="rounded border border-[#E2E8F0] p-1.5 text-[#475569] transition-colors hover:bg-[#F8FAFC] hover:text-[#0F172A]"
          >
            <X className="h-4 w-4" strokeWidth={1.75} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {loading && (
            <div className="space-y-4">
              <div className="h-20 animate-pulse rounded border border-[#E2E8F0] bg-[#F8FAFC]" />
              <div className="h-28 animate-pulse rounded border border-[#E2E8F0] bg-[#F8FAFC]" />
              <div className="h-32 animate-pulse rounded border border-[#E2E8F0] bg-[#F8FAFC]" />
            </div>
          )}

          {!loading && error && (
            <div className="rounded border border-rose-200 bg-rose-50/60 p-4 text-sm text-rose-800">
              <p className="font-medium">Không tải được thông tin bằng chứng</p>
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

          {!loading && !error && data && (
            <div className="space-y-6 text-sm">
              {/* Tóm tắt mặt hàng & mức giá */}
              <section className="rounded border border-[#E2E8F0] bg-[#F8FAFC] p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-xs font-medium text-[#475569]">
                      {formatBrandLabel(data.brand)} · {data.location_name}
                    </p>
                    <h3 className="mt-0.5 text-base font-semibold text-[#0F172A]">
                      {data.commodity_name}
                    </h3>
                  </div>
                  <VerificationBadge status={data.verification_status} />
                </div>

                {data.buy_price !== null && data.sell_price !== null ? (
                  <div className="mt-4 grid grid-cols-2 gap-3 border-t border-[#E2E8F0] pt-3">
                    <div>
                      <p className="text-xs text-[#475569]">
                        Mua vào ({goldUnit === "CHI" ? "VND/chỉ" : "VND/lượng"})
                      </p>
                      <p className="tabular-nums mt-0.5 text-base font-semibold text-[#0F172A]">
                        {formatVnd(data.buy_price, { divisor: goldDivisor })}
                      </p>
                    </div>
                    <div>
                      <p className="text-xs text-[#475569]">
                        Bán ra ({goldUnit === "CHI" ? "VND/chỉ" : "VND/lượng"})
                      </p>
                      <p className="tabular-nums mt-0.5 text-base font-semibold text-[#0F172A]">
                        {formatVnd(data.sell_price, { divisor: goldDivisor })}
                      </p>
                    </div>
                  </div>
                ) : (
                  <div className="mt-4 border-t border-[#E2E8F0] pt-3">
                    <p className="text-xs text-[#475569]">Giá bán lẻ niêm yết</p>
                    <p className="tabular-nums mt-0.5 text-base font-semibold text-[#0F172A]">
                      {formatVnd(data.retail_price)}
                    </p>
                  </div>
                )}

                {data.cross_check_delta_pct !== null && (
                  <p className="tabular-nums mt-3 border-t border-[#E2E8F0] pt-2.5 text-xs text-[#475569]">
                    Độ lệch đối chiếu chéo:{" "}
                    <span className="font-semibold text-emerald-700">
                      {formatPercent(data.cross_check_delta_pct)}
                    </span>
                  </p>
                )}
              </section>

              {/* Nguồn chính thống */}
              <section className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
                  Nguồn công bố chính thức
                </h4>
                <div className="rounded border border-[#E2E8F0] bg-white p-3.5">
                  <p className="font-medium text-[#0F172A]">{data.source_name}</p>
                  <p className="font-mono mt-0.5 text-xs text-[#475569]">{data.source_code}</p>
                  <a
                    href={data.source_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-2.5 inline-flex items-center gap-1.5 text-xs font-medium text-[#0F172A] underline decoration-[#CBD5E1] underline-offset-4 hover:decoration-[#0F172A]"
                  >
                    <span className="break-all">{data.source_url}</span>
                    <ExternalLink className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
                  </a>
                </div>
              </section>

              {/* Nguồn đối chiếu chéo */}
              {data.cross_check_sources.length > 0 && (
                <section className="space-y-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
                    Nguồn đối chiếu chéo
                  </h4>
                  <div className="divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-white">
                    {data.cross_check_sources.map((src) => (
                      <div key={src.source_code} className="p-3.5">
                        <div className="flex items-center justify-between gap-2">
                          <p className="font-medium text-[#0F172A]">{src.source_name}</p>
                          {src.delta_pct !== null && (
                            <span className="tabular-nums text-xs font-medium text-emerald-700">
                              Lệch {formatPercent(src.delta_pct)}
                            </span>
                          )}
                        </div>
                        <a
                          href={src.source_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="mt-1.5 inline-flex items-center gap-1.5 text-xs text-[#475569] underline decoration-[#CBD5E1] underline-offset-4 hover:text-[#0F172A]"
                        >
                          <span className="break-all">{src.source_url}</span>
                          <ExternalLink className="h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
                        </a>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              {/* Mốc thời gian UTC+7 */}
              <section className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
                  Mốc thời gian (UTC+7)
                </h4>
                <dl className="divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-white text-xs">
                  <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                    <dt className="text-[#475569]">Ngày hiệu lực</dt>
                    <dd className="tabular-nums font-medium text-[#0F172A]">
                      {formatVnDate(data.effective_date)}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                    <dt className="text-[#475569]">Thời điểm nguồn công bố</dt>
                    <dd className="tabular-nums font-medium text-[#0F172A]">
                      {data.published_at_vn
                        ? formatVnDateTimeFull(data.published_at_vn)
                        : "Không công bố giờ trên bảng gốc"}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                    <dt className="text-[#475569]">Thời điểm hệ thống thu thập</dt>
                    <dd className="tabular-nums font-medium text-[#0F172A]">
                      {formatVnDateTimeFull(data.fetched_at_vn)}
                    </dd>
                  </div>
                  <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                    <dt className="text-[#475569]">Xác nhận gần nhất</dt>
                    <dd className="tabular-nums font-medium text-[#0F172A]">
                      {formatVnDateTimeFull(data.last_verified_at_vn)}
                    </dd>
                  </div>
                </dl>
              </section>

              {/* Kết quả bộ lọc an toàn */}
              {layerEntries.length > 0 && (
                <section className="space-y-2">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
                    Kết quả kiểm chứng 6 tầng
                  </h4>
                  <ul className="divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-white text-xs">
                    {layerEntries.map(([key, status]) => (
                      <li key={key} className="flex items-center justify-between gap-3 px-3.5 py-2">
                        <span className="flex items-center gap-2 text-[#0F172A]">
                          <ShieldCheck
                            className="h-3.5 w-3.5 shrink-0 text-emerald-700"
                            strokeWidth={1.75}
                          />
                          {LAYER_LABELS[key] ?? key}
                        </span>
                        <span className="font-medium text-emerald-700">
                          {status === "PASSED" ? "Đạt" : status}
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}

              {/* Truy vết kiểm toán & SHA-256 */}
              <section className="space-y-2">
                <h4 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
                  Dấu vân tay kiểm toán
                </h4>
                <div className="space-y-3 rounded border border-[#E2E8F0] bg-white p-3.5 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-[#475569]">Mã phiên thu thập</span>
                    <Link
                      href={`/audit?runId=${data.run_id}`}
                      onClick={onClose}
                      className="font-mono inline-flex items-center gap-1 font-medium text-[#0F172A] underline decoration-[#CBD5E1] underline-offset-4 hover:decoration-[#0F172A]"
                    >
                      {data.run_code}
                      <ArrowUpRight className="h-3.5 w-3.5" strokeWidth={1.75} />
                    </Link>
                  </div>

                  <div className="border-t border-[#E2E8F0] pt-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[#475569]">Mã băm SHA-256 bản lưu gốc</span>
                      <button
                        type="button"
                        onClick={handleCopySha}
                        className="inline-flex items-center gap-1 rounded border border-[#E2E8F0] bg-[#F8FAFC] px-2 py-1 text-[11px] font-medium text-[#0F172A] hover:bg-white"
                      >
                        {copiedSha ? (
                          <>
                            <Check className="h-3 w-3 text-emerald-700" strokeWidth={1.75} />
                            <span>Đã sao chép</span>
                          </>
                        ) : (
                          <>
                            <Copy className="h-3 w-3" strokeWidth={1.75} />
                            <span>Sao chép</span>
                          </>
                        )}
                      </button>
                    </div>
                    <p className="font-mono mt-1.5 break-all rounded bg-[#F8FAFC] px-2.5 py-2 text-[11px] text-[#0F172A]">
                      {data.content_sha256}
                    </p>
                  </div>

                  <div className="tabular-nums flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[#E2E8F0] pt-2.5 text-[#475569]">
                    <span>HTTP {data.http_status ?? "—"}</span>
                    <span>Chiến lược: {data.fetch_strategy_used}</span>
                    <span>Dung lượng: {formatBytes(data.snapshot_byte_size)}</span>
                  </div>
                </div>
              </section>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

