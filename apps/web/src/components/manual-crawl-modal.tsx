"use client";

import { useEffect, useRef, useState } from "react";
import { KeyRound, Loader2, Play, Trash2, X } from "lucide-react";
import { ApiError, fetchCrawlRunDetail, triggerManualCrawlRun } from "@/lib/api";
import { formatDuration, formatScopeLabel } from "@/lib/format";
import type { CrawlRunDetailResponse, CrawlScope, TriggerCrawlRunResponse } from "@/lib/types";
import { RunStatusBadge } from "./status-badge";

interface ManualCrawlModalProps {
  open: boolean;
  onClose: () => void;
  onCompleted: () => void;
  onInspectRun: (runId: string) => void;
}

const SESSION_KEY_STORAGE = "vxg_admin_session_key";
const MAX_POLL_DURATION_MS = 240_000; // 4 minutes safe window covering cold start + GitHub Actions

const SCOPES: readonly { code: CrawlScope; label: string; hint: string }[] = [
  { code: "ALL", label: "Tất cả (ALL)", hint: "11 nguồn Vàng, Xăng dầu, Gas" },
  { code: "GOLD", label: "Vàng (GOLD)", hint: "SJC, DOJI, PNJ & CafeF" },
  { code: "FUEL", label: "Xăng dầu (FUEL)", hint: "Petrolimex, Saigon Petro & Webgia" },
  { code: "GAS", label: "Gas (GAS)", hint: "Petrolimex Gas, Saigon Petro, PV Gas & VietNamBiz" },
];

interface ConflictInfo {
  message: string;
  activeRunId: string | null;
  activeRunCode: string | null;
  activeStatus: string | null;
}

export function ManualCrawlModal({
  open,
  onClose,
  onCompleted,
  onInspectRun,
}: ManualCrawlModalProps) {
  const [scope, setScope] = useState<CrawlScope>("GOLD");
  const [adminKey, setAdminKey] = useState("");
  const [hasSavedKey, setHasSavedKey] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [conflictInfo, setConflictInfo] = useState<ConflictInfo | null>(null);
  const [queuedRun, setQueuedRun] = useState<TriggerCrawlRunResponse | null>(null);
  const [liveDetail, setLiveDetail] = useState<CrawlRunDetailResponse | null>(null);
  const [elapsedSeconds, setElapsedSeconds] = useState(0);
  const [pollTimedOut, setPollTimedOut] = useState(false);
  const pollTimerRef = useRef<number | null>(null);
  const pollStartedAtRef = useRef<number>(0);

  const stopPolling = () => {
    if (pollTimerRef.current !== null) {
      window.clearInterval(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  };

  useEffect(() => {
    if (!open) {
      stopPolling();
      return;
    }

    try {
      const saved = window.sessionStorage.getItem(SESSION_KEY_STORAGE) ?? "";
      setAdminKey(saved);
      setHasSavedKey(saved.length > 0);
    } catch {
      setAdminKey("");
      setHasSavedKey(false);
    }
    setErrorMessage(null);
    setConflictInfo(null);
    setPollTimedOut(false);
  }, [open]);

  useEffect(() => {
    return () => {
      stopPolling();
    };
  }, []);

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

  const handleKeyChange = (value: string) => {
    setAdminKey(value);
    try {
      if (value.trim().length > 0) {
        window.sessionStorage.setItem(SESSION_KEY_STORAGE, value);
        setHasSavedKey(true);
      } else {
        window.sessionStorage.removeItem(SESSION_KEY_STORAGE);
        setHasSavedKey(false);
      }
    } catch {
      setHasSavedKey(false);
    }
  };

  const handleClearKey = () => {
    setAdminKey("");
    setHasSavedKey(false);
    try {
      window.sessionStorage.removeItem(SESSION_KEY_STORAGE);
    } catch {
      // Ignore storage errors cleanly
    }
  };

  const startPollingRun = (runId: string) => {
    stopPolling();
    pollStartedAtRef.current = Date.now();
    setElapsedSeconds(0);
    setPollTimedOut(false);

    const pollOnce = async () => {
      const elapsedMs = Date.now() - pollStartedAtRef.current;
      setElapsedSeconds(Math.floor(elapsedMs / 1000));

      if (elapsedMs >= MAX_POLL_DURATION_MS) {
        stopPolling();
        setPollTimedOut(true);
        return;
      }

      try {
        const detail = await fetchCrawlRunDetail(runId);
        setLiveDetail(detail);
        if (
          detail.status === "COMPLETED" ||
          detail.status === "PARTIAL_SUCCESS" ||
          detail.status === "FAILED"
        ) {
          stopPolling();
          onCompleted();
        }
      } catch {
        // Keep polling gracefully on transient network blip or Render wakeup
      }
    };

    void pollOnce();
    pollTimerRef.current = window.setInterval(() => {
      void pollOnce();
    }, 3000);
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setErrorMessage(null);
    setConflictInfo(null);
    setQueuedRun(null);
    setLiveDetail(null);
    setPollTimedOut(false);
    setElapsedSeconds(0);
    stopPolling();

    const trimmed = adminKey.trim();
    if (!trimmed) {
      setErrorMessage("Vui lòng nhập khóa quản trị trước khi kích hoạt.");
      return;
    }

    try {
      window.sessionStorage.setItem(SESSION_KEY_STORAGE, trimmed);
      setHasSavedKey(true);
    } catch {
      // Ignore storage availability
    }

    setSubmitting(true);
    try {
      const response = await triggerManualCrawlRun(scope, trimmed);
      setQueuedRun(response);
      startPollingRun(response.run_id);
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 401) {
          setErrorMessage("Thiếu khóa quản trị (HTTP 401). Vui lòng nhập khóa quản trị.");
        } else if (err.status === 403) {
          setErrorMessage("Khóa quản trị không chính xác (HTTP 403). Vui lòng kiểm tra lại.");
        } else if (err.status === 409) {
          const details =
            err.details && typeof err.details === "object"
              ? (err.details as Record<string, unknown>)
              : {};
          setConflictInfo({
            message: `Đang có phiên thu thập trùng phạm vi "${formatScopeLabel(scope)}".`,
            activeRunId:
              typeof details.active_run_id === "string" ? details.active_run_id : null,
            activeRunCode:
              typeof details.active_run_code === "string" ? details.active_run_code : null,
            activeStatus:
              typeof details.active_status === "string" ? details.active_status : null,
          });
        } else if (err.status === 502) {
          setErrorMessage(
            "Không thể kích hoạt GitHub Actions Worker (HTTP 502). Hệ thống đã đánh dấu phiên này là FAILED để không kẹt hàng đợi."
          );
        } else {
          setErrorMessage(err.message);
        }
      } else {
        setErrorMessage("Không thể gửi yêu cầu tới máy chủ API.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const currentStatus = liveDetail?.status ?? queuedRun?.status ?? null;
  const isTrackingActive =
    !pollTimedOut && (currentStatus === "QUEUED" || currentStatus === "RUNNING");

  const statusHelperText = () => {
    if (pollTimedOut) {
      return "Phiên vẫn đang tiếp tục chạy nền trên Worker/GitHub Actions. Bạn có thể đóng hộp thoại và bấm Làm mới sau ít phút.";
    }
    if (currentStatus === "QUEUED") {
      return `Đang chờ Worker / GitHub Actions khởi động (~15–30s)... (đã chờ ${elapsedSeconds}s)`;
    }
    if (currentStatus === "RUNNING") {
      return `Worker đang thu thập & kiểm chứng dữ liệu... (đã chạy ${elapsedSeconds}s)`;
    }
    if (currentStatus === "FAILED") {
      return "Phiên thu thập kết thúc với trạng thái FAILED.";
    }
    return "Phiên thu thập đã hoàn tất.";
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/30 px-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="manual-crawl-modal-title"
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded border border-[#E2E8F0] bg-white"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-[#E2E8F0] px-5 py-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-wider text-[#475569]">
              Tác vụ quản trị
            </p>
            <h2
              id="manual-crawl-modal-title"
              className="mt-0.5 text-base font-semibold text-[#0F172A]"
            >
              Chạy cào dữ liệu thủ công
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Đóng hộp thoại chạy cào thủ công"
            className="rounded border border-[#E2E8F0] p-1.5 text-[#475569] hover:bg-[#F8FAFC] hover:text-[#0F172A]"
          >
            <X className="h-4 w-4" strokeWidth={1.75} />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-5 px-5 py-5 text-sm">
          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#475569]">
              1. Chọn phạm vi thu thập
            </p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              {SCOPES.map((option) => {
                const selected = scope === option.code;
                return (
                  <button
                    key={option.code}
                    type="button"
                    onClick={() => setScope(option.code)}
                    className={`rounded border px-3 py-2.5 text-left transition-colors ${
                      selected
                        ? "border-[#0F172A] bg-[#F8FAFC]"
                        : "border-[#E2E8F0] bg-white hover:border-[#CBD5E1]"
                    }`}
                  >
                    <p className="font-medium text-[#0F172A]">{option.label}</p>
                    <p className="mt-0.5 text-xs text-[#475569]">{option.hint}</p>
                  </button>
                );
              })}
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-center justify-between">
              <label
                htmlFor="manual-admin-key-input"
                className="text-xs font-semibold uppercase tracking-wider text-[#475569]"
              >
                2. Khóa quản trị (lưu tạm trong phiên trình duyệt)
              </label>
              {hasSavedKey && (
                <button
                  type="button"
                  onClick={handleClearKey}
                  className="inline-flex items-center gap-1 text-xs font-medium text-rose-700 hover:underline"
                >
                  <Trash2 className="h-3 w-3" strokeWidth={1.75} />
                  Xóa khóa đã lưu
                </button>
              )}
            </div>
            <div className="relative">
              <KeyRound
                className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-[#475569]"
                strokeWidth={1.75}
              />
              <input
                id="manual-admin-key-input"
                type="password"
                autoComplete="off"
                value={adminKey}
                onChange={(event) => handleKeyChange(event.target.value)}
                placeholder="Nhập khóa xác thực X-Admin-API-Key"
                className="w-full rounded border border-[#E2E8F0] bg-white py-2 pr-3 pl-9 text-sm text-[#0F172A] placeholder:text-[#94A3B8] focus:border-[#0F172A] focus:outline-none"
              />
            </div>
            <p className="mt-1.5 text-xs text-[#475569]">
              Khóa chỉ được lưu tạm thời trong <span className="font-mono">sessionStorage</span> của
              tab hiện tại và tự xóa khi đóng trình duyệt.
            </p>
          </div>

          {submitting && (
            <div className="rounded border border-[#E2E8F0] bg-[#F8FAFC] px-3.5 py-2.5 text-xs text-[#475569]">
              Đang gửi lệnh kích hoạt tới API (nếu API trên Render vừa thức dậy, bước này có thể
              mất 25–45 giây)...
            </div>
          )}

          {errorMessage && (
            <div className="rounded border border-rose-200 bg-rose-50/70 px-3.5 py-2.5 text-xs text-rose-800">
              {errorMessage}
            </div>
          )}

          {conflictInfo && (
            <div className="rounded border border-amber-200 bg-amber-50/70 px-3.5 py-3 text-xs text-amber-900">
              <p className="font-medium">{conflictInfo.message}</p>
              {conflictInfo.activeRunCode && (
                <p className="tabular-nums mt-1 text-amber-800">
                  Phiên đang hoạt động:{" "}
                  <span className="font-mono font-semibold">{conflictInfo.activeRunCode}</span> (
                  {conflictInfo.activeStatus})
                </p>
              )}
              {conflictInfo.activeRunId && (
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    onInspectRun(conflictInfo.activeRunId ?? "");
                  }}
                  className="mt-2 font-medium text-[#0F172A] underline underline-offset-4"
                >
                  Xem tiến trình phiên đang chạy
                </button>
              )}
            </div>
          )}

          {queuedRun && (
            <div className="rounded border border-[#E2E8F0] bg-[#F8FAFC] p-3.5 text-xs">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-mono font-semibold text-[#0F172A]">{queuedRun.run_code}</p>
                  <p className="tabular-nums mt-0.5 text-[#475569]">{statusHelperText()}</p>
                </div>
                {currentStatus && <RunStatusBadge status={currentStatus} />}
              </div>

              {liveDetail && (
                <div className="tabular-nums mt-3 grid grid-cols-3 gap-2 border-t border-[#E2E8F0] pt-2.5 text-[#475569]">
                  <span>
                    OK/Lỗi:{" "}
                    <strong className="text-[#0F172A]">
                      {liveDetail.pages_fetched_ok}/{liveDetail.pages_fetched_failed}
                    </strong>
                  </span>
                  <span>
                    Xác thực:{" "}
                    <strong className="text-emerald-700">{liveDetail.records_verified}</strong>
                  </span>
                  <span>
                    Thời lượng:{" "}
                    <strong className="text-[#0F172A]">
                      {formatDuration(liveDetail.duration_ms)}
                    </strong>
                  </span>
                </div>
              )}

              {!isTrackingActive && (
                <button
                  type="button"
                  onClick={() => {
                    onClose();
                    onInspectRun(queuedRun.run_id);
                  }}
                  className="mt-3 font-medium text-[#0F172A] underline underline-offset-4"
                >
                  Mở nhật ký chi tiết từng nguồn
                </button>
              )}
            </div>
          )}

          <div className="flex items-center justify-end gap-2.5 border-t border-[#E2E8F0] pt-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-[#E2E8F0] bg-white px-3.5 py-2 text-xs font-medium text-[#475569] hover:text-[#0F172A]"
            >
              Đóng
            </button>
            <button
              type="submit"
              disabled={submitting || isTrackingActive}
              className="inline-flex items-center gap-1.5 rounded bg-[#0F172A] px-4 py-2 text-xs font-medium text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {submitting || isTrackingActive ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" strokeWidth={1.75} />
                  <span>{isTrackingActive ? "Đang chạy..." : "Đang gửi..."}</span>
                </>
              ) : (
                <>
                  <Play className="h-3.5 w-3.5" strokeWidth={1.75} />
                  <span>Kích hoạt phiên cào</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
