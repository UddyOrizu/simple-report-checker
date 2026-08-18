import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getClaim, reverifyClaim } from "../api/client";
import { SEVERITY_STYLES, VERDICT_STYLES } from "./verdictColors";

interface ClaimReviewPanelProps {
  claimId: string;
}

/** Per-claim detail: raw chunk text, scope/domain + which tier resolved it, full verifier and
 * challenger reasoning side by side (not collapsed), tool calls, and for deterministic claims
 * the actual recomputed figures. Deliberately more detailed than a production UI — the goal is
 * to see *why*, not just get a clean verdict. */
export function ClaimReviewPanel({ claimId }: ClaimReviewPanelProps) {
  const queryClient = useQueryClient();
  const [reverifying, setReverifying] = useState(false);
  const [reverifyError, setReverifyError] = useState<string | null>(null);

  const { data: claim, isLoading, error } = useQuery({
    queryKey: ["claim", claimId],
    queryFn: () => getClaim(claimId),
  });

  const onReverify = async () => {
    setReverifying(true);
    setReverifyError(null);
    try {
      await reverifyClaim(claimId);
      await queryClient.invalidateQueries({ queryKey: ["claim", claimId] });
    } catch (err) {
      setReverifyError(err instanceof Error ? err.message : "Reverify failed.");
    } finally {
      setReverifying(false);
    }
  };

  if (isLoading) return <p className="text-gray-500 p-4">Loading claim…</p>;
  if (error || !claim) return <p className="text-red-600 p-4">Failed to load claim.</p>;

  const verdict = claim.verdict;
  const isDeterministic = verdict?.resolved_by === "deterministic";

  return (
    <div className="border rounded-lg p-4 space-y-4">
      <div className="flex justify-between items-start gap-4">
        <p className="font-medium">{claim.claim_text}</p>
        <button
          className="text-xs border rounded px-2 py-1 whitespace-nowrap disabled:opacity-50"
          onClick={onReverify}
          disabled={reverifying}
        >
          {reverifying ? "Reverifying…" : "Reverify"}
        </button>
      </div>

      {reverifyError && <p className="text-red-600 text-xs">{reverifyError}</p>}

      <div className="flex flex-wrap gap-2 text-xs">
        <span className="border rounded px-2 py-0.5">type: {claim.claim_type}</span>
        <span className="border rounded px-2 py-0.5">scope: {claim.scope}</span>
        <span className="border rounded px-2 py-0.5">
          domain: {claim.domain ?? "—"} (resolved via {claim.domain_source ?? "—"}, confidence{" "}
          {claim.domain_confidence?.toFixed(2) ?? "—"})
        </span>
      </div>

      <div>
        <p className="text-xs text-gray-500 mb-1">Source text</p>
        <p className="text-sm bg-gray-50 border rounded p-2">{claim.source_span}</p>
      </div>

      {verdict && (
        <div className="flex flex-wrap gap-2 items-center">
          <span className={`text-sm border rounded px-2 py-0.5 ${VERDICT_STYLES[verdict.final_verdict]}`}>
            {verdict.final_verdict}
          </span>
          <span className={`text-xs rounded px-2 py-0.5 ${SEVERITY_STYLES[verdict.severity]}`}>{verdict.severity}</span>
          <span className="text-xs text-gray-500">
            confidence {verdict.final_confidence.toFixed(2)} · resolved by {verdict.resolved_by}
            {verdict.resolved_by === "agent" && ` (agreement: ${verdict.agreement ? "yes" : "no"})`}
          </span>
        </div>
      )}

      {isDeterministic && verdict?.verifier_reasoning && (
        <div>
          <p className="text-xs text-gray-500 mb-1">Deterministic computation</p>
          <p className="text-sm bg-gray-50 border rounded p-2">{verdict.verifier_reasoning}</p>
        </div>
      )}

      {!isDeterministic && verdict && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-gray-500 mb-1">Verifier — {verdict.verifier_verdict}</p>
            <p className="text-sm bg-gray-50 border rounded p-2">{verdict.verifier_reasoning}</p>
            <p className="text-xs text-gray-400 mt-1">confidence {verdict.verifier_confidence?.toFixed(2) ?? "—"}</p>
          </div>
          <div>
            <p className="text-xs text-gray-500 mb-1">Challenger — {verdict.challenger_verdict}</p>
            <p className="text-sm bg-gray-50 border rounded p-2">{verdict.challenger_reasoning}</p>
            <p className="text-xs text-gray-400 mt-1">confidence {verdict.challenger_confidence?.toFixed(2) ?? "—"}</p>
          </div>
        </div>
      )}

      {claim.evidence.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-1">Evidence ({claim.evidence.length})</p>
          <ul className="space-y-1">
            {claim.evidence.map((e) => (
              <li key={e.id} className="text-sm bg-gray-50 border rounded p-2">
                <span className="text-xs text-gray-500">[{e.source_type}] {e.source_ref}</span>
                <p>{e.content_snippet}</p>
              </li>
            ))}
          </ul>
        </div>
      )}

      {claim.traces.length > 0 && (
        <div>
          <p className="text-xs text-gray-500 mb-1">Agent traces & tool calls</p>
          <ul className="space-y-2">
            {claim.traces.map((t) => (
              <li key={t.id} className="text-sm bg-gray-50 border rounded p-2">
                <p className="text-xs font-medium">{t.agent_name}</p>
                {t.tool_calls && t.tool_calls.length > 0 ? (
                  <ul className="text-xs text-gray-500 mt-1 space-y-0.5">
                    {t.tool_calls.map((call, i) => (
                      <li key={i}>
                        {call.tool_name}({JSON.stringify(call.tool_args)}) → {call.result}
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="text-xs text-gray-400 mt-1">no tool calls</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
