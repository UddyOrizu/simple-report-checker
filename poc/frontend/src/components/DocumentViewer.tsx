import { useEffect, useMemo, useRef, useState } from "react";
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

// How many claims to paint into the page on first render / per subsequent scroll-triggered
// batch. A page-styled render is heavier per node (highlight marks, borders) than the old chip
// list, so on a document with hundreds of claims we paint incrementally instead of all at once.
const BATCH_SIZE = 60;

// The claims worth a reviewer's attention first, for the "jump to next flagged claim" control —
// a wrong/unverifiable claim or a high-severity finding, not routine supported/info claims.
function isFlagged(claim: ClaimListItem): boolean {
  return claim.severity === "critical" || claim.severity === "major" || claim.final_verdict === "disputed";
}

/** Source text with sentence-level verdict highlighting, styled as a paginated word-processor
 * page so the extracted content reads like the source document rather than a list of chips.
 * Built from each claim's claim_text, since claim extraction (not full raw-text retrieval) is
 * what the API exposes — each extracted sentence renders as a highlighted, clickable mark
 * colored by its verdict, flowing inline like normal prose.
 *
 * On a large document with hundreds of claims, reading linearly to find what needs attention
 * doesn't scale — the filter bar dims non-matching claims in place (rather than removing them,
 * which would break sentence flow), "Jump to next flagged claim" cycles through the
 * highest-severity/disputed claims directly, and claims past the current batch are painted
 * lazily as the reader scrolls toward them (an IntersectionObserver on a sentinel at the bottom
 * of the painted content) rather than all rendered up front. */
export function DocumentViewer({ claims, selectedClaimId, onSelectClaim }: DocumentViewerProps) {
  const [verdictFilter, setVerdictFilter] = useState<Verdict | "all">("all");
  const [severityFilter, setSeverityFilter] = useState<Severity | "all">("all");
  const [scopeFilter, setScopeFilter] = useState<Scope | "all">("all");
  const [flaggedCursor, setFlaggedCursor] = useState(0);
  const [visibleCount, setVisibleCount] = useState(BATCH_SIZE);

  const spanRefs = useRef(new Map<string, HTMLSpanElement>());
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollId = useRef<string | null>(null);

  // A fresh claims array (new document loaded, or a refetch) starts back at the first batch.
  useEffect(() => {
    setVisibleCount(BATCH_SIZE);
  }, [claims]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    if (!sentinel || visibleCount >= claims.length) return;
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setVisibleCount((c) => Math.min(c + BATCH_SIZE, claims.length));
        }
      },
      { rootMargin: "600px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [visibleCount, claims.length]);

  // Once a claim scrolled-to by "jump to flagged" has been painted (its batch grew to include
  // it), scroll to it — deferred from jumpToNextFlagged because the span doesn't exist yet.
  useEffect(() => {
    if (!pendingScrollId.current) return;
    const el = spanRefs.current.get(pendingScrollId.current);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      pendingScrollId.current = null;
    }
  }, [visibleCount]);

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

    const index = claims.findIndex((c) => c.id === claim.id);
    if (index >= visibleCount) {
      pendingScrollId.current = claim.id;
      setVisibleCount(index + 1);
    } else {
      spanRefs.current.get(claim.id)?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  };

  const selectClaim = (claimId: string) => {
    onSelectClaim(claimId);
    spanRefs.current.get(claimId)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const visibleClaims = claims.slice(0, visibleCount);

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

      {/* Gray "canvas" behind a floating white page, like a word processor's editing view. */}
      <div className="bg-gray-200/70 rounded-lg p-4 sm:p-8">
        <div className="bg-white mx-auto max-w-[820px] shadow-md rounded-sm ring-1 ring-black/5">
          <div
            className="px-8 py-10 sm:px-16 sm:py-14 text-[15px] leading-8 text-gray-900"
            style={{ fontFamily: "Georgia, 'Times New Roman', Cambria, serif" }}
          >
            {visibleClaims.map((claim, i) => {
              const verified = claim.final_verdict != null;
              const style = verified ? VERDICT_STYLES[claim.final_verdict as Verdict] : "text-gray-900";
              const selected = claim.id === selectedClaimId;
              const dimmed = filtersActive && !matches(claim);
              return (
                <span key={claim.id}>
                  <span
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
                    className={`rounded-sm px-0.5 cursor-pointer transition-opacity box-decoration-clone ${
                      verified ? `${style} border-b-2` : "border-b border-dashed border-gray-300 hover:bg-gray-50"
                    } ${selected ? "ring-2 ring-blue-500" : ""} ${dimmed ? "opacity-25" : ""}`}
                    title={claim.final_verdict ?? "not yet verified"}
                  >
                    {claim.claim_text}
                  </span>
                  {i < visibleClaims.length - 1 && " "}
                </span>
              );
            })}

            {visibleCount < claims.length && (
              <div ref={sentinelRef} className="text-center text-xs text-gray-400 mt-8 font-sans">
                Loading more of the document…
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
