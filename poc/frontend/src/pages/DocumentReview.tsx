import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { getDocument, listClaims } from "../api/client";
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
  const [selectedClaimId, setSelectedClaimId] = useState<string | null>(null);
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

  if (isLoading || !document) return <p className="text-gray-500 p-8">Loading…</p>;

  return (
    <div className="max-w-5xl mx-auto p-8">
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
        <p className="text-red-600">Processing failed for this document.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div>
            <h2 className="text-sm font-medium text-gray-500 mb-2">Document</h2>
            <DocumentViewer claims={claims ?? []} selectedClaimId={selectedClaimId} onSelectClaim={setSelectedClaimId} />
          </div>
          <div>
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
