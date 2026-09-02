import { useEffect, useState } from "react";

export interface DocumentEvent {
  event: string;
  [key: string]: unknown;
}

export interface ClaimVerifiedEvent extends DocumentEvent {
  event: "claim_verified";
  claim_id: string;
  final_verdict: "supported" | "contradicted" | "insufficient" | "disputed";
}

export interface IngestProgress {
  pages_done: number;
  pages_total: number;
}

export type ConnectionStatus = "connecting" | "open" | "closed" | "error";

export interface UseDocumentEventsResult {
  claims: ClaimVerifiedEvent[];
  events: DocumentEvent[];
  ingestProgress: IngestProgress | null;
  status: ConnectionStatus;
}

const NAMED_EVENT_TYPES = [
  "ingest_progress",
  "ingest_complete",
  "claim_extracted",
  "claim_verified",
  "verification_complete",
  "ingest_failed",
  "processing_failed",
];

/** Subscribes to a document's SSE stream (Phase 7.2) and updates claim state incrementally as
 * claim_verified events arrive, rather than waiting for the whole document to finish. */
export function useDocumentEvents(documentId: string | null): UseDocumentEventsResult {
  const [claims, setClaims] = useState<ClaimVerifiedEvent[]>([]);
  const [events, setEvents] = useState<DocumentEvent[]>([]);
  const [ingestProgress, setIngestProgress] = useState<IngestProgress | null>(null);
  const [status, setStatus] = useState<ConnectionStatus>("connecting");

  useEffect(() => {
    if (!documentId) return;

    setClaims([]);
    setEvents([]);
    setIngestProgress(null);
    setStatus("connecting");

    const source = new EventSource(`/api/documents/${documentId}/events`);

    source.onopen = () => setStatus("open");
    source.onerror = () => setStatus("error");

    const handle = (raw: MessageEvent<string>) => {
      const parsed = JSON.parse(raw.data) as DocumentEvent;
      setEvents((prev) => [...prev, parsed]);

      if (parsed.event === "ingest_progress") {
        setIngestProgress({
          pages_done: parsed.pages_done as number,
          pages_total: parsed.pages_total as number,
        });
      }
      if (parsed.event === "claim_verified") {
        setClaims((prev) => [...prev, parsed as ClaimVerifiedEvent]);
      }
      if (parsed.event === "verification_complete" || parsed.event === "ingest_failed" || parsed.event === "processing_failed") {
        setStatus("closed");
        source.close();
      }
    };

    for (const type of NAMED_EVENT_TYPES) {
      source.addEventListener(type, handle);
    }

    return () => {
      for (const type of NAMED_EVENT_TYPES) {
        source.removeEventListener(type, handle);
      }
      source.close();
    };
  }, [documentId]);

  return { claims, events, ingestProgress, status };
}
