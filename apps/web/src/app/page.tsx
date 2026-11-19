import { PricesDashboard } from "@/components/prices-dashboard";
import { fetchLatestPrices } from "@/lib/api";
import type { CityCode, CommodityCategory, LatestPricesResponse } from "@/lib/types";

export const dynamic = "force-dynamic";

const VALID_CITIES = new Set<CityCode>(["HN", "DN", "HCM"]);
const VALID_DOMAINS = new Set<CommodityCategory>(["GOLD", "FUEL", "GAS"]);

function normalizeParam(value: string | string[] | undefined): string | undefined {
  if (Array.isArray(value)) {
    return value[0];
  }
  return value;
}

export default async function HomePage({
  searchParams,
}: {
  searchParams: Promise<{ city?: string | string[]; domain?: string | string[] }>;
}) {
  const params = await searchParams;
  const rawCity = normalizeParam(params.city)?.toUpperCase();
  const rawDomain = normalizeParam(params.domain)?.toUpperCase();

  const initialCity: CityCode = VALID_CITIES.has(rawCity as CityCode)
    ? (rawCity as CityCode)
    : "HN";
  const initialDomain: "ALL" | CommodityCategory = VALID_DOMAINS.has(rawDomain as CommodityCategory)
    ? (rawDomain as CommodityCategory)
    : "ALL";

  let initialData: LatestPricesResponse | null = null;
  let initialError: string | null = null;

  try {
    initialData = await fetchLatestPrices({ location: initialCity });
  } catch (err: unknown) {
    initialError =
      err instanceof Error ? err.message : "Không thể kết nối máy chủ dữ liệu lúc khởi tạo.";
  }

  return (
    <PricesDashboard
      initialCity={initialCity}
      initialDomain={initialDomain}
      initialData={initialData}
      initialError={initialError}
    />
  );
}
