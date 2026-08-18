import type { UseDocumentEventsResult } from "../hooks/useDocumentEvents";

interface ProgressBarProps {
  events: UseDocumentEventsResult;
}

function currentStage(events: UseDocumentEventsResult): string {
  const names = events.events.map((e) => e.event);
  if (names.includes("verification_complete")) return "Verification complete";
  if (names.includes("claim_verified")) return "Verifying claims";
  if (names.includes("claim_extracted")) return "Extracting claims";
  if (names.includes("ingest_complete")) return "Ingestion complete";
  if (names.includes("ingest_progress")) return "Ingesting";
  return "Queued";
}

/** Shows the named stage and, on long documents, real percentage progress from
 * ingest_progress's pages_done/pages_total — not just a static "processing…" label. */
export function ProgressBar({ events }: ProgressBarProps) {
  const stage = currentStage(events);
  const { ingestProgress } = events;
  const percent = ingestProgress ? Math.round((ingestProgress.pages_done / ingestProgress.pages_total) * 100) : null;
  const claimsVerified = events.events.filter((e) => e.event === "claim_verified").length;
  const claimsExtracted = events.events.filter((e) => e.event === "claim_extracted").length;

  return (
    <div className="max-w-xl mx-auto p-8">
      <p className="text-sm text-gray-500 mb-2">{stage}</p>
      <div className="w-full bg-gray-200 rounded-full h-3 overflow-hidden">
        <div
          className="bg-blue-500 h-3 transition-all duration-300"
          style={{ width: percent !== null ? `${percent}%` : "20%" }}
        />
      </div>
      {percent !== null && ingestProgress && (
        <p className="text-xs text-gray-500 mt-1">
          {ingestProgress.pages_done} / {ingestProgress.pages_total} pages ({percent}%)
        </p>
      )}
      {claimsExtracted > 0 && (
        <p className="text-xs text-gray-500 mt-2">
          {claimsVerified} / {claimsExtracted} claims verified
        </p>
      )}
      <p className="text-xs text-gray-400 mt-2">connection: {events.status}</p>
    </div>
  );
}
