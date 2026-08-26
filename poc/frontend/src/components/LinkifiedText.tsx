const URL_PATTERN = /https?:\/\/[^\s)]+/g;

/** Renders text with any http(s) URLs as real, clickable links — opened in a new tab with
 * rel="noopener noreferrer" so an external page can't reach back and manipulate this tab via
 * window.opener. Everything else renders as plain text. Used anywhere free text (evidence
 * snippets, source refs, agent reasoning) might contain a citation URL. */
export function LinkifiedText({ text }: { text: string | null | undefined }) {
  if (!text) return null;

  const parts = text.split(URL_PATTERN);
  const urls = text.match(URL_PATTERN) ?? [];

  return (
    <>
      {parts.map((part, i) => (
        <span key={i}>
          {part}
          {urls[i] && (
            <a
              href={urls[i]}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-600 underline break-all"
            >
              {urls[i]}
            </a>
          )}
        </span>
      ))}
    </>
  );
}
