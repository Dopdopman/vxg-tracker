"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { formatVnDateTime } from "@/lib/format";

interface SiteHeaderProps {
  lastUpdatedVn: string | null;
}

export function SiteHeader({ lastUpdatedVn }: SiteHeaderProps) {
  const pathname = usePathname();
  const isPricesActive = pathname === "/";
  const isForecastActive = pathname.startsWith("/forecast");
  const isAuditActive = pathname.startsWith("/audit");

  return (
    <header className="sticky top-0 z-30 border-b border-[#E2E8F0] bg-white">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-x-6 gap-y-2 px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 sm:gap-8">
          <Link
            href="/"
            className="text-sm font-semibold tracking-wider text-[#0F172A] whitespace-nowrap"
          >
            VXG TRACKER
          </Link>

          <nav
            aria-label="Điều hướng chính"
            className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs sm:gap-5 sm:text-sm"
          >
            <Link
              href="/"
              className={`whitespace-nowrap border-b-2 py-1 transition-colors ${
                isPricesActive
                  ? "border-[#0F172A] font-semibold text-[#0F172A]"
                  : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
              }`}
            >
              Bảng giá
            </Link>
            <Link
              href="/forecast"
              className={`whitespace-nowrap border-b-2 py-1 transition-colors ${
                isForecastActive
                  ? "border-[#0F172A] font-semibold text-[#0F172A]"
                  : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
              }`}
            >
              Phân tích &amp; Dự báo
            </Link>
            <Link
              href="/audit"
              className={`whitespace-nowrap border-b-2 py-1 transition-colors ${
                isAuditActive
                  ? "border-[#0F172A] font-semibold text-[#0F172A]"
                  : "border-transparent font-medium text-[#475569] hover:text-[#0F172A]"
              }`}
            >
              Kiểm toán dữ liệu
            </Link>
          </nav>
        </div>

        <div className="tabular-nums text-xs text-[#475569]">
          <span className="text-[#475569]">Cập nhật gần nhất: </span>
          <span className="font-medium text-[#0F172A]">
            {lastUpdatedVn ? `${formatVnDateTime(lastUpdatedVn)} (UTC+7)` : "Đang chờ dữ liệu (UTC+7)"}
          </span>
        </div>
      </div>
    </header>
  );
}

