import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getDocument, listClaims, resumeDocument } from "../api/client";
import { ClaimReviewPanel } from "../components/ClaimReviewPanel";
import { DocumentViewer } from "../components/DocumentViewer";
import { ProgressBar } from "../components/ProgressBar";
import { STATUS_STYLES } from "../components/verdictColors";
import { useDocumentEvents } from "../hooks/useDocumentEvents";

// "ingested" is an intermediate state too — claim extraction/verification can still be running
// after ingestion finishes and before the pipeline sets the terminal "complete" status.
const IN_PROGRESS_STATUSES = new Set(["queued", "processing", "ingested"]);

export function DocumentReview() {
  const { documentId } = useParams<{ documentId: string }>();
  const queryClient = useQueryClient();
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
  const [resuming, setResuming] = useState(false);
  const [resumeError, setResumeError] = useState<string | null>(null);
  const events = useDocumentEvents(documentId ?? null);

  const { data: document, isLoading } = useQuery({
    queryKey: ["document", documentId],
    queryFn: () => getDocument(documentId as string),
    enabled: !!documentId,
    // while processing, poll — the SSE stream also drives live updates, but a plain refetch
    // keeps the page correct even if the stream drops
    refetchInterval: (query) => (query.state.data && IN_PROGRESS_STATUSES.has(query.state.data.status) ? 2000 : false),
  });

  const inProgress = document ? IN_PROGRESS_STATUSES.has(document.status) : false;

  const { data: claims } = useQuery({
    queryKey: ["claims", documentId],
    queryFn: () => listClaims(documentId as string),
    enabled: !!documentId && !inProgress,
  });

  const onResume = async () => {
    if (!documentId) return;
    setResuming(true);
    setResumeError(null);
    try {
      await resumeDocument(documentId);
      await queryClient.invalidateQueries({ queryKey: ["document", documentId] });
    } catch (err) {
      setResumeError(err instanceof Error ? err.message : "Resume failed.");
    } finally {
      setResuming(false);
    }
  };

  if (isLoading || !document) return <p className="text-gray-500 p-8">Loading…</p>;

  return (
    <div className="max-w-7xl mx-auto p-8">
      <Link to="/" className="text-sm text-blue-600">
        ← Back to documents
      </Link>

      <div className="flex items-center gap-3 mt-2 mb-6">
        <h1 className="text-2xl font-semibold">{document.filename}</h1>
        <span className={`text-xs rounded px-2 py-0.5 ${STATUS_STYLES[document.status] ?? "bg-gray-100"}`}>
          {document.status}
        </span>
      </div>

      {inProgress ? (
        <ProgressBar events={events} />
      ) : document.status === "failed" ? (
        <div className="flex items-center gap-3">
          <p className="text-red-600">Processing failed for this document.</p>
          <button
            className="border rounded px-3 py-1 text-sm disabled:opacity-50"
            onClick={onResume}
            disabled={resuming}
          >
            {resuming ? "Resuming…" : "Resume processing"}
          </button>
          {resumeError && <p className="text-red-600 text-xs">{resumeError}</p>}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-6 items-start">
          <div>
            <h2 className="text-sm font-medium text-gray-500 mb-2">Document</h2>
            <DocumentViewer claims={claims ?? []} selectedClaimId={selectedClaimId} onSelectClaim={setSelectedClaimId} />
          </div>
          <div className="lg:sticky lg:top-8">
            <h2 className="text-sm font-medium text-gray-500 mb-2">Claim review</h2>
            {selectedClaimId ? (
              <ClaimReviewPanel claimId={selectedClaimId} />
            ) : (
              <p className="text-gray-500 text-sm">Select a claim to review it.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
