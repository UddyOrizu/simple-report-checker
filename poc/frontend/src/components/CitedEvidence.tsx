import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getSection } from "../api/client";
import type { EvidenceItem, EvidenceStance } from "../api/types";

const STANCE_STYLES: Record<EvidenceStance, string> = {
  supports: "bg-green-50 border-green-200 text-green-700",
  contradicts: "bg-red-50 border-red-200 text-red-700",
  context: "bg-gray-100 border-gray-200 text-gray-600",
};

/** Locates `quote` in `content` word by word, tolerating the markup and whitespace between words
 * that a verbatim quote of Markdown source drops (table pipes, **emphasis**, line breaks). */
function findQuote(content: string, quote: string): [number, number] | null {
  const words = quote.split(/[\W_]+/).filter(Boolean);
  if (words.length === 0) return null;
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const match = new RegExp(escaped.join("[\\W_]+"), "i").exec(content);
  return match ? [match.index, match.index + match[0].length] : null;
}

function SectionWithHighlight({ documentId, sectionId, quote }: { documentId: string; sectionId: string; quote: string | null }) {
  const { data: section, isLoading, error } = useQuery({
    queryKey: ["section", sectionId],
    queryFn: () => getSection(documentId, sectionId),
  });

  if (isLoading) return <p className="text-xs text-gray-500">Loading section…</p>;
  if (error || !section) return <p className="text-xs text-red-600">Failed to load section.</p>;

  const content = section.content ?? "";
  const span = quote ? findQuote(content, quote) : null;

  return (
    <div className="mt-2 border rounded bg-white p-2 max-h-80 overflow-auto">
      <p className="text-xs font-medium mb-1">
        {section.title ?? "Untitled section"}
        {section.page_start != null && (
          <span className="text-gray-400 font-normal">
            {" "}
            · pages {section.page_start}
            {section.page_end != null && section.page_end !== section.page_start && `–${section.page_end}`}
          </span>
        )}
      </p>
      <p className="text-xs whitespace-pre-wrap text-gray-700">
        {span ? (
          <>
            {content.slice(0, span[0])}
            <mark className="bg-yellow-200">{content.slice(span[0], span[1])}</mark>
            {content.slice(span[1])}
          </>
        ) : (
          content
        )}
      </p>
    </div>
  );
}

/** Evidence with a structured citation: stance, the verbatim quote, and a link that opens the
 * cited section with the quote highlighted in place. */
export function CitedEvidence({ evidence, documentId }: { evidence: EvidenceItem; documentId: string }) {
  const [open, setOpen] = useState(false);
  const location = [evidence.section_path, evidence.page_number != null ? `page ${evidence.page_number}` : null]
    .filter(Boolean)
    .join(" · ");
  // content_snippet is `STANCE: "quote" — explanation`; show just the explanation under the quote.
  const explanation = evidence.content_snippet?.split(" — ").slice(1).join(" — ");

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        {evidence.stance && (
          <span className={`border rounded px-1.5 py-0.5 ${STANCE_STYLES[evidence.stance]}`}>{evidence.stance}</span>
        )}
        {evidence.section_id ? (
          <button className="text-blue-700 underline text-left" onClick={() => setOpen(!open)}>
            § {location || "cited section"}
          </button>
        ) : (
          <span className="text-gray-500">{location}</span>
        )}
      </div>
      <blockquote className="border-l-2 border-gray-300 pl-2 my-1 italic">“{evidence.quote}”</blockquote>
      {explanation && <p className="text-xs text-gray-600">{explanation}</p>}
      {open && evidence.section_id && (
        <SectionWithHighlight documentId={documentId} sectionId={evidence.section_id} quote={evidence.quote} />
      )}
    </div>
  );
}
