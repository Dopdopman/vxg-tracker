import { AuditDashboard } from "@/components/audit-dashboard";
import {
  fetchAnomalies,
  fetchAuditSummary,
  fetchCrawlRuns,
  fetchSourcesHealth,
} from "@/lib/api";

export const dynamic = "force-dynamic";

function normalizeParam(value: string | string[] | undefined): string | null {
  if (Array.isArray(value)) {
    return value[0] ?? null;
  }
  return value ?? null;
}

export default async function AuditPage({
  searchParams,
}: {
  searchParams: Promise<{ runId?: string | string[]; tab?: string | string[] }>;
}) {
  const params = await searchParams;
  const initialRunId = normalizeParam(params.runId);
  const rawTab = normalizeParam(params.tab);
  const initialTab =
    rawTab === "health" || rawTab === "anomalies" ? rawTab : "runs";

  const [summaryRes, runsRes, healthRes, anomaliesRes] = await Promise.allSettled([
    fetchAuditSummary(),
    fetchCrawlRuns({ limit: 15, offset: 0 }),
    fetchSourcesHealth(),
    fetchAnomalies({ status: "OPEN", limit: 50, offset: 0 }),
  ]);

  const firstFailure = [summaryRes, runsRes, healthRes, anomaliesRes].find(
    (res): res is PromiseRejectedResult => res.status === "rejected"
  );

  const initialError = firstFailure
    ? firstFailure.reason instanceof Error
      ? firstFailure.reason.message
      : "Không thể kết nối máy chủ kiểm toán."
    : null;

  return (
    <AuditDashboard
      initialSummary={summaryRes.status === "fulfilled" ? summaryRes.value : null}
      initialRuns={runsRes.status === "fulfilled" ? runsRes.value : null}
      initialHealth={healthRes.status === "fulfilled" ? healthRes.value : null}
      initialAnomalies={anomaliesRes.status === "fulfilled" ? anomaliesRes.value : null}
      initialRunId={initialRunId}
      initialTab={initialTab}
      initialError={initialError}
    />
  );
}

