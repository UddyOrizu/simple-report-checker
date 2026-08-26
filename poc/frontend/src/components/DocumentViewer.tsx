import { useMemo, useRef, useState } from "react";
import type { ClaimListItem, Scope, Severity, Verdict } from "../api/types";
import { VERDICT_STYLES } from "./verdictColors";

interface DocumentViewerProps {
  claims: ClaimListItem[];
  selectedClaimId: string | null;
  onSelectClaim: (claimId: string) => void;
}

const VERDICT_OPTIONS: Verdict[] = ["supported", "contradicted", "insufficient", "disputed"];
const SEVERITY_OPTIONS: Severity[] = ["critical", "major", "minor", "info"];
const SCOPE_OPTIONS: Scope[] = ["internal", "external", "both"];

// The claims worth a reviewer's attention first, for the "jump to next flagged claim" control —
// a wrong/unverifiable claim or a high-severity finding, not routine supported/info claims.
function isFlagged(claim: ClaimListItem): boolean {
  return claim.severity === "critical" || claim.severity === "major" || claim.final_verdict === "disputed";
}

/** Source text with sentence-level verdict highlighting. Built from each claim's source_span,
 * since claim extraction (not full raw-text retrieval) is what the API exposes — each
 * extracted sentence renders as a highlighted, clickable span colored by its verdict.
 *
 * On a large document with hundreds of claims, reading linearly to find what needs attention
 * doesn't scale — the filter bar dims non-matching claims in place (rather than removing them,
 * which would break sentence flow) and "Jump to next flagged claim" cycles through the
 * highest-severity/disputed claims directly. */
export function DocumentViewer({ claims, selectedClaimId, onSelectClaim }: DocumentViewerProps) {
  const [verdictFilter, setVerdictFilter] = useState<Verdict | "all">("all");
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");
  const [scopeFilter, setScopeFilter] = useState<Scope | "all">("all");
  const [flaggedCursor, setFlaggedCursor] = useState(0);

  const spanRefs = useRef(new Map<string, HTMLSpanElement>());

  const matches = (claim: ClaimListItem) =>
    (verdictFilter === "all" || claim.final_verdict === verdictFilter) &&
    (severityFilter === "all" || claim.severity === severityFilter) &&
    (scopeFilter === "all" || claim.scope === scopeFilter);

  const flaggedClaims = useMemo(() => claims.filter(isFlagged), [claims]);
  const filtersActive = verdictFilter !== "all" || severityFilter !== "all" || scopeFilter !== "all";

  if (claims.length === 0) {
    return <p className="text-gray-500 p-4">No claims extracted for this document yet.</p>;
  }

  const jumpToNextFlagged = () => {
    if (flaggedClaims.length === 0) return;
    const claim = flaggedClaims[flaggedCursor % flaggedClaims.length];
    setFlaggedCursor((i) => i + 1);
    onSelectClaim(claim.id);
    spanRefs.current.get(claim.id)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const selectClaim = (claimId: string) => {
    onSelectClaim(claimId);
    spanRefs.current.get(claimId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <div>
      <div className="flex flex-wrap gap-2 items-center mb-3 text-xs">
        <select
          className="border rounded px-2 py-1"
          value={verdictFilter}
          onChange={(e) => setVerdictFilter(e.target.value as Verdict | "all")}
        >
          <option value="all">All verdicts</option>
          {VERDICT_OPTIONS.map((v) => (
            <option key={v} value={v}>
              {v}
            </option>
          ))}
        </select>
        <select
          className="border rounded px-2 py-1"
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value as Severity | "all")}
        >
          <option value="all">All severities</option>
          {SEVERITY_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select
          className="border rounded px-2 py-1"
          value={scopeFilter}
          onChange={(e) => setScopeFilter(e.target.value as Scope | "all")}
        >
          <option value="all">All scopes</option>
          {SCOPE_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        {filtersActive && (
          <button
            className="border rounded px-2 py-1 text-gray-500"
            onClick={() => {
              setVerdictFilter("all");
              setSeverityFilter("all");
              setScopeFilter("all");
            }}
          >
            Clear filters
          </button>
        )}
        <button
          className="border rounded px-2 py-1 bg-orange-50 border-orange-200 text-orange-700 disabled:opacity-40 disabled:bg-white"
          onClick={jumpToNextFlagged}
          disabled={flaggedClaims.length === 0}
        >
          Jump to next flagged claim ({flaggedClaims.length})
        </button>
      </div>

      <div className="border rounded-lg p-4 leading-relaxed">
        {claims.map((claim) => {
          const style = claim.final_verdict ? VERDICT_STYLES[claim.final_verdict] : "bg-white text-gray-800 border-gray-200";
          const selected = claim.id === selectedClaimId;
          const dimmed = filtersActive && !matches(claim);
          return (
            <span
              key={claim.id}
              ref={(el) => {
                if (el) spanRefs.current.set(claim.id, el);
                else spanRefs.current.delete(claim.id);
              }}
              role="button"
              tabIndex={0}
              onClick={() => selectClaim(claim.id)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  selectClaim(claim.id);
                }
              }}
              className={`inline-block border rounded px-1.5 py-0.5 m-0.5 cursor-pointer text-sm transition-opacity ${style} ${
                selected ? "ring-2 ring-blue-500" : ""
              } ${dimmed ? "opacity-25" : ""}`}
              title={claim.final_verdict ?? "not yet verified"}
            >
              {claim.claim_text}
            </span>
          );
        })}
      </div>
    </div>
  );
}
