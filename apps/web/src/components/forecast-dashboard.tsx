"use client";

import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { AlertTriangle, ExternalLink, RefreshCw, SlidersHorizontal } from "lucide-react";
import { fetchForecastDetail, fetchLatestForecasts, fetchNewsAnalytics } from "@/lib/api";
import {
  formatBrandLabel,
  formatCatalystLabel,
  formatCityName,
  formatHorizonLabel,
  formatMacroValue,
  formatPercent,
  formatRangeLabel,
  formatSignedPercent,
  formatSignedVnd,
  formatVnDate,
  formatVnDateTime,
  formatVnd,
  formatZoneLabel,
} from "@/lib/format";
import type {
  CatalogResponse,
  CityCode,
  CommodityCatalogItem,
  CommodityCategory,
  ForecastDetailResponse,
  ForecastHorizon,
  ForecastItem,
  HistoryLocationCode,
  HistoryRange,
  MacroAnalyticsResponse,
  MacroIndicatorCode,
  NewsAnalyticsResponse,
} from "@/lib/types";
import { ForecastChart } from "./forecast-chart";
import { ModelBacktestDrawer } from "./model-backtest-drawer";
import { SiteHeader } from "./site-header";
import { DirectionBadge, ImpactBadge } from "./status-badge";

interface ForecastDashboardProps {
  initialDomain: CommodityCategory;
  initialCity: CityCode;
  initialCommodityCode: string;
  initialHorizon: ForecastHorizon;
  initialRange: HistoryRange;
  catalog: CatalogResponse;
  initialLatestForecasts: ForecastItem[];
  initialDetail: ForecastDetailResponse | null;
  initialMacro: MacroAnalyticsResponse | null;
  initialNews: NewsAnalyticsResponse | null;
  initialError: string | null;
}

const DOMAINS: readonly { code: CommodityCategory; label: string }[] = [
  { code: "GOLD", label: "Vàng" },
  { code: "FUEL", label: "Xăng dầu" },
  { code: "GAS", label: "Gas" },
];

const CITIES: readonly { code: CityCode; label: string }[] = [
  { code: "HN", label: "Hà Nội" },
  { code: "DN", label: "Đà Nẵng" },
  { code: "HCM", label: "TP. Hồ Chí Minh" },
];

const RANGES: readonly HistoryRange[] = ["30d", "90d", "1y", "2y"];

const GOLD_HORIZONS: readonly { code: ForecastHorizon; label: string }[] = [
  { code: "T_PLUS_1", label: "T+1" },
  { code: "T_PLUS_7", label: "T+7" },
];

const DOMAIN_MACRO_CODES: Record<CommodityCategory, readonly MacroIndicatorCode[]> = {
  GOLD: ["XAU_USD", "DXY", "USD_VND_VCB", "USD_VND_MARKET"],
  FUEL: ["BRENT_USD", "WTI_USD", "USD_VND_VCB", "DXY"],
  GAS: ["BRENT_USD", "WTI_USD", "USD_VND_VCB", "DXY"],
};

const MACRO_NOTES_VI: Record<MacroIndicatorCode, string> = {
  XAU_USD: "Giá vàng giao ngay quốc tế dẫn dắt xu hướng vàng trong nước.",
  BRENT_USD: "Tham chiếu chính cho giá xăng dầu thành phẩm nhập khẩu.",
  WTI_USD: "Chỉ báo bổ sung biến động thị trường dầu thô toàn cầu.",
  DXY: "Sức mạnh đồng USD thường tác động nghịch chiều tới giá hàng hóa.",
  USD_VND_VCB: "Tỷ giá bán ra niêm yết tại Vietcombank (chi phí quy đổi nội địa).",
  USD_VND_MARKET: "Chuỗi tỷ giá liên ngân hàng dài hạn phục vụ tính chênh lệch.",
};

function defaultHorizonForDomain(domain: CommodityCategory): ForecastHorizon {
  if (domain === "GOLD") {
    return "T_PLUS_1";
  }
  if (domain === "FUEL") {
    return "NEXT_FUEL_CYCLE";
  }
  return "NEXT_GAS_MONTH";
}

function resolveRepresentativeCommodity(
  domain: CommodityCategory,
  commodities: CommodityCatalogItem[],
  latestItems: ForecastItem[]
): string {
  const domainCommodities = commodities.filter((c) => c.category === domain);
  const preferredByDomain: Record<CommodityCategory, string> = {
    GOLD: "GOLD_SJC_BAR_SJC",
    FUEL: "FUEL_RON95_III",
    GAS: "GAS_SAIGONPETRO_12KG",
  };
  const preferredCode = preferredByDomain[domain];
  if (
    latestItems.some(
      (f) => f.domain === domain && f.commodity_code === preferredCode && f.status === "READY"
    )
  ) {
    return preferredCode;
  }
  const readyCode = latestItems.find(
    (f) => f.domain === domain && f.status === "READY"
  )?.commodity_code;
  if (readyCode && domainCommodities.some((c) => c.code === readyCode)) {
    return readyCode;
  }
  if (domainCommodities.some((c) => c.code === preferredCode)) {
    return preferredCode;
  }
  return domainCommodities[0]?.code ?? "";
}

export function ForecastDashboard({
  initialDomain,
  initialCity,
  initialCommodityCode,
  initialHorizon,
  initialRange,
  catalog,
  initialLatestForecasts,
  initialDetail,
  initialMacro,
  initialNews,
  initialError,
}: ForecastDashboardProps) {
  const router = useRouter();
  const [domain, setDomain] = useState<CommodityCategory>(initialDomain);
  const [city, setCity] = useState<CityCode>(initialCity);
  const [commodityCode, setCommodityCode] = useState<string>(initialCommodityCode);
  const [horizon, setHorizon] = useState<ForecastHorizon>(initialHorizon);
  const [range, setRange] = useState<HistoryRange>(initialRange);
  const [goldUnit, setGoldUnit] = useState<"LUONG" | "CHI">("LUONG");
  const [activeExplainTab, setActiveExplainTab] = useState<"macro" | "news">("macro");
  const [drawerOpen, setDrawerOpen] = useState(false);

  const [latestForecasts, setLatestForecasts] = useState<ForecastItem[]>(initialLatestForecasts);
  const [detail, setDetail] = useState<ForecastDetailResponse | null>(initialDetail);
  const [macro] = useState<MacroAnalyticsResponse | null>(initialMacro);
  const [news, setNews] = useState<NewsAnalyticsResponse | null>(initialNews);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(initialError);

  const reqSeqRef = useRef(0);

  const domainCommodities = useMemo(() => {
    const statusByCode = new Map<string, boolean>();
    for (const item of latestForecasts) {
      if (item.domain === domain && item.status === "READY") {
        statusByCode.set(item.commodity_code, true);
      }
    }
    return catalog.commodities
      .filter((c) => c.category === domain)
      .sort((a, b) => {
        const aReady = statusByCode.has(a.code) ? 0 : 1;
        const bReady = statusByCode.has(b.code) ? 0 : 1;
        if (aReady !== bReady) {
          return aReady - bReady;
        }
        return a.display_order - b.display_order;
      });
  }, [catalog.commodities, domain, latestForecasts]);

  const readySetForDomain = useMemo(() => {
    const s = new Set<string>();
    for (const item of latestForecasts) {
      if (item.domain === domain && item.status === "READY") {
        s.add(item.commodity_code);
      }
    }
    return s;
  }, [latestForecasts, domain]);

  const syncUrl = (
    nextDomain: CommodityCategory,
    nextCity: CityCode,
    nextCommodity: string,
    nextRange: HistoryRange,
    nextHorizon: ForecastHorizon
  ) => {
    const query = new URLSearchParams();
    query.set("domain", nextDomain);
    if (nextDomain !== "FUEL") {
      query.set("city", nextCity);
    }
    if (nextCommodity) {
      query.set("commodity", nextCommodity);
    }
    query.set("range", nextRange);
    if (nextDomain === "GOLD") {
      query.set("horizon", nextHorizon);
    }
    router.replace(`/forecast?${query.toString()}`, { scroll: false });
  };

  const loadSelection = async (params: {
    nextDomain: CommodityCategory;
    nextCity: CityCode;
    nextCommodity: string;
    nextRange: HistoryRange;
    nextHorizon: ForecastHorizon;
    refreshNews?: boolean;
    refreshLatest?: boolean;
  }) => {
    const seq = ++reqSeqRef.current;
    setLoading(true);
    setError(null);

    const apiLoc: HistoryLocationCode =
      params.nextDomain === "FUEL" ? "VUNG_1" : params.nextCity;

    try {
      const newsPromise = params.refreshNews
        ? fetchNewsAnalytics({
            domain: params.nextDomain,
            hours: params.nextDomain === "GAS" ? 168 : 72,
            limit: 5,
          })
        : Promise.resolve(null);

      const latestPromise = params.refreshLatest
        ? fetchLatestForecasts({ location: params.nextCity })
        : Promise.resolve(null);

      const [nextDetail, nextNews, nextLatest] = await Promise.all([
        fetchForecastDetail({
          commodity_code: params.nextCommodity,
          location: apiLoc,
          horizon: params.nextHorizon,
          history_range: params.nextRange,
        }),
        newsPromise,
        latestPromise,
      ]);

      if (seq !== reqSeqRef.current) {
        return;
      }
      setDetail(nextDetail);
      if (nextNews) {
        setNews(nextNews);
      }
      if (nextLatest) {
        setLatestForecasts(nextLatest.items);
      }
    } catch (err: unknown) {
      if (seq === reqSeqRef.current) {
        setError(err instanceof Error ? err.message : "Không thể tải dữ liệu phân tích & dự báo.");
      }
    } finally {
      if (seq === reqSeqRef.current) {
        setLoading(false);
      }
    }
  };

  const handleDomainChange = (nextDomain: CommodityCategory) => {
    if (nextDomain === domain) {
      return;
    }
    const nextCity: CityCode = nextDomain === "GOLD" || nextDomain === "GAS" ? "HCM" : city;
    const nextHorizon = defaultHorizonForDomain(nextDomain);
    const nextCommodity = resolveRepresentativeCommodity(
      nextDomain,
      catalog.commodities,
      latestForecasts
    );
    setDomain(nextDomain);
    if (nextCity !== city) {
      setCity(nextCity);
    }
    setHorizon(nextHorizon);
    setCommodityCode(nextCommodity);
    syncUrl(nextDomain, nextCity, nextCommodity, range, nextHorizon);
    void loadSelection({
      nextDomain,
      nextCity,
      nextCommodity,
      nextRange: range,
      nextHorizon,
      refreshNews: true,
      refreshLatest: nextCity !== city,
    });
  };

  const handleCityChange = (nextCity: CityCode) => {
    if (nextCity === city || domain === "FUEL") {
      return;
    }
    setCity(nextCity);
    syncUrl(domain, nextCity, commodityCode, range, horizon);
    void loadSelection({
      nextDomain: domain,
      nextCity,
      nextCommodity: commodityCode,
      nextRange: range,
      nextHorizon: horizon,
      refreshLatest: true,
    });
  };

  const handleCommodityChange = (nextCode: string) => {
    if (!nextCode || nextCode === commodityCode) {
      return;
    }
    setCommodityCode(nextCode);
    syncUrl(domain, city, nextCode, range, horizon);
    void loadSelection({
      nextDomain: domain,
      nextCity: city,
      nextCommodity: nextCode,
      nextRange: range,
      nextHorizon: horizon,
    });
  };

  const handleRangeChange = (nextRange: HistoryRange) => {
    if (nextRange === range) {
      return;
    }
    setRange(nextRange);
    syncUrl(domain, city, commodityCode, nextRange, horizon);
    void loadSelection({
      nextDomain: domain,
      nextCity: city,
      nextCommodity: commodityCode,
      nextRange,
      nextHorizon: horizon,
    });
  };

  const handleHorizonChange = (nextHorizon: ForecastHorizon) => {
    if (nextHorizon === horizon) {
      return;
    }
    setHorizon(nextHorizon);
    syncUrl(domain, city, commodityCode, range, nextHorizon);
    void loadSelection({
      nextDomain: domain,
      nextCity: city,
      nextCommodity: commodityCode,
      nextRange: range,
      nextHorizon,
    });
  };

  const forecast = detail?.forecast ?? null;
  const goldDivisor = domain === "GOLD" && goldUnit === "CHI" ? 10 : 1;
  const unitLabel =
    domain === "GOLD"
      ? goldUnit === "CHI"
        ? "VND / Chỉ"
        : "VND / Lượng"
      : domain === "FUEL"
        ? "VND / Lít"
        : "VND / Bình";

  const mergedNews = useMemo(() => {
    const seen = new Set<string>();
    const out: {
      id: string;
      publisher: string;
      title: string;
      url: string;
      publishedAt: string | null;
      impactLevel: string;
      catalysts: string[];
      summary: string | null;
    }[] = [];

    for (const item of detail?.news_signals ?? []) {
      if (item.url && !seen.has(item.url)) {
        seen.add(item.url);
        out.push({
          id: item.id || item.url,
          publisher: item.publisher,
          title: item.title,
          url: item.url,
          publishedAt: item.published_at_vn,
          impactLevel: item.impact_level,
          catalysts: item.catalysts,
          summary: item.summary,
        });
      }
    }
    for (const item of news?.items ?? []) {
      if (item.url && !seen.has(item.url)) {
        seen.add(item.url);
        out.push({
          id: item.id,
          publisher: item.source_name,
          title: item.title,
          url: item.url,
          publishedAt: item.published_at,
          impactLevel: item.impact_level,
          catalysts: item.catalysts,
          summary: item.summary,
        });
      }
    }
    return out.slice(0, 5);
  }, [detail?.news_signals, news?.items]);

  const macroRows = useMemo(() => {
    const codes = DOMAIN_MACRO_CODES[domain];
    const latestMap = macro?.latest ?? {};
    return codes
      .map((code) => latestMap[code])
      .filter((item): item is NonNullable<typeof item> => item !== undefined);
  }, [domain, macro?.latest]);

  const leadingMacro = useMemo(() => {
    if (domain === "GOLD") {
      if (macro?.sjc_world_spread) {
        const sp = macro.sjc_world_spread;
        return {
          title: "Chênh lệch SJC vs Thế giới quy đổi",
          primaryValue: formatSignedPercent(sp.spread_pct, 2),
          secondaryLine: `Chênh: ${formatSignedVnd(sp.spread_vnd)} / lượng`,
          footnote: `Quy đổi ${formatVnd(sp.world_vnd_per_tael)} (${sp.fx_source})`,
        };
      }
      const xau = macro?.latest.XAU_USD;
      return {
        title: "Vàng thế giới (XAU/USD)",
        primaryValue: xau ? formatMacroValue(xau.value, xau.unit) : "—",
        secondaryLine: xau ? `Biến động 7N: ${formatSignedPercent(xau.change_pct_7d, 2)}` : "Chờ đồng bộ vĩ mô",
        footnote: "Nguồn: Yahoo Finance (GC=F)",
      };
    }

    const brent = detail?.macro_highlights.find((m) => m.key === "BRENT_USD") ?? macro?.latest.BRENT_USD;
    const fx =
      detail?.macro_highlights.find((m) => m.key.startsWith("USD_VND")) ??
      macro?.latest.USD_VND_VCB ??
      macro?.latest.USD_VND_MARKET;

    if (domain === "FUEL") {
      return {
        title: "Dầu thô Brent & Tỷ giá USD/VND",
        primaryValue: brent
          ? `${formatMacroValue(brent.value, "USD")} (${formatSignedPercent(brent.change_pct_7d, 2)} · 7N)`
          : "—",
        secondaryLine: fx
          ? `USD/VND: ${formatVnd(fx.value)} (${formatSignedPercent(fx.change_pct_7d, 2)} · 7N)`
          : "Chờ đồng bộ tỷ giá",
        footnote: "Chu kỳ bình quân 7 ngày định giá Liên Bộ",
      };
    }

    const cpNewsCount = mergedNews.filter((n) => n.catalysts.includes("CP_ARAMCO")).length;
    return {
      title: "Dầu thô, Tỷ giá & Tín hiệu CP Gas",
      primaryValue: brent
        ? `Brent ${formatMacroValue(brent.value, "USD")} (${formatSignedPercent(brent.change_pct_7d, 2)} · 7N)`
        : "—",
      secondaryLine: fx ? `USD/VND: ${formatVnd(fx.value)}` : "Chờ đồng bộ tỷ giá",
      footnote:
        cpNewsCount > 0
          ? `${cpNewsCount} tin liên quan giá CP Aramco gần đây`
          : "Truyền dẫn chi phí thế giới → bán lẻ đầu tháng",
    };
  }, [domain, detail?.macro_highlights, macro, mergedNews]);

  const backtest = detail?.backtest_summary ?? forecast?.backtest_summary ?? null;

  return (
    <div className="min-h-screen bg-[#F8FAFC] text-[#0F172A]">
      <SiteHeader lastUpdatedVn={detail?.as_of_vn ?? macro?.as_of_vn ?? null} />

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        {/* Tiêu đề & Thanh bộ lọc 1 hàng gọn */}
        <div className="mb-6 border-b border-[#E2E8F0] pb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-[#0F172A]">
            Phân tích &amp; Dự báo xu hướng giá
          </h1>
          <p className="mt-1 text-sm text-[#475569]">
            Mô hình định lượng minh bạch theo từng ngành (Zero Look-Ahead), tự chấm điểm sai số
            thực tế và tách bạch dữ liệu chưa đủ mẫu.
          </p>

          <div className="mt-5 flex flex-wrap items-center gap-3">
            {/* Ngành hàng */}
            <div
              role="group"
              aria-label="Chọn ngành hàng phân tích"
              className="inline-flex rounded border border-[#E2E8F0] bg-white p-0.5 text-sm"
            >
              {DOMAINS.map((d) => {
                const active = domain === d.code;
                return (
                  <button
                    key={d.code}
                    type="button"
                    onClick={() => handleDomainChange(d.code)}
                    aria-pressed={active}
                    className={`rounded-sm px-3.5 py-1.5 transition-colors ${
                      active
                        ? "bg-[#0F172A] font-medium text-white"
                        : "text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    {d.label}
                  </button>
                );
              })}
            </div>

            {/* Chọn mã sản phẩm */}
            <label className="sr-only" htmlFor="forecast-commodity-select">
              Chọn mặt hàng
            </label>
            <select
              id="forecast-commodity-select"
              value={commodityCode}
              onChange={(event) => handleCommodityChange(event.target.value)}
              className="w-full max-w-full rounded border border-[#E2E8F0] bg-white px-3 py-1.5 text-sm font-medium text-[#0F172A] focus:border-[#0F172A] focus:outline-none sm:w-auto"
            >
              {domainCommodities.map((c) => {
                const isReady = readySetForDomain.has(c.code);
                return (
                  <option key={c.code} value={c.code}>
                    {formatBrandLabel(c.brand)} · {c.product_name}
                    {isReady ? "" : " (Chưa đủ mẫu)"}
                  </option>
                );
              })}
            </select>

            {/* Thành phố (Vàng & Gas) hoặc Biểu giá Vùng 1 (Xăng dầu) */}
            {domain === "FUEL" ? (
              <span className="rounded border border-[#E2E8F0] bg-white px-3 py-1.5 text-xs font-medium text-[#475569]">
                Biểu giá {formatZoneLabel("VUNG_1")}
              </span>
            ) : (
              <div
                role="group"
                aria-label="Chọn thành phố"
                className="inline-flex rounded border border-[#E2E8F0] bg-white p-0.5 text-xs"
              >
                {CITIES.map((c) => {
                  const active = city === c.code;
                  return (
                    <button
                      key={c.code}
                      type="button"
                      onClick={() => handleCityChange(c.code)}
                      aria-pressed={active}
                      className={`rounded-sm px-2.5 py-1.5 transition-colors ${
                        active
                          ? "bg-[#0F172A] font-medium text-white"
                          : "text-[#475569] hover:text-[#0F172A]"
                      }`}
                    >
                      {c.label}
                    </button>
                  );
                })}
              </div>
            )}

            {/* Khung lịch sử */}
            <div
              role="group"
              aria-label="Khung thời gian lịch sử"
              className="inline-flex rounded border border-[#E2E8F0] bg-white p-0.5 text-xs"
            >
              {RANGES.map((r) => {
                const active = range === r;
                return (
                  <button
                    key={r}
                    type="button"
                    onClick={() => handleRangeChange(r)}
                    aria-pressed={active}
                    className={`rounded-sm px-2.5 py-1.5 transition-colors ${
                      active
                        ? "bg-[#0F172A] font-medium text-white"
                        : "text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    {formatRangeLabel(r)}
                  </button>
                );
              })}
            </div>

            {/* Kỳ hạn T+1 | T+7 & Đơn vị Lượng/Chỉ riêng cho Vàng */}
            {domain === "GOLD" && (
              <>
                <div
                  role="group"
                  aria-label="Kỳ hạn dự báo vàng"
                  className="inline-flex rounded border border-[#E2E8F0] bg-white p-0.5 text-xs"
                >
                  {GOLD_HORIZONS.map((h) => {
                    const active = horizon === h.code;
                    return (
                      <button
                        key={h.code}
                        type="button"
                        onClick={() => handleHorizonChange(h.code)}
                        aria-pressed={active}
                        className={`rounded-sm px-2.5 py-1.5 transition-colors ${
                          active
                            ? "bg-[#0F172A] font-medium text-white"
                            : "text-[#475569] hover:text-[#0F172A]"
                        }`}
                      >
                        {h.label}
                      </button>
                    );
                  })}
                </div>

                <div
                  role="group"
                  aria-label="Đơn vị quy đổi vàng"
                  className="inline-flex rounded border border-[#E2E8F0] bg-white p-0.5 text-xs"
                >
                  <button
                    type="button"
                    onClick={() => setGoldUnit("LUONG")}
                    aria-pressed={goldUnit === "LUONG"}
                    className={`rounded-sm px-2.5 py-1.5 transition-colors ${
                      goldUnit === "LUONG"
                        ? "bg-[#0F172A] font-medium text-white"
                        : "text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    Lượng
                  </button>
                  <button
                    type="button"
                    onClick={() => setGoldUnit("CHI")}
                    aria-pressed={goldUnit === "CHI"}
                    className={`rounded-sm px-2.5 py-1.5 transition-colors ${
                      goldUnit === "CHI"
                        ? "bg-[#0F172A] font-medium text-white"
                        : "text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    Chỉ
                  </button>
                </div>
              </>
            )}
          </div>
        </div>

        {error && (
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded border border-rose-200 bg-rose-50/70 px-4 py-3 text-sm text-rose-800">
            <div>
              <p className="font-medium">Không thể tải dữ liệu phân tích</p>
              <p className="mt-0.5 text-xs text-rose-700">{error}</p>
            </div>
            <button
              type="button"
              onClick={() =>
                void loadSelection({
                  nextDomain: domain,
                  nextCity: city,
                  nextCommodity: commodityCode,
                  nextRange: range,
                  nextHorizon: horizon,
                  refreshNews: true,
                })
              }
              className="inline-flex items-center gap-1.5 rounded border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-800 hover:bg-rose-50"
            >
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />
              Thử lại
            </button>
          </div>
        )}

        {/* Biểu đồ trung tâm */}
        <section className="mb-6 rounded border border-[#E2E8F0] bg-white p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-semibold text-[#0F172A]">
                  {forecast?.commodity_name ?? commodityCode}
                </h2>
                <span className="rounded border border-[#E2E8F0] bg-[#F8FAFC] px-2 py-0.5 text-xs font-medium text-[#475569]">
                  {domain === "FUEL"
                    ? formatZoneLabel(detail?.applied_zone ?? "VUNG_1")
                    : formatCityName(city)}
                </span>
                <span className="text-xs text-[#475569]">· {unitLabel}</span>
              </div>
              {detail?.history_stats.latest_vnd !== null &&
                detail?.history_stats.latest_vnd !== undefined && (
                  <p className="tabular-nums mt-1 text-xs text-[#475569]">
                    Giá tham chiếu gần nhất:{" "}
                    <strong className="font-semibold text-[#0F172A]">
                      {formatVnd(detail.history_stats.latest_vnd, { divisor: goldDivisor })}
                    </strong>{" "}
                    (Khung {formatRangeLabel(range)}:{" "}
                    {formatVnd(detail.history_stats.min_vnd, {
                      divisor: goldDivisor,
                      includeSymbol: false,
                    })}{" "}
                    – {formatVnd(detail.history_stats.max_vnd, { divisor: goldDivisor })})
                  </p>
                )}
            </div>

            <div className="flex flex-wrap items-center gap-4 text-xs text-[#475569]">
              <span className="inline-flex items-center gap-1.5">
                <span className="inline-block h-0.5 w-4 bg-[#0F172A]" />
                Lịch sử thật
              </span>
              {forecast?.status === "READY" && (
                <>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="inline-block h-0.5 w-4 border-t-2 border-dashed border-[#047857]" />
                    Điểm dự báo
                  </span>
                  <span className="inline-flex items-center gap-1.5">
                    <span className="inline-block h-2.5 w-4 rounded-xs border border-[#94A3B8]/60 bg-[#0F172A]/10" />
                    Biên tin cậy 80%
                  </span>
                </>
              )}
            </div>
          </div>

          {loading ? (
            <div className="h-[340px] w-full animate-pulse rounded border border-[#E2E8F0] bg-[#F8FAFC]" />
          ) : forecast ? (
            <ForecastChart
              historyPoints={detail?.history_points ?? []}
              forecast={forecast}
              divisor={goldDivisor}
              unitLabel={unitLabel}
            />
          ) : null}
        </section>

        {/* Dải 3 chỉ số cốt lõi ngay dưới biểu đồ (đúng 3 cột, không nhồi thêm) */}
        <section
          aria-label="Chỉ số dự báo cốt lõi"
          className="mb-8 grid grid-cols-1 divide-y divide-[#E2E8F0] rounded border border-[#E2E8F0] bg-white md:grid-cols-3 md:divide-x md:divide-y-0"
        >
          {/* Cột 1: Xu hướng & Biên độ dự kiến */}
          <div className="flex flex-col justify-between p-5">
            <div>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-xs font-medium text-[#475569]">Xu hướng &amp; Biên độ dự kiến</p>
                {forecast && <DirectionBadge direction={forecast.predicted_direction} />}
              </div>

              {forecast?.status === "READY" ? (
                <>
                  <p className="tabular-nums mt-2.5 text-2xl font-semibold text-[#0F172A]">
                    {formatVnd(forecast.point_estimate, { divisor: goldDivisor })}
                  </p>
                  <p className="tabular-nums mt-1 text-xs text-[#475569]">
                    Thay đổi dự kiến:{" "}
                    <span
                      className={`font-medium ${
                        (forecast.expected_change_vnd ?? 0) > 0
                          ? "text-emerald-700"
                          : (forecast.expected_change_vnd ?? 0) < 0
                            ? "text-rose-700"
                            : "text-[#0F172A]"
                      }`}
                    >
                      {formatSignedVnd(forecast.expected_change_vnd, { divisor: goldDivisor })} (
                      {formatSignedPercent(forecast.expected_change_pct, 2)})
                    </span>
                  </p>
                  <p className="tabular-nums mt-1 text-xs text-[#475569]">
                    Biên độ 80%:{" "}
                    <strong className="font-medium text-[#0F172A]">
                      {formatVnd(forecast.lower_bound, {
                        divisor: goldDivisor,
                        includeSymbol: false,
                      })}{" "}
                      – {formatVnd(forecast.upper_bound, { divisor: goldDivisor })}
                    </strong>
                  </p>
                </>
              ) : (
                <p className="mt-3 text-sm font-medium text-[#475569]">
                  Đang tích lũy thêm dữ liệu thật trước khi kích hoạt dự báo.
                </p>
              )}
            </div>

            <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-[#E2E8F0] pt-2.5 text-xs text-[#475569]">
              <span className="tabular-nums">
                Mục tiêu: {formatVnDate(forecast?.target_date)} (
                {forecast ? formatHorizonLabel(forecast.horizon_type) : "—"})
              </span>
              {forecast?.high_volatility_flag && (
                <span className="inline-flex items-center gap-1 rounded border border-amber-200 bg-amber-50/80 px-2 py-0.5 text-[11px] font-medium text-amber-800">
                  <AlertTriangle className="h-3 w-3" strokeWidth={1.75} />
                  Rủi ro tin tức cao
                </span>
              )}
            </div>
          </div>

          {/* Cột 2: Chỉ số vĩ mô dẫn dắt chính */}
          <div className="flex flex-col justify-between p-5">
            <div>
              <p className="text-xs font-medium text-[#475569]">{leadingMacro.title}</p>
              <p className="tabular-nums mt-2.5 text-2xl font-semibold text-[#0F172A]">
                {leadingMacro.primaryValue}
              </p>
              <p className="tabular-nums mt-1 text-xs text-[#475569]">
                {leadingMacro.secondaryLine}
              </p>
            </div>
            <p className="mt-3 border-t border-[#E2E8F0] pt-2.5 text-xs text-[#475569]">
              {leadingMacro.footnote}
            </p>
          </div>

          {/* Cột 3: Độ tin cậy & Sai số lịch sử + Nút mở Drawer */}
          <div className="flex flex-col justify-between p-5">
            <div>
              <div className="flex items-center justify-between gap-2">
                <p className="text-xs font-medium text-[#475569]">Độ tin cậy &amp; Sai số lịch sử</p>
                {forecast?.confidence_score !== null && forecast?.confidence_score !== undefined && (
                  <span className="tabular-nums font-mono text-xs font-semibold text-[#0F172A]">
                    {(forecast.confidence_score * 100).toFixed(0)}% tin cậy
                  </span>
                )}
              </div>

              {backtest ? (
                <>
                  <p className="tabular-nums mt-2.5 text-2xl font-semibold text-[#0F172A]">
                    MAPE {formatPercent(backtest.mape_pct, 2)}
                  </p>
                  <p className="tabular-nums mt-1 text-xs text-[#475569]">
                    MAE: {formatVnd(backtest.mae_vnd, { divisor: goldDivisor })} · Đúng chiều:{" "}
                    <strong className="font-medium text-emerald-700">
                      {formatPercent(backtest.directional_accuracy_pct, 1)}
                    </strong>
                  </p>
                  <p className="tabular-nums mt-1 text-xs text-[#475569]">
                    Phủ biên 80%: {formatPercent(backtest.interval_coverage_pct, 1)} (
                    {backtest.sample_count} kỳ kiểm thử)
                  </p>
                </>
              ) : (
                <p className="mt-3 text-xs text-[#475569]">
                  Chưa có thống kê Walk-Forward cho cặp mặt hàng &amp; khu vực này.
                </p>
              )}
            </div>

            <div className="mt-3 border-t border-[#E2E8F0] pt-2.5">
              <button
                type="button"
                onClick={() => setDrawerOpen(true)}
                className="inline-flex items-center gap-1.5 rounded border border-[#E2E8F0] bg-[#F8FAFC] px-3 py-1.5 text-xs font-medium text-[#0F172A] transition-colors hover:border-[#CBD5E1] hover:bg-white"
              >
                <SlidersHorizontal className="h-3.5 w-3.5" strokeWidth={1.75} />
                <span>Xem chi tiết mô hình</span>
              </button>
            </div>
          </div>
        </section>

        {/* Khu vực giải thích Progressive Disclosure (2 tab chuyển đổi) */}
        <section className="rounded border border-[#E2E8F0] bg-white">
          <div className="flex items-center gap-6 border-b border-[#E2E8F0] px-5 pt-3">
            <button
              type="button"
              onClick={() => setActiveExplainTab("macro")}
              className={`border-b-2 pb-2.5 text-sm transition-colors ${
                activeExplainTab === "macro"
                  ? "border-[#0F172A] font-semibold text-[#0F172A]"
                  : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
              }`}
            >
              Động lực vĩ mô ({macroRows.length})
            </button>
            <button
              type="button"
              onClick={() => setActiveExplainTab("news")}
              className={`border-b-2 pb-2.5 text-sm transition-colors ${
                activeExplainTab === "news"
                  ? "border-[#0F172A] font-semibold text-[#0F172A]"
                  : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
              }`}
            >
              Tin tức tác động ({mergedNews.length})
            </button>
          </div>

          {activeExplainTab === "macro" ? (
            macroRows.length === 0 ? (
              <p className="px-5 py-8 text-center text-sm text-[#475569]">
                Chưa có dữ liệu chỉ số vĩ mô được đồng bộ.
              </p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-left text-sm">
                  <thead>
                    <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC] text-xs font-medium uppercase tracking-wider text-[#475569]">
                      <th className="px-5 py-3">Chỉ số vĩ mô</th>
                      <th className="px-4 py-3 text-right">Giá trị gần nhất</th>
                      <th className="px-4 py-3 text-right">Biến động 1N</th>
                      <th className="px-4 py-3 text-right">Biến động 7N</th>
                      <th className="px-5 py-3">Vai trò phân tích</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-[#E2E8F0]">
                    {macroRows.map((row) => (
                      <tr key={row.indicator_code} className="hover:bg-[#F8FAFC]">
                        <td className="px-5 py-3.5">
                          <p className="font-medium text-[#0F172A]">{row.label_vi}</p>
                          <p className="tabular-nums font-mono mt-0.5 text-[11px] text-[#475569]">
                            {row.source_code} · {formatVnDate(row.date)}
                          </p>
                        </td>
                        <td className="tabular-nums px-4 py-3.5 text-right font-semibold text-[#0F172A]">
                          {formatMacroValue(row.value, row.unit)}
                        </td>
                        <td
                          className={`tabular-nums px-4 py-3.5 text-right text-xs font-medium ${
                            (row.change_pct_1d ?? 0) > 0
                              ? "text-emerald-700"
                              : (row.change_pct_1d ?? 0) < 0
                                ? "text-rose-700"
                                : "text-[#475569]"
                          }`}
                        >
                          {formatSignedPercent(row.change_pct_1d, 2)}
                        </td>
                        <td
                          className={`tabular-nums px-4 py-3.5 text-right text-xs font-medium ${
                            (row.change_pct_7d ?? 0) > 0
                              ? "text-emerald-700"
                              : (row.change_pct_7d ?? 0) < 0
                                ? "text-rose-700"
                                : "text-[#475569]"
                          }`}
                        >
                          {formatSignedPercent(row.change_pct_7d, 2)}
                        </td>
                        <td className="px-5 py-3.5 text-xs text-[#475569]">
                          {MACRO_NOTES_VI[row.indicator_code] ?? "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ) : mergedNews.length === 0 ? (
            <p className="px-5 py-8 text-center text-sm text-[#475569]">
              Không có tin tức thị trường tác động đáng chú ý trong cửa sổ theo dõi gần đây.
            </p>
          ) : (
            <div className="divide-y divide-[#E2E8F0]">
              {mergedNews.map((item) => (
                <article key={item.id} className="space-y-2 px-5 py-4 text-sm">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <ImpactBadge level={item.impactLevel} />
                      {item.catalysts.slice(0, 3).map((cat) => (
                        <span
                          key={cat}
                          className="rounded border border-[#E2E8F0] bg-[#F8FAFC] px-2 py-0.5 text-[11px] font-medium text-[#475569]"
                        >
                          {formatCatalystLabel(cat)}
                        </span>
                      ))}
                    </div>
                    <span className="tabular-nums text-xs text-[#475569]">
                      {item.publisher} ·{" "}
                      {item.publishedAt ? `${formatVnDateTime(item.publishedAt)} (UTC+7)` : "—"}
                    </span>
                  </div>

                  <a
                    href={item.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-start gap-1.5 font-medium text-[#0F172A] underline decoration-[#CBD5E1] underline-offset-4 transition-colors hover:decoration-[#0F172A]"
                  >
                    <span>{item.title}</span>
                    <ExternalLink className="mt-1 h-3.5 w-3.5 shrink-0" strokeWidth={1.75} />
                  </a>

                  {item.summary && (
                    <p className="text-xs leading-relaxed text-[#475569]">{item.summary}</p>
                  )}
                </article>
              ))}
            </div>
          )}
        </section>

        {/* Chân trang miễn trừ trách nhiệm */}
        <footer className="mt-8 border-t border-[#E2E8F0] pt-4 text-xs text-[#475569]">
          Dữ liệu phân tích định lượng phục vụ tham khảo, không phải khuyến nghị đầu tư.
        </footer>
      </main>

      {forecast && (
        <ModelBacktestDrawer
          open={drawerOpen}
          onClose={() => setDrawerOpen(false)}
          forecast={forecast}
          goldDivisor={goldDivisor}
          unitLabel={unitLabel}
          goldSpread={macro?.sjc_world_spread ?? null}
          disclaimer={
            detail?.disclaimer ??
            "Dữ liệu phân tích định lượng phục vụ tham khảo, không phải khuyến nghị đầu tư."
          }
        />
      )}
    </div>
  );
}

