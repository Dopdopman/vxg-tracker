"use client";

import { useEffect } from "react";
import { X } from "lucide-react";
import {
  formatCount,
  formatHorizonLabel,
  formatPercent,
  formatVnDateTimeFull,
  formatVnd,
} from "@/lib/format";
import type { ForecastItem, GoldWorldSpreadItem } from "@/lib/types";

interface ModelBacktestDrawerProps {
  open: boolean;
  onClose: () => void;
  forecast: ForecastItem;
  goldDivisor: number;
  unitLabel: string;
  goldSpread: GoldWorldSpreadItem | null;
  disclaimer: string;
}

export function ModelBacktestDrawer({
  open,
  onClose,
  forecast,
  goldDivisor,
  unitLabel,
  goldSpread,
  disclaimer,
}: ModelBacktestDrawerProps) {
  useEffect(() => {
    if (!open) {
      return;
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  const driversMeta = forecast.drivers_json;
  const modelFamily =
    typeof driversMeta.model_family === "string"
      ? driversMeta.model_family
      : forecast.model_version;
  const targetSchedule =
    typeof driversMeta.target_schedule === "string" ? driversMeta.target_schedule : null;
  const backtest = forecast.backtest_summary;

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-slate-900/25"
      role="dialog"
      aria-modal="true"
      aria-labelledby="model-drawer-title"
      onClick={onClose}
    >
      <div
        className="flex h-full w-full max-w-lg flex-col border-l border-[#E2E8F0] bg-white"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[#E2E8F0] px-5 py-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-[#475569]">
              Minh bạch định lượng &amp; Kiểm thử
            </p>
            <h2 id="model-drawer-title" className="mt-0.5 text-base font-semibold text-[#0F172A]">
              Chi tiết mô hình &amp; Kiểm thử ngược
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng chi tiết mô hình"
            className="rounded border border-[#E2E8F0] p-1.5 text-[#475569] transition-colors hover:bg-[#F8FAFC] hover:text-[#0F172A]"
          >
            <X className="h-4 w-4" strokeWidth={1.75} />
          </button>
        </div>

        <div className="flex-1 space-y-6 overflow-y-auto px-5 py-5 text-sm">
          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
              1. Cấu hình mô hình &amp; Zero Look-Ahead
            </h3>
            <dl className="divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-[#F8FAFC] text-xs">
              <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                <dt className="text-[#475569]">Phiên bản mô hình</dt>
                <dd className="font-mono font-semibold text-[#0F172A]">
                  {forecast.model_version}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                <dt className="text-[#475569]">Kiến trúc ước lượng</dt>
                <dd className="text-right font-medium text-[#0F172A]">{modelFamily}</dd>
              </div>
              <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                <dt className="text-[#475569]">Kỳ hạn dự báo</dt>
                <dd className="font-medium text-[#0F172A]">
                  {targetSchedule ?? formatHorizonLabel(forecast.horizon_type)}
                </dd>
              </div>
              <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                <dt className="text-[#475569]">Mốc khóa dữ liệu đầu vào</dt>
                <dd className="tabular-nums font-medium text-[#0F172A]">
                  {forecast.feature_cutoff_vn
                    ? formatVnDateTimeFull(forecast.feature_cutoff_vn)
                    : "Khóa nghiêm ngặt <= ngày dự báo"}
                </dd>
              </div>
              {forecast.high_volatility_flag && (
                <div className="flex items-center justify-between gap-4 px-3.5 py-2.5">
                  <dt className="text-amber-800">Nới rộng biên độ rủi ro tin tức</dt>
                  <dd className="tabular-nums font-semibold text-amber-800">
                    +{formatPercent(forecast.band_widening_pct, 1)}
                  </dd>
                </div>
              )}
            </dl>
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
              2. Trọng số đặc trưng &amp; Động lực giải thích
            </h3>
            {forecast.feature_drivers.length === 0 ? (
              <p className="rounded border border-[#E2E8F0] bg-white px-3.5 py-3 text-xs text-[#475569]">
                Chưa có bảng trọng số đặc trưng (mã đang ở trạng thái chưa đủ dữ liệu hoặc dùng hệ
                số truyền dẫn chi phí cơ bản).
              </p>
            ) : (
              <div className="divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-white text-xs">
                {forecast.feature_drivers.map((drv) => {
                  const dirLabel =
                    drv.direction === "UP"
                      ? "Tác động cùng chiều (+)"
                      : drv.direction === "DOWN"
                        ? "Tác động nghịch chiều (-)"
                        : "Trung tính";
                  return (
                    <div key={drv.key} className="space-y-1.5 px-3.5 py-2.5">
                      <div className="flex items-center justify-between gap-3">
                        <span className="font-medium text-[#0F172A]">{drv.label_vi}</span>
                        <span className="tabular-nums font-semibold text-[#0F172A]">
                          {formatPercent(drv.weight_pct, 1)}
                        </span>
                      </div>
                      <div className="h-1.5 w-full overflow-hidden rounded bg-[#F1F5F9]">
                        <div
                          className="h-full bg-[#0F172A]"
                          style={{ width: `${Math.min(100, Math.max(2, drv.weight_pct))}%` }}
                        />
                      </div>
                      <div className="flex items-center justify-between text-[11px] text-[#475569]">
                        <span>{dirLabel}</span>
                        <span className="tabular-nums font-mono">
                          Giá trị đầu vào: {drv.value !== null ? drv.value : "—"}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </section>

          <section className="space-y-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
              3. Kiểm thử ngược Walk-Forward vs Naive Baseline
            </h3>
            {!backtest ? (
              <p className="rounded border border-[#E2E8F0] bg-white px-3.5 py-3 text-xs text-[#475569]">
                Chưa tích lũy đủ kỳ out-of-sample để thống kê Walk-Forward.
              </p>
            ) : (
              <div className="overflow-hidden rounded border border-[#E2E8F0] bg-white text-xs">
                <table className="w-full border-collapse text-left">
                  <thead>
                    <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC] text-[11px] uppercase tracking-wider text-[#475569]">
                      <th className="px-3.5 py-2.5">Chỉ số kiểm thử</th>
                      <th className="px-3.5 py-2.5 text-right">Mô hình VXG</th>
                      <th className="px-3.5 py-2.5 text-right">Naive Baseline</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E2E8F0]">
                    <tr>
                      <td className="px-3.5 py-2.5 text-[#475569]">
                        Sai số tuyệt đối TB (MAE · {unitLabel})
                      </td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right font-semibold text-[#0F172A]">
                        {formatVnd(backtest.mae_vnd, { divisor: goldDivisor })}
                      </td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right text-[#475569]">
                        {formatVnd(backtest.naive_mae_vnd, { divisor: goldDivisor })}
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3.5 py-2.5 text-[#475569]">Sai số phần trăm TB (MAPE)</td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right font-semibold text-[#0F172A]">
                        {formatPercent(backtest.mape_pct, 2)}
                      </td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right text-[#475569]">
                        {formatPercent(backtest.naive_mape_pct, 2)}
                      </td>
                    </tr>
                    <tr>
                      <td className="px-3.5 py-2.5 text-[#475569]">Tỷ lệ đoán đúng chiều</td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right font-semibold text-emerald-700">
                        {formatPercent(backtest.directional_accuracy_pct, 1)}
                      </td>
                      <td className="px-3.5 py-2.5 text-right text-[#475569]">—</td>
                    </tr>
                    <tr>
                      <td className="px-3.5 py-2.5 text-[#475569]">Độ phủ biên tin cậy 80%</td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right font-semibold text-[#0F172A]">
                        {formatPercent(backtest.interval_coverage_pct, 1)}
                      </td>
                      <td className="px-3.5 py-2.5 text-right text-[#475569]">—</td>
                    </tr>
                    <tr>
                      <td className="px-3.5 py-2.5 text-[#475569]">Số kỳ đánh giá độc lập</td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right font-medium text-[#0F172A]">
                        {formatCount(backtest.sample_count)} kỳ
                      </td>
                      <td className="tabular-nums px-3.5 py-2.5 text-right text-[#475569]">
                        {formatCount(backtest.sample_count)} kỳ
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {forecast.domain === "GOLD" && (
            <section className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-[#475569]">
                4. Công thức quy đổi Vàng thế giới &amp; Tỷ giá
              </h3>
              <div className="rounded border border-[#E2E8F0] bg-[#F8FAFC] p-3.5 text-xs text-[#475569]">
                <p className="font-mono text-[11px] font-medium text-[#0F172A]">
                  Giá quy đổi = XAU/USD × (37,5 / 31,1034768) × USD/VND
                </p>
                {goldSpread && (
                  <p className="mt-1.5">
                    Kỳ gần nhất dùng nguồn tỷ giá{" "}
                    <strong className="font-mono text-[#0F172A]">{goldSpread.fx_source}</strong> (
                    {goldSpread.disclaimer_vi}).
                  </p>
                )}
              </div>
            </section>
          )}

          <p className="border-t border-[#E2E8F0] pt-4 text-xs text-[#475569]">{disclaimer}</p>
        </div>
      </div>
    </div>
  );
}

