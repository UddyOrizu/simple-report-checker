import type { ClaimListItem } from "../api/types";
import { VERDICT_STYLES } from "./verdictColors";

interface DocumentViewerProps {
  claims: ClaimListItem[];
  selectedClaimId: string | null;
  onSelectClaim: (claimId: string) => void;
}

/** Source text with sentence-level verdict highlighting. Built from each claim's source_span,
 * since claim extraction (not full raw-text retrieval) is what the API exposes — each
 * extracted sentence renders as a highlighted, clickable span colored by its verdict. */
export function DocumentViewer({ claims, selectedClaimId, onSelectClaim }: DocumentViewerProps) {
  if (claims.length === 0) {
    return <p className="text-gray-500 p-4">No claims extracted for this document yet.</p>;
  }

  return (
    <div className="border rounded-lg p-4 leading-relaxed">
      {claims.map((claim) => {
        const style = claim.final_verdict ? VERDICT_STYLES[claim.final_verdict] : "bg-white text-gray-800 border-gray-200";
        const selected = claim.id === selectedClaimId;
        return (
          <span
            key={claim.id}
            onClick={() => onSelectClaim(claim.id)}
            className={`inline-block border rounded px-1.5 py-0.5 m-0.5 cursor-pointer text-sm ${style} ${
              selected ? "ring-2 ring-blue-500" : ""
            }`}
            title={claim.final_verdict ?? "not yet verified"}
          >
            {claim.claim_text}
          </span>
        );
      })}
    </div>
  );
}
