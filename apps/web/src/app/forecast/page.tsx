import { ForecastDashboard } from "@/components/forecast-dashboard";
import {
  fetchCatalog,
  fetchForecastDetail,
  fetchLatestForecasts,
  fetchMacroAnalytics,
  fetchNewsAnalytics,
} from "@/lib/api";
import type {
  CityCode,
  CommodityCategory,
  ForecastHorizon,
  HistoryLocationCode,
  HistoryRange,
} from "@/lib/types";

export const dynamic = "force-dynamic";

const VALID_DOMAINS = new Set<CommodityCategory>(["GOLD", "FUEL", "GAS"]);
const VALID_CITIES = new Set<CityCode>(["HN", "DN", "HCM"]);
const VALID_RANGES = new Set<HistoryRange>(["30d", "90d", "1y", "2y"]);

function normalizeParam(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) {
    return value[0];
  }
  return value;
}

function defaultHorizon(domain: CommodityCategory): ForecastHorizon {
  if (domain === "GOLD") {
    return "T_PLUS_1";
  }
  if (domain === "FUEL") {
    return "NEXT_FUEL_CYCLE";
  }
  return "NEXT_GAS_MONTH";
}

export default async function ForecastPage({
  searchParams,
}: {
  searchParams: Promise<{
    domain?: string | string[];
    city?: string | string[];
    commodity?: string | string[];
    range?: string | string[];
    horizon?: string | string[];
  }>;
}) {
  const params = await searchParams;
  const rawDomain = normalizeParam(params.domain)?.toUpperCase();
  const rawCity = normalizeParam(params.city)?.toUpperCase();
  const rawCommodity = normalizeParam(params.commodity)?.toUpperCase();
  const rawRange = normalizeParam(params.range)?.toLowerCase();
  const rawHorizon = normalizeParam(params.horizon)?.toUpperCase();

  const initialDomain: CommodityCategory = VALID_DOMAINS.has(rawDomain as CommodityCategory)
    ? (rawDomain as CommodityCategory)
    : "GOLD";
  const initialCity: CityCode = VALID_CITIES.has(rawCity as CityCode)
    ? (rawCity as CityCode)
    : "HCM";
  const initialRange: HistoryRange = VALID_RANGES.has(rawRange as HistoryRange)
    ? (rawRange as HistoryRange)
    : "90d";
  const initialHorizon: ForecastHorizon =
    initialDomain === "GOLD" && (rawHorizon === "T_PLUS_1" || rawHorizon === "T_PLUS_7")
      ? rawHorizon
      : defaultHorizon(initialDomain);

  const [catalogRes, latestRes, macroRes, newsRes] = await Promise.allSettled([
    fetchCatalog(),
    fetchLatestForecasts({ location: initialCity }),
    fetchMacroAnalytics({ range: "30d" }),
    fetchNewsAnalytics({
      domain: initialDomain,
      hours: initialDomain === "GAS" ? 168 : 72,
      limit: 5,
    }),
  ]);

  const catalog =
    catalogRes.status === "fulfilled"
      ? catalogRes.value
      : {
          timezone: "Asia/Ho_Chi_Minh",
          server_time_vn: new Date().toISOString(),
          cities: [],
          domains: [],
          commodities: [],
          sources: [],
        };

  const latestItems = latestRes.status === "fulfilled" ? latestRes.value.items : [];
  const domainCommodities = catalog.commodities.filter((c) => c.category === initialDomain);

  let initialCommodityCode = "";
  if (rawCommodity && domainCommodities.some((c) => c.code === rawCommodity)) {
    initialCommodityCode = rawCommodity;
  } else {
    const readyMatch = latestItems.find(
      (item) => item.domain === initialDomain && item.status === "READY"
    );
    if (readyMatch && domainCommodities.some((c) => c.code === readyMatch.commodity_code)) {
      initialCommodityCode = readyMatch.commodity_code;
    } else if (
      initialDomain === "GOLD" &&
      domainCommodities.some((c) => c.code === "GOLD_SJC_BAR_SJC")
    ) {
      initialCommodityCode = "GOLD_SJC_BAR_SJC";
    } else if (
      initialDomain === "FUEL" &&
      domainCommodities.some((c) => c.code === "FUEL_RON95_III")
    ) {
      initialCommodityCode = "FUEL_RON95_III";
    } else if (
      initialDomain === "GAS" &&
      domainCommodities.some((c) => c.code === "GAS_SAIGONPETRO_12KG")
    ) {
      initialCommodityCode = "GAS_SAIGONPETRO_12KG";
    } else {
      initialCommodityCode = domainCommodities[0]?.code ?? "GOLD_SJC_BAR_SJC";
    }
  }

  let initialDetail = null;
  let detailError: string | null = null;
  const apiLoc: HistoryLocationCode = initialDomain === "FUEL" ? "VUNG_1" : initialCity;

  try {
    initialDetail = await fetchForecastDetail({
      commodity_code: initialCommodityCode,
      location: apiLoc,
      horizon: initialHorizon,
      history_range: initialRange,
    });
  } catch (err: unknown) {
    detailError =
      err instanceof Error ? err.message : "Không thể tải dữ liệu phân tích & dự báo.";
  }

  const firstCoreFailure = [catalogRes, latestRes, macroRes].find(
    (res): res is PromiseRejectedResult => res.status === "rejected"
  );
  const initialError =
    detailError ??
    (firstCoreFailure
      ? firstCoreFailure.reason instanceof Error
        ? firstCoreFailure.reason.message
        : "Không thể kết nối máy chủ API."
      : null);

  return (
    <ForecastDashboard
      initialDomain={initialDomain}
      initialCity={initialCity}
      initialCommodityCode={initialCommodityCode}
      initialHorizon={initialHorizon}
      initialRange={initialRange}
      catalog={catalog}
      initialLatestForecasts={latestItems}
      initialDetail={initialDetail}
      initialMacro={macroRes.status === "fulfilled" ? macroRes.value : null}
      initialNews={newsRes.status === "fulfilled" ? newsRes.value : null}
      initialError={initialError}
    />
  );
}

