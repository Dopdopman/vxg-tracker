import { formatDirectionLabel, formatImpactLabel } from "@/lib/format";
import type {
  AlertStatus,
  CrawlRunStatus,
  FetchStatus,
  ForecastEvaluationType,
  PredictedDirection,
  SourceHealthStatus,
  VerificationStatus,
} from "@/lib/types";

interface BadgeStyle {
  dotClass: string;
  pillClass: string;
  label: string;
}

function StatusPill({ dotClass, pillClass, label }: BadgeStyle) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${pillClass}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${dotClass}`} />
      <span>{label}</span>
    </span>
  );
}

export function VerificationBadge({ status }: { status: VerificationStatus }) {
  if (status === "CROSS_CHECKED") {
    return (
      <StatusPill
        dotClass="bg-emerald-700"
        pillClass="border-emerald-200 bg-emerald-50/70 text-emerald-700"
        label="Đối chiếu chéo"
      />
    );
  }
  if (status === "VERIFIED") {
    return (
      <StatusPill
        dotClass="bg-amber-700"
        pillClass="border-amber-200 bg-amber-50/70 text-amber-700"
        label="Xác thực nguồn chính"
      />
    );
  }
  return (
    <StatusPill
      dotClass="bg-rose-700"
      pillClass="border-rose-200 bg-rose-50/70 text-rose-700"
      label={status}
    />
  );
}

export function RunStatusBadge({ status }: { status: CrawlRunStatus }) {
  const styles: Record<CrawlRunStatus, BadgeStyle> = {
    COMPLETED: {
      dotClass: "bg-emerald-700",
      pillClass: "border-emerald-200 bg-emerald-50/70 text-emerald-700",
      label: "Hoàn tất",
    },
    PARTIAL_SUCCESS: {
      dotClass: "bg-amber-700",
      pillClass: "border-amber-200 bg-amber-50/70 text-amber-700",
      label: "Hoàn tất một phần",
    },
    RUNNING: {
      dotClass: "bg-amber-700",
      pillClass: "border-amber-200 bg-amber-50/70 text-amber-700",
      label: "Đang chạy",
    },
    QUEUED: {
      dotClass: "bg-slate-500",
      pillClass: "border-[#E2E8F0] bg-[#F8FAFC] text-[#475569]",
      label: "Đang chờ",
    },
    FAILED: {
      dotClass: "bg-rose-700",
      pillClass: "border-rose-200 bg-rose-50/70 text-rose-700",
      label: "Thất bại",
    },
  };
  return <StatusPill {...styles[status]} />;
}

export function SourceHealthBadge({ status }: { status: SourceHealthStatus }) {
  const styles: Record<SourceHealthStatus, BadgeStyle> = {
    HEALTHY: {
      dotClass: "bg-emerald-700",
      pillClass: "border-emerald-200 bg-emerald-50/70 text-emerald-700",
      label: "HEALTHY · Ổn định",
    },
    DEGRADED: {
      dotClass: "bg-amber-700",
      pillClass: "border-amber-200 bg-amber-50/70 text-amber-700",
      label: "DEGRADED · Suy giảm",
    },
    DOWN: {
      dotClass: "bg-rose-700",
      pillClass: "border-rose-200 bg-rose-50/70 text-rose-700",
      label: "DOWN · Gián đoạn",
    },
    UNKNOWN: {
      dotClass: "bg-slate-400",
      pillClass: "border-[#E2E8F0] bg-[#F8FAFC] text-[#475569]",
      label: "UNKNOWN · Chưa rõ",
    },
  };
  return <StatusPill {...styles[status]} />;
}

export function FetchStatusBadge({ status }: { status: FetchStatus }) {
  if (status === "SUCCESS") {
    return (
      <StatusPill
        dotClass="bg-emerald-700"
        pillClass="border-emerald-200 bg-emerald-50/70 text-emerald-700"
        label="Thành công"
      />
    );
  }
  const labels: Record<Exclude<FetchStatus, "SUCCESS">, string> = {
    HTTP_ERROR: "Lỗi HTTP",
    TIMEOUT: "Quá thời gian",
    PARSE_ERROR: "Lỗi bóc tách",
    SCHEMA_DRIFT: "Thay đổi cấu trúc",
    BLOCKED: "Bị chặn WAF",
  };
  return (
    <StatusPill
      dotClass="bg-rose-700"
      pillClass="border-rose-200 bg-rose-50/70 text-rose-700"
      label={labels[status]}
    />
  );
}

export function AlertStatusBadge({ status }: { status: AlertStatus }) {
  if (status === "RESOLVED") {
    return (
      <StatusPill
        dotClass="bg-emerald-700"
        pillClass="border-emerald-200 bg-emerald-50/70 text-emerald-700"
        label="Đã xử lý"
      />
    );
  }
  if (status === "ACKNOWLEDGED") {
    return (
      <StatusPill
        dotClass="bg-amber-700"
        pillClass="border-amber-200 bg-amber-50/70 text-amber-700"
        label="Đã ghi nhận"
      />
    );
  }
  return (
    <StatusPill
      dotClass="bg-rose-700"
      pillClass="border-rose-200 bg-rose-50/70 text-rose-700"
      label="Đang mở"
    />
  );
}

export function DirectionBadge({ direction }: { direction: PredictedDirection }) {
  const styles: Record<PredictedDirection, BadgeStyle> = {
    UP: {
      dotClass: "bg-emerald-700",
      pillClass: "border-emerald-200 bg-emerald-50/70 text-emerald-700",
      label: formatDirectionLabel("UP"),
    },
    DOWN: {
      dotClass: "bg-rose-700",
      pillClass: "border-rose-200 bg-rose-50/70 text-rose-700",
      label: formatDirectionLabel("DOWN"),
    },
    FLAT: {
      dotClass: "bg-slate-500",
      pillClass: "border-[#E2E8F0] bg-[#F8FAFC] text-[#475569]",
      label: formatDirectionLabel("FLAT"),
    },
    UNKNOWN: {
      dotClass: "bg-amber-700",
      pillClass: "border-amber-200 bg-amber-50/70 text-amber-700",
      label: formatDirectionLabel("UNKNOWN"),
    },
  };
  return <StatusPill {...styles[direction]} />;
}

export function ImpactBadge({ level }: { level: string }) {
  if (level === "HIGH") {
    return (
      <StatusPill
        dotClass="bg-rose-700"
        pillClass="border-rose-200 bg-rose-50/70 text-rose-700"
        label={`Tác động: ${formatImpactLabel(level)}`}
      />
    );
  }
  if (level === "MEDIUM") {
    return (
      <StatusPill
        dotClass="bg-amber-700"
        pillClass="border-amber-200 bg-amber-50/70 text-amber-700"
        label={`Tác động: ${formatImpactLabel(level)}`}
      />
    );
  }
  return (
    <StatusPill
      dotClass="bg-slate-500"
      pillClass="border-[#E2E8F0] bg-[#F8FAFC] text-[#475569]"
      label={`Tác động: ${formatImpactLabel(level)}`}
    />
  );
}

export function EvaluationTypeBadge({ type }: { type: ForecastEvaluationType }) {
  if (type === "LIVE") {
    return (
      <StatusPill
        dotClass="bg-emerald-700"
        pillClass="border-emerald-200 bg-emerald-50/70 text-emerald-700"
        label="LIVE"
      />
    );
  }
  return (
    <StatusPill
      dotClass="bg-slate-500"
      pillClass="border-[#E2E8F0] bg-[#F8FAFC] text-[#475569]"
      label="BACKTEST"
    />
  );
}
