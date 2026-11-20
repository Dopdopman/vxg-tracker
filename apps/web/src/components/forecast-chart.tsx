"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  LineSeries,
  LineStyle,
  createChart,
  type BarPrice,
  type IChartApi,
  type ISeriesApi,
  type Time,
} from "lightweight-charts";
import { Info } from "lucide-react";
import { formatVnDate, formatVnd } from "@/lib/format";
import type { ForecastItem, PriceHistoryPoint } from "@/lib/types";

interface ForecastChartProps {
  historyPoints: PriceHistoryPoint[];
  forecast: ForecastItem;
  divisor: number;
  unitLabel: string;
}

interface ConePolygon {
  x1: number;
  yBase: number;
  x2: number;
  yHigh: number;
  yLow: number;
}

interface CrosshairReadout {
  dateLabel: string;
  priceVnd: number;
  isForecast: boolean;
  lowerVnd: number | null;
  upperVnd: number | null;
}

function addDaysIso(isoDate: string, days: number): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  if (!y || !m || !d) {
    return isoDate;
  }
  const dt = new Date(Date.UTC(y, m - 1, d + days));
  return dt.toISOString().slice(0, 10);
}

export function ForecastChart({
  historyPoints,
  forecast,
  divisor,
  unitLabel,
}: ForecastChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [conePoly, setConePoly] = useState<ConePolygon | null>(null);
  const [readout, setReadout] = useState<CrosshairReadout | null>(null);

  const sortedHistory = useMemo(() => {
    const byDate = new Map<string, number>();
    for (const pt of historyPoints) {
      if (pt.date && Number.isFinite(pt.reference_price) && pt.reference_price > 0) {
        byDate.set(pt.date, pt.reference_price);
      }
    }
    return Array.from(byDate.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([date, reference_price]) => ({ date, reference_price }));
  }, [historyPoints]);

  const projection = useMemo(() => {
    if (forecast.status !== "READY" || forecast.point_estimate === null) {
      return null;
    }
    const lastHist = sortedHistory[sortedHistory.length - 1] ?? null;
    const anchorDate = lastHist ? lastHist.date : forecast.forecast_date;
    const anchorVnd = lastHist
      ? lastHist.reference_price
      : (forecast.baseline_price ?? forecast.point_estimate);
    const targetDate =
      forecast.target_date > anchorDate ? forecast.target_date : addDaysIso(anchorDate, 1);

    return {
      anchorDate,
      anchorVal: Math.round(anchorVnd / divisor),
      targetDate,
      pointVal: Math.round(forecast.point_estimate / divisor),
      lowerVal:
        forecast.lower_bound !== null ? Math.round(forecast.lower_bound / divisor) : null,
      upperVal:
        forecast.upper_bound !== null ? Math.round(forecast.upper_bound / divisor) : null,
    };
  }, [forecast, sortedHistory, divisor]);

  useEffect(() => {
    const el = containerRef.current;
    if (!el || sortedHistory.length === 0) {
      setConePoly(null);
      setReadout(null);
      return;
    }

    const lineColor =
      forecast.predicted_direction === "UP"
        ? "#047857"
        : forecast.predicted_direction === "DOWN"
          ? "#BE123C"
          : "#475569";

    const chart: IChartApi = createChart(el, {
      width: el.clientWidth,
      height: el.clientHeight || 340,
      layout: {
        background: { type: ColorType.Solid, color: "#FFFFFF" },
        textColor: "#475569",
        fontFamily: "var(--font-plex-mono), ui-monospace, monospace",
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: "#F1F5F9" },
        horzLines: { color: "#F1F5F9" },
      },
      rightPriceScale: {
        borderColor: "#E2E8F0",
        scaleMargins: { top: 0.14, bottom: 0.14 },
      },
      timeScale: {
        borderColor: "#E2E8F0",
        rightOffset: 3,
        fixLeftEdge: true,
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#94A3B8", width: 1, style: LineStyle.Dashed },
        horzLine: { color: "#94A3B8", width: 1, style: LineStyle.Dashed },
      },
      localization: {
        priceFormatter: (price: BarPrice) =>
          formatVnd(Number(price), { includeSymbol: false }),
      },
      handleScroll: { mouseWheel: true, pressedMouseMove: true },
      handleScale: { mouseWheel: true, pinch: true },
    });

    const historySeries: ISeriesApi<"Line"> = chart.addSeries(LineSeries, {
      color: "#0F172A",
      lineWidth: 2,
      lineStyle: LineStyle.Solid,
      priceLineVisible: false,
      lastValueVisible: true,
      crosshairMarkerRadius: 4,
      priceFormat: {
        type: "custom",
        minMove: 1,
        formatter: (price: BarPrice) => formatVnd(Number(price), { includeSymbol: false }),
      },
    });

    const histData = sortedHistory.map((pt) => ({
      time: pt.date as Time,
      value: Math.round(pt.reference_price / divisor),
    }));
    historySeries.setData(histData);

    let forecastSeries: ISeriesApi<"Line"> | null = null;
    if (projection) {
      forecastSeries = chart.addSeries(LineSeries, {
        color: lineColor,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: true,
        crosshairMarkerRadius: 4,
        priceFormat: {
          type: "custom",
          minMove: 1,
          formatter: (price: BarPrice) => formatVnd(Number(price), { includeSymbol: false }),
        },
      });
      forecastSeries.setData([
        { time: projection.anchorDate as Time, value: projection.anchorVal },
        { time: projection.targetDate as Time, value: projection.pointVal },
      ]);

      if (projection.upperVal !== null && projection.lowerVal !== null) {
        const upperSeries = chart.addSeries(LineSeries, {
          color: "rgba(100, 116, 139, 0.55)",
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        upperSeries.setData([
          { time: projection.anchorDate as Time, value: projection.anchorVal },
          { time: projection.targetDate as Time, value: projection.upperVal },
        ]);

        const lowerSeries = chart.addSeries(LineSeries, {
          color: "rgba(100, 116, 139, 0.55)",
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        lowerSeries.setData([
          { time: projection.anchorDate as Time, value: projection.anchorVal },
          { time: projection.targetDate as Time, value: projection.lowerVal },
        ]);
      }
    }

    chart.timeScale().fitContent();

    const updateCone = () => {
      if (
        !forecastSeries ||
        !projection ||
        projection.lowerVal === null ||
        projection.upperVal === null
      ) {
        setConePoly(null);
        return;
      }
      const x1 = chart.timeScale().timeToCoordinate(projection.anchorDate as Time);
      const x2 = chart.timeScale().timeToCoordinate(projection.targetDate as Time);
      const yBase = forecastSeries.priceToCoordinate(projection.anchorVal);
      const yHigh = forecastSeries.priceToCoordinate(projection.upperVal);
      const yLow = forecastSeries.priceToCoordinate(projection.lowerVal);

      if (x1 === null || x2 === null || yBase === null || yHigh === null || yLow === null) {
        setConePoly(null);
        return;
      }
      setConePoly({ x1, yBase, x2, yHigh, yLow });
    };

    const rafId = window.requestAnimationFrame(updateCone);
    chart.timeScale().subscribeVisibleLogicalRangeChange(updateCone);

    chart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        setReadout(null);
        return;
      }
      const timeStr = typeof param.time === "string" ? param.time : "";
      if (!timeStr) {
        setReadout(null);
        return;
      }
      if (projection && timeStr === projection.targetDate) {
        setReadout({
          dateLabel: formatVnDate(timeStr),
          priceVnd: projection.pointVal,
          isForecast: true,
          lowerVnd: projection.lowerVal,
          upperVnd: projection.upperVal,
        });
        return;
      }
      const histVal = param.seriesData.get(historySeries);
      if (histVal && "value" in histVal && typeof histVal.value === "number") {
        setReadout({
          dateLabel: formatVnDate(timeStr),
          priceVnd: histVal.value,
          isForecast: false,
          lowerVnd: null,
          upperVnd: null,
        });
        return;
      }
      setReadout(null);
    });

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      const nextWidth = Math.floor(entry.contentRect.width);
      const nextHeight = Math.floor(entry.contentRect.height) || 340;
      if (nextWidth > 0) {
        chart.applyOptions({ width: nextWidth, height: nextHeight });
        window.requestAnimationFrame(updateCone);
      }
    });
    resizeObserver.observe(el);

    return () => {
      window.cancelAnimationFrame(rafId);
      resizeObserver.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(updateCone);
      chart.remove();
    };
  }, [sortedHistory, projection, forecast.predicted_direction, divisor]);

  const coneFill =
    forecast.predicted_direction === "UP"
      ? "rgba(4, 120, 87, 0.10)"
      : forecast.predicted_direction === "DOWN"
        ? "rgba(190, 18, 60, 0.10)"
        : "rgba(71, 85, 105, 0.10)";

  return (
    <div className="space-y-3">
      {sortedHistory.length === 0 ? (
        <div className="flex h-[320px] items-center justify-center rounded border border-dashed border-[#CBD5E1] bg-[#F8FAFC] px-6 text-center text-sm text-[#475569]">
          Chưa có chuỗi dữ liệu lịch sử đã chuẩn hóa cho lựa chọn này.
        </div>
      ) : (
        <div className="relative h-[340px] w-full overflow-hidden rounded border border-[#E2E8F0] bg-white">
          <div ref={containerRef} className="h-full w-full" />

          {conePoly && (
            <svg
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 z-10 h-full w-full"
            >
              <polygon
                points={`${conePoly.x1},${conePoly.yBase} ${conePoly.x2},${conePoly.yHigh} ${conePoly.x2},${conePoly.yLow}`}
                fill={coneFill}
              />
            </svg>
          )}

          {readout && (
            <div className="pointer-events-none absolute top-3 left-3 z-20 rounded border border-[#E2E8F0] bg-white/95 px-3 py-2 text-xs shadow-xs">
              <p className="tabular-nums font-medium text-[#475569]">
                {readout.isForecast ? "Dự báo mục tiêu" : "Giá thực tế"} · {readout.dateLabel}
              </p>
              <p className="tabular-nums mt-0.5 text-sm font-semibold text-[#0F172A]">
                {formatVnd(readout.priceVnd)}{" "}
                <span className="text-[11px] font-normal text-[#475569]">({unitLabel})</span>
              </p>
              {readout.lowerVnd !== null && readout.upperVnd !== null && (
                <p className="tabular-nums mt-0.5 text-[11px] text-[#475569]">
                  Biên 80%: {formatVnd(readout.lowerVnd, { includeSymbol: false })} –{" "}
                  {formatVnd(readout.upperVnd)}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {forecast.status === "INSUFFICIENT_DATA" && (
        <div className="flex items-start gap-2.5 rounded border border-amber-200 bg-amber-50/70 px-4 py-3 text-xs text-amber-900">
          <Info className="mt-0.5 h-4 w-4 shrink-0 text-amber-700" strokeWidth={1.75} />
          <div>
            <p className="font-semibold">
              Chưa đủ dữ liệu lịch sử để hiển thị đường dự báo (INSUFFICIENT_DATA)
            </p>
            <p className="mt-0.5 text-amber-800">
              {forecast.insufficient_reason ??
                "Hệ thống chỉ hiển thị lịch sử thật hiện có và sẽ tự động bật dự báo khi đủ ngưỡng mẫu lịch sử."}
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

