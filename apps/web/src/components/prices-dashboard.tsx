"use client";

import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { ChevronRight, RefreshCw } from "lucide-react";
import { fetchLatestPrices } from "@/lib/api";
import {
  formatBrandLabel,
  formatCityName,
  formatVnDate,
  formatVnDateTime,
  formatVnd,
  formatZoneLabel,
} from "@/lib/format";
import type { CityCode, CommodityCategory, LatestPriceItem, LatestPricesResponse } from "@/lib/types";
import { EvidenceDrawer } from "./evidence-drawer";
import { SiteHeader } from "./site-header";
import { VerificationBadge } from "./status-badge";

interface PricesDashboardProps {
  initialCity: CityCode;
  initialDomain: "ALL" | CommodityCategory;
  initialData: LatestPricesResponse | null;
  initialError: string | null;
}

const CITIES: readonly { code: CityCode; label: string }[] = [
  { code: "HN", label: "Hà Nội" },
  { code: "DN", label: "Đà Nẵng" },
  { code: "HCM", label: "TP. Hồ Chí Minh" },
];

const DOMAINS: readonly { code: "ALL" | CommodityCategory; label: string }[] = [
  { code: "ALL", label: "Tất cả" },
  { code: "GOLD", label: "Vàng" },
  { code: "FUEL", label: "Xăng dầu" },
  { code: "GAS", label: "Gas" },
];

function TableSkeleton({ columns }: { columns: number }) {
  return (
    <div className="divide-y divide-[#E2E8F0]">
      {[0, 1, 2, 3].map((row) => (
        <div
          key={row}
          className="grid gap-4 px-5 py-3.5"
          style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
        >
          {Array.from({ length: columns }).map((_, idx) => (
            <div key={idx} className="h-4 animate-pulse rounded bg-[#F1F5F9]" />
          ))}
        </div>
      ))}
    </div>
  );
}

export function PricesDashboard({
  initialCity,
  initialDomain,
  initialData,
  initialError,
}: PricesDashboardProps) {
  const router = useRouter();
  const [selectedCity, setSelectedCity] = useState<CityCode>(initialCity);
  const [selectedDomain, setSelectedDomain] = useState<"ALL" | CommodityCategory>(initialDomain);
  const [goldUnit, setGoldUnit] = useState<"LUONG" | "CHI">("LUONG");
  const [data, setData] = useState<LatestPricesResponse | null>(initialData);
  const [error, setError] = useState<string | null>(initialError);
  const [loading, setLoading] = useState(false);
  const [evidenceRecordId, setEvidenceRecordId] = useState<string | null>(null);
  const requestSeqRef = useRef(0);

  const syncUrlParams = (city: CityCode, domain: "ALL" | CommodityCategory) => {
    const query = new URLSearchParams();
    query.set("city", city);
    if (domain !== "ALL") {
      query.set("domain", domain);
    }
    router.replace(`/?${query.toString()}`, { scroll: false });
  };

  const loadCityPrices = async (city: CityCode, domain: "ALL" | CommodityCategory) => {
    const reqId = ++requestSeqRef.current;
    setLoading(true);
    setError(null);
    try {
      const response = await fetchLatestPrices({ location: city });
      if (reqId === requestSeqRef.current) {
        setData(response);
        setLoading(false);
      }
    } catch (err: unknown) {
      if (reqId === requestSeqRef.current) {
        setError(err instanceof Error ? err.message : "Không thể tải bảng giá từ máy chủ API.");
        setLoading(false);
      }
    }
    void domain;
  };

  const handleCitySelect = (nextCity: CityCode) => {
    if (nextCity === selectedCity && data !== null && !error) {
      return;
    }
    setSelectedCity(nextCity);
    syncUrlParams(nextCity, selectedDomain);
    void loadCityPrices(nextCity, selectedDomain);
  };

  const handleDomainSelect = (nextDomain: "ALL" | CommodityCategory) => {
    setSelectedDomain(nextDomain);
    syncUrlParams(selectedCity, nextDomain);
  };

  const items = data?.items ?? [];
  const goldItems = items.filter((item) => item.domain === "GOLD");
  const fuelItems = items.filter((item) => item.domain === "FUEL");
  const gasItems = items.filter((item) => item.domain === "GAS");

  const appliedFuelZone =
    fuelItems.find((item) => item.applied_zone !== null)?.applied_zone ??
    data?.fuel_zone_mapping[selectedCity] ??
    "VUNG_1";

  const showGold = selectedDomain === "ALL" || selectedDomain === "GOLD";
  const showFuel = selectedDomain === "ALL" || selectedDomain === "FUEL";
  const showGas = selectedDomain === "ALL" || selectedDomain === "GAS";

  const goldDivisor = goldUnit === "CHI" ? 10 : 1;
  const goldUnitLabel = goldUnit === "CHI" ? "VND / Chỉ" : "VND / Lượng";

  const renderFuelEffective = (item: LatestPriceItem) =>
    item.published_at_vn ? formatVnDateTime(item.published_at_vn) : formatVnDate(item.effective_date);

  const renderGasEffective = (item: LatestPriceItem) =>
    item.published_at_vn ? formatVnDateTime(item.published_at_vn) : formatVnDate(item.effective_date);

  return (
    <div className="min-h-screen bg-[#F8FAFC] text-[#0F172A]">
      <SiteHeader lastUpdatedVn={data?.last_updated_vn ?? null} />

      <main className="mx-auto max-w-6xl px-4 py-8 sm:px-6">
        <div className="mb-6 border-b border-[#E2E8F0] pb-6">
          <h1 className="text-2xl font-semibold tracking-tight text-[#0F172A]">
            Bảng giá hàng hóa thiết yếu hôm nay
          </h1>
          <p className="mt-1 text-sm text-[#475569]">
            Số liệu niêm yết chính thức từ các doanh nghiệp đầu mối tại Việt Nam, đã qua kiểm chứng
            6 tầng an toàn. Bấm vào từng dòng để xem nguồn gốc &amp; bằng chứng.
          </p>

          {/* Thanh lọc gọn 1 hàng */}
          <div className="mt-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div
              role="group"
              aria-label="Chọn thành phố"
              className="inline-flex w-full rounded border border-[#E2E8F0] bg-white p-0.5 sm:w-auto"
            >
              {CITIES.map((city) => {
                const active = selectedCity === city.code;
                return (
                  <button
                    key={city.code}
                    type="button"
                    onClick={() => handleCitySelect(city.code)}
                    aria-pressed={active}
                    className={`flex-1 rounded-sm px-3.5 py-1.5 text-sm whitespace-nowrap transition-colors sm:flex-none ${
                      active
                        ? "bg-[#0F172A] font-medium text-white"
                        : "text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    {city.label}
                  </button>
                );
              })}
            </div>

            <div
              role="group"
              aria-label="Lọc nhóm mặt hàng"
              className="inline-flex flex-wrap items-center gap-1.5"
            >
              {DOMAINS.map((domain) => {
                const active = selectedDomain === domain.code;
                return (
                  <button
                    key={domain.code}
                    type="button"
                    onClick={() => handleDomainSelect(domain.code)}
                    aria-pressed={active}
                    className={`rounded border px-3 py-1.5 text-sm transition-colors ${
                      active
                        ? "border-[#0F172A] bg-white font-semibold text-[#0F172A]"
                        : "border-[#E2E8F0] bg-white text-[#475569] hover:border-[#CBD5E1] hover:text-[#0F172A]"
                    }`}
                  >
                    {domain.label}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {error && (
          <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded border border-rose-200 bg-rose-50/70 px-4 py-3 text-sm text-rose-800">
            <div>
              <p className="font-medium">Không thể tải dữ liệu bảng giá</p>
              <p className="mt-0.5 text-xs text-rose-700">{error}</p>
            </div>
            <button
              type="button"
              onClick={() => void loadCityPrices(selectedCity, selectedDomain)}
              className="inline-flex items-center gap-1.5 rounded border border-rose-300 bg-white px-3 py-1.5 text-xs font-medium text-rose-800 hover:bg-rose-50"
            >
              <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.75} />
              Thử lại
            </button>
          </div>
        )}

        <div className="space-y-8">
          {/* 1. Bảng Giá Vàng */}
          {showGold && (
            <section className="rounded border border-[#E2E8F0] bg-white">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#E2E8F0] px-5 py-3.5">
                <div>
                  <h2 className="text-base font-semibold text-[#0F172A]">
                    1. Giá Vàng — {formatCityName(selectedCity)}
                  </h2>
                  <p className="mt-0.5 text-xs text-[#475569]">
                    Đơn vị hiển thị: {goldUnitLabel} (1 lượng = 10 chỉ)
                  </p>
                </div>

                <div
                  role="group"
                  aria-label="Đơn vị quy đổi giá vàng"
                  className="inline-flex rounded border border-[#E2E8F0] bg-[#F8FAFC] p-0.5 text-xs"
                >
                  <button
                    type="button"
                    onClick={() => setGoldUnit("LUONG")}
                    aria-pressed={goldUnit === "LUONG"}
                    className={`rounded-sm px-2.5 py-1 transition-colors ${
                      goldUnit === "LUONG"
                        ? "bg-white font-semibold text-[#0F172A] shadow-xs"
                        : "text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    VND / Lượng
                  </button>
                  <button
                    type="button"
                    onClick={() => setGoldUnit("CHI")}
                    aria-pressed={goldUnit === "CHI"}
                    className={`rounded-sm px-2.5 py-1 transition-colors ${
                      goldUnit === "CHI"
                        ? "bg-white font-semibold text-[#0F172A] shadow-xs"
                        : "text-[#475569] hover:text-[#0F172A]"
                    }`}
                  >
                    VND / Chỉ
                  </button>
                </div>
              </div>

              {loading ? (
                <TableSkeleton columns={5} />
              ) : goldItems.length === 0 ? (
                <p className="px-5 py-8 text-center text-sm text-[#475569]">
                  Chưa có dữ liệu giá vàng đã xác thực cho {formatCityName(selectedCity)}.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-left text-sm">
                    <thead>
                      <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC] text-xs font-medium uppercase tracking-wider text-[#475569]">
                        <th className="px-5 py-3">Thương hiệu &amp; Sản phẩm</th>
                        <th className="px-5 py-3 text-right">Mua vào</th>
                        <th className="px-5 py-3 text-right">Bán ra</th>
                        <th className="px-5 py-3 text-right">Chênh lệch</th>
                        <th className="px-5 py-3 text-right">Xác thực</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E2E8F0]">
                      {goldItems.map((item) => {
                        const spread =
                          item.sell_price !== null && item.buy_price !== null
                            ? item.sell_price - item.buy_price
                            : null;
                        return (
                          <tr
                            key={item.record_id}
                            onClick={() => setEvidenceRecordId(item.record_id)}
                            className="cursor-pointer transition-colors hover:bg-[#F8FAFC]"
                          >
                            <td className="px-5 py-3.5">
                              <span className="font-medium text-[#0F172A]">
                                {formatBrandLabel(item.brand)}
                              </span>
                              <span className="mx-1.5 text-[#CBD5E1]">·</span>
                              <span className="text-[#0F172A]">{item.commodity_name}</span>
                            </td>
                            <td className="tabular-nums px-5 py-3.5 text-right font-medium text-[#0F172A]">
                              {formatVnd(item.buy_price, { divisor: goldDivisor })}
                            </td>
                            <td className="tabular-nums px-5 py-3.5 text-right font-semibold text-[#0F172A]">
                              {formatVnd(item.sell_price, { divisor: goldDivisor })}
                            </td>
                            <td className="tabular-nums px-5 py-3.5 text-right text-[#475569]">
                              {formatVnd(spread, { divisor: goldDivisor })}
                            </td>
                            <td className="px-5 py-3.5 text-right">
                              <button
                                type="button"
                                onClick={(event) => {
                                  event.stopPropagation();
                                  setEvidenceRecordId(item.record_id);
                                }}
                                className="inline-flex items-center gap-1.5 text-left hover:opacity-80"
                              >
                                <VerificationBadge status={item.verification_status} />
                                <ChevronRight
                                  className="h-3.5 w-3.5 text-[#475569]"
                                  strokeWidth={1.75}
                                />
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}

          {/* 2. Bảng Giá Xăng dầu */}
          {showFuel && (
            <section className="rounded border border-[#E2E8F0] bg-white">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#E2E8F0] px-5 py-3.5">
                <div>
                  <h2 className="text-base font-semibold text-[#0F172A]">2. Giá Xăng dầu</h2>
                  <p className="mt-0.5 text-xs text-[#475569]">
                    Áp dụng biểu giá {formatZoneLabel(appliedFuelZone)} cho{" "}
                    {formatCityName(selectedCity)}
                  </p>
                </div>
                <span className="text-xs text-[#475569]">Đơn vị: VND / Lít</span>
              </div>

              {loading ? (
                <TableSkeleton columns={5} />
              ) : fuelItems.length === 0 ? (
                <p className="px-5 py-8 text-center text-sm text-[#475569]">
                  Chưa có dữ liệu giá xăng dầu đã xác thực cho {formatCityName(selectedCity)}.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-left text-sm">
                    <thead>
                      <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC] text-xs font-medium uppercase tracking-wider text-[#475569]">
                        <th className="px-5 py-3">Mặt hàng</th>
                        <th className="px-5 py-3">Đơn vị phân phối</th>
                        <th className="px-5 py-3 text-right">Giá bán lẻ (VND/lít)</th>
                        <th className="px-5 py-3 text-right">Hiệu lực từ</th>
                        <th className="px-5 py-3 text-right">Xác thực</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E2E8F0]">
                      {fuelItems.map((item) => (
                        <tr
                          key={item.record_id}
                          onClick={() => setEvidenceRecordId(item.record_id)}
                          className="cursor-pointer transition-colors hover:bg-[#F8FAFC]"
                        >
                          <td className="px-5 py-3.5">
                            <p className="font-medium text-[#0F172A]">{item.commodity_name}</p>
                            {item.spec_detail && (
                              <p className="text-xs text-[#475569]">{item.spec_detail}</p>
                            )}
                          </td>
                          <td className="px-5 py-3.5 text-[#0F172A]">
                            {formatBrandLabel(item.brand)}
                          </td>
                          <td className="tabular-nums px-5 py-3.5 text-right font-semibold text-[#0F172A]">
                            {formatVnd(item.retail_price)}
                          </td>
                          <td className="tabular-nums px-5 py-3.5 text-right text-xs text-[#475569]">
                            {renderFuelEffective(item)}
                          </td>
                          <td className="px-5 py-3.5 text-right">
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setEvidenceRecordId(item.record_id);
                              }}
                              className="inline-flex items-center gap-1.5 text-left hover:opacity-80"
                            >
                              <VerificationBadge status={item.verification_status} />
                              <ChevronRight
                                className="h-3.5 w-3.5 text-[#475569]"
                                strokeWidth={1.75}
                              />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}

          {/* 3. Bảng Giá Gas */}
          {showGas && (
            <section className="rounded border border-[#E2E8F0] bg-white">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#E2E8F0] px-5 py-3.5">
                <div>
                  <h2 className="text-base font-semibold text-[#0F172A]">
                    3. Giá Gas — {formatCityName(selectedCity)}
                  </h2>
                  <p className="mt-0.5 text-xs text-[#475569]">
                    Bình dân dụng 12kg &amp; công nghiệp 45kg / 48kg (đã gồm VAT)
                  </p>
                </div>
                <span className="text-xs text-[#475569]">Đơn vị: VND / Bình</span>
              </div>

              {loading ? (
                <TableSkeleton columns={4} />
              ) : gasItems.length === 0 ? (
                <p className="px-5 py-8 text-center text-sm text-[#475569]">
                  Chưa có dữ liệu giá gas đã xác thực cho {formatCityName(selectedCity)}.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full border-collapse text-left text-sm">
                    <thead>
                      <tr className="border-b border-[#E2E8F0] bg-[#F8FAFC] text-xs font-medium uppercase tracking-wider text-[#475569]">
                        <th className="px-5 py-3">Thương hiệu &amp; Loại bình</th>
                        <th className="px-5 py-3 text-right">Giá bán lẻ (VND/bình)</th>
                        <th className="px-5 py-3 text-right">Hiệu lực</th>
                        <th className="px-5 py-3 text-right">Xác thực</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-[#E2E8F0]">
                      {gasItems.map((item) => (
                        <tr
                          key={item.record_id}
                          onClick={() => setEvidenceRecordId(item.record_id)}
                          className="cursor-pointer transition-colors hover:bg-[#F8FAFC]"
                        >
                          <td className="px-5 py-3.5">
                            <p className="font-medium text-[#0F172A]">{item.commodity_name}</p>
                            {item.spec_detail && (
                              <p className="text-xs text-[#475569]">{item.spec_detail}</p>
                            )}
                          </td>
                          <td className="tabular-nums px-5 py-3.5 text-right font-semibold text-[#0F172A]">
                            {formatVnd(item.retail_price)}
                          </td>
                          <td className="tabular-nums px-5 py-3.5 text-right text-xs text-[#475569]">
                            {renderGasEffective(item)}
                          </td>
                          <td className="px-5 py-3.5 text-right">
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setEvidenceRecordId(item.record_id);
                              }}
                              className="inline-flex items-center gap-1.5 text-left hover:opacity-80"
                            >
                              <VerificationBadge status={item.verification_status} />
                              <ChevronRight
                                className="h-3.5 w-3.5 text-[#475569]"
                                strokeWidth={1.75}
                              />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </section>
          )}
        </div>
      </main>

      <EvidenceDrawer
        recordId={evidenceRecordId}
        goldUnit={goldUnit}
        onClose={() => setEvidenceRecordId(null)}
      />
    </div>
  );
}

