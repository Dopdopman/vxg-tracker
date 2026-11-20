import type {
  AlertSeverity,
  AlertType,
  CityCode,
  CommodityCategory,
  CrawlScope,
  ForecastHorizon,
  HistoryRange,
  NewsImpactLevel,
  PredictedDirection,
  TriggerType,
} from "./types";

const VN_TIMEZONE = "Asia/Ho_Chi_Minh";

const _vnDateTimeFormatter = new Intl.DateTimeFormat("en-GB", {
  timeZone: VN_TIMEZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

function formatIntegerWithDots(value: number): string {
  const negative = value < 0;
  const rounded = Math.round(Math.abs(value));
  const grouped = String(rounded).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return negative ? `-${grouped}` : grouped;
}

export function formatVnd(
  amount: number | null | undefined,
  options?: { divisor?: number; includeSymbol?: boolean }
): string {
  if (amount === null || amount === undefined || Number.isNaN(amount)) {
    return "—";
  }
  const divisor = options?.divisor ?? 1;
  const scaled = amount / divisor;
  const text = formatIntegerWithDots(scaled);
  return options?.includeSymbol === false ? text : `${text} ₫`;
}

export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return formatIntegerWithDots(value);
}

function extractVnParts(iso: string): Record<string, string> | null {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return null;
  }
  const parts = _vnDateTimeFormatter.formatToParts(parsed);
  const map: Record<string, string> = {};
  for (const part of parts) {
    if (part.type !== "literal") {
      map[part.type] = part.value;
    }
  }
  return map;
}

export function formatVnDateTime(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const parts = extractVnParts(iso);
  if (!parts) {
    return iso;
  }
  return `${parts.hour}:${parts.minute} · ${parts.day}/${parts.month}/${parts.year}`;
}

export function formatVnDateTimeFull(iso: string | null | undefined): string {
  if (!iso) {
    return "—";
  }
  const parts = extractVnParts(iso);
  if (!parts) {
    return iso;
  }
  return `${parts.hour}:${parts.minute}:${parts.second} · ${parts.day}/${parts.month}/${parts.year} (UTC+7)`;
}

export function formatVnDate(isoDate: string | null | undefined): string {
  if (!isoDate) {
    return "—";
  }
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(isoDate.trim());
  if (!match) {
    return isoDate;
  }
  return `${match[3]}/${match[2]}/${match[1]}`;
}

export function formatPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  return `${value.toFixed(digits)}%`;
}

export function formatLatencyMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) {
    return "—";
  }
  return `${formatIntegerWithDots(ms)} ms`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) {
    return "—";
  }
  if (ms < 1000) {
    return `${ms} ms`;
  }
  return `${(ms / 1000).toFixed(1).replace(".", ",")} s`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) {
    return "—";
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  return `${(bytes / 1024).toFixed(1).replace(".", ",")} KB`;
}

const CITY_NAMES: Record<CityCode, string> = {
  HN: "Hà Nội",
  DN: "Đà Nẵng",
  HCM: "TP. Hồ Chí Minh",
};

export function formatCityName(code: string): string {
  if (code in CITY_NAMES) {
    return CITY_NAMES[code as CityCode];
  }
  return code;
}

const BRAND_LABELS: Record<string, string> = {
  SJC: "SJC",
  DOJI: "DOJI",
  PNJ: "PNJ",
  PETROLIMEX: "Petrolimex",
  SAIGON_PETRO: "Saigon Petro",
  PVOIL: "PVOIL",
  PETROLIMEX_GAS: "Petrolimex Gas",
  PV_GAS_LPG: "PV Gas LPG",
};

export function formatBrandLabel(brand: string): string {
  return BRAND_LABELS[brand] ?? brand;
}

export function formatZoneLabel(zone: string | null | undefined): string {
  if (zone === "VUNG_1") {
    return "Vùng 1";
  }
  if (zone === "VUNG_2") {
    return "Vùng 2";
  }
  return zone ?? "Vùng niêm yết";
}

const DOMAIN_LABELS: Record<CommodityCategory, string> = {
  GOLD: "Vàng",
  FUEL: "Xăng dầu",
  GAS: "Gas",
};

export function formatDomainLabel(domain: CommodityCategory): string {
  return DOMAIN_LABELS[domain];
}

const SCOPE_LABELS: Record<CrawlScope, string> = {
  ALL: "Tất cả",
  GOLD: "Vàng",
  FUEL: "Xăng dầu",
  GAS: "Gas",
};

export function formatScopeLabel(scope: CrawlScope): string {
  return SCOPE_LABELS[scope];
}

const TRIGGER_LABELS: Record<TriggerType, string> = {
  SCHEDULED: "Định kỳ",
  MANUAL_ADMIN: "Thủ công",
  RETRY: "Thử lại",
};

export function formatTriggerLabel(trigger: TriggerType): string {
  return TRIGGER_LABELS[trigger];
}

const ALERT_TYPE_LABELS: Record<AlertType, string> = {
  SCHEMA_DRIFT: "Thay đổi cấu trúc nguồn",
  SOURCE_DOWN_3X: "Nguồn lỗi 3 lần liên tiếp",
  PRICE_SPIKE: "Biến động giá vượt ngưỡng",
  CROSS_CHECK_MISMATCH: "Sai lệch đối chiếu chéo",
  VALIDATION_ERROR: "Lỗi kiểm chứng dữ liệu",
};

export function formatAlertTypeLabel(type: AlertType): string {
  return ALERT_TYPE_LABELS[type];
}

const SEVERITY_LABELS: Record<AlertSeverity, string> = {
  LOW: "Thấp",
  MEDIUM: "Trung bình",
  HIGH: "Cao",
  CRITICAL: "Nghiêm trọng",
};

export function formatSeverityLabel(severity: AlertSeverity): string {
  return SEVERITY_LABELS[severity];
}

export function formatSignedVnd(
  delta: number | null | undefined,
  options?: { divisor?: number; includeSymbol?: boolean }
): string {
  if (delta === null || delta === undefined || Number.isNaN(delta)) {
    return "—";
  }
  const divisor = options?.divisor ?? 1;
  const scaled = Math.round(delta / divisor);
  if (scaled === 0) {
    return options?.includeSymbol === false ? "0" : "0 ₫";
  }
  const sign = scaled > 0 ? "+" : "-";
  const text = formatIntegerWithDots(Math.abs(scaled));
  return options?.includeSymbol === false ? `${sign}${text}` : `${sign}${text} ₫`;
}

export function formatSignedPercent(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

const HORIZON_LABELS: Record<ForecastHorizon, string> = {
  T_PLUS_1: "T+1 (1 ngày)",
  T_PLUS_7: "T+7 (7 ngày)",
  NEXT_FUEL_CYCLE: "Kỳ điều hành tới",
  NEXT_GAS_MONTH: "Kỳ đầu tháng tới",
};

export function formatHorizonLabel(horizon: ForecastHorizon): string {
  return HORIZON_LABELS[horizon];
}

const DIRECTION_LABELS: Record<PredictedDirection, string> = {
  UP: "Xu hướng tăng",
  DOWN: "Xu hướng giảm",
  FLAT: "Đi ngang",
  UNKNOWN: "Chưa đủ dữ liệu",
};

export function formatDirectionLabel(direction: PredictedDirection): string {
  return DIRECTION_LABELS[direction];
}

const IMPACT_LABELS: Record<NewsImpactLevel, string> = {
  HIGH: "Cao",
  MEDIUM: "Trung bình",
  LOW: "Thấp",
};

export function formatImpactLabel(level: string): string {
  if (level in IMPACT_LABELS) {
    return IMPACT_LABELS[level as NewsImpactLevel];
  }
  return level;
}

const CATALYST_LABELS: Record<string, string> = {
  FED_RATE: "Lãi suất Fed",
  OPEC_SUPPLY: "Nguồn cung OPEC+",
  GEOPOLITICAL: "Địa chính trị",
  USD_FX: "Tỷ giá USD",
  CP_ARAMCO: "Giá CP Aramco",
  VN_POLICY: "Điều hành trong nước",
  INFLATION: "Lạm phát",
  INVENTORY: "Tồn kho năng lượng",
};

export function formatCatalystLabel(code: string): string {
  return CATALYST_LABELS[code] ?? code;
}

const RANGE_LABELS: Record<HistoryRange, string> = {
  "30d": "30N",
  "90d": "90N",
  "1y": "1N",
  "2y": "2N",
};

export function formatRangeLabel(range: HistoryRange): string {
  return RANGE_LABELS[range];
}

export function formatMacroValue(value: number | null | undefined, unit: string): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return "—";
  }
  if (unit.includes("VND")) {
    return `${formatIntegerWithDots(value)} ₫`;
  }
  if (unit.includes("USD")) {
    return `$${value.toLocaleString("en-US", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }
  if (unit === "PCT") {
    return formatSignedPercent(value, 2);
  }
  return value.toFixed(2);
}

