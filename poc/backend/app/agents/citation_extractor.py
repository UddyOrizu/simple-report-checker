import bisect
import re
from typing import Any

SECTION_HEADERS = [
    "references",
    "bibliography",
    "works cited",
    "citations",
    "notes",
    "endnotes",
    "sources",
]

_HEADER_RE = re.compile(
    r"^\s*(?:#+\s*)?(?:\d+[\.)]\s*)?(?:" + "|".join(re.escape(h) for h in SECTION_HEADERS) + r")\s*:?\s*$",
    re.IGNORECASE,
)

_ENTRY_START_RE = re.compile(
    r"^\s*(?:\[(\d+)\]|\((\d+)\)|(\d+)[\.\)])\s+",
    re.MULTILINE,
)

_SENTENCE_END_RE = re.compile(r"[.!?]+(?:\s+|\n+|$)")
_CITATION_MARKER_RE = re.compile(r"\[(\d+(?:\s*[-–,]\s*\d+)*)\]|\((\d+(?:\s*[-–,]\s*\d+)*)\)")
_URL_RE = re.compile(r"(https?://[^\s\]\)>\",\']+|www\.[^\s\]\)>\",\']+)")


def _expand_numbers(raw: str) -> list[int]:
    numbers: list[int] = []
    for part in re.split(r"\s*,\s*", raw.strip()):
        part = part.strip()
        if not part:
            continue
        rng = re.match(r"^(\d+)\s*[-–]\s*(\d+)$", part)
        if rng:
            lo, hi = int(rng.group(1)), int(rng.group(2))
            numbers.extend(range(lo, hi + 1))
        elif part.isdigit():
            numbers.append(int(part))
    return numbers


def find_reference_section(text: str) -> tuple[str | None, int | None]:
    """Return the last matching reference section and its char offset in the document."""
    lines = text.splitlines()
    start_line = None
    for i, line in enumerate(lines):
        if _HEADER_RE.match(line):
            start_line = i

    if start_line is None:
        return None, None

    section_text = "\n".join(lines[start_line + 1 :])
    offset = len("\n".join(lines[: start_line + 1]))
    return section_text, offset


def split_reference_entries(section_text: str) -> dict[int, str]:
    matches = list(_ENTRY_START_RE.finditer(section_text))
    entries: dict[int, str] = {}

    if not matches:
        lines = [ln.strip() for ln in section_text.splitlines() if ln.strip()]
        for idx, line in enumerate(lines, start=1):
            entries[idx] = line
        return entries

    for i, match in enumerate(matches):
        value = next((g for g in match.groups() if g is not None), None)
        if value is None:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(section_text)
        entry_text = re.sub(r"\s+", " ", section_text[start:end]).strip()
        entries[int(value)] = entry_text

    return entries


def split_sentences_with_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    start = 0
    for match in _SENTENCE_END_RE.finditer(text):
        end = match.end()
        spans.append((start, end))
        start = end
    if start < len(text):
        spans.append((start, len(text)))
    if not spans:
        spans = [(0, len(text))]
    return spans


def sentence_at(text: str, spans: list[tuple[int, int]], position: int) -> str:
    starts = [start for start, _ in spans]
    idx = bisect.bisect_right(starts, position) - 1
    idx = max(0, min(idx, len(spans) - 1))
    start, end = spans[idx]
    return re.sub(r"\s+", " ", text[start:end]).strip()


def find_intext_citations(body_text: str) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    for match in _CITATION_MARKER_RE.finditer(body_text):
        raw = match.group(1) or match.group(2) or ""
        for number in _expand_numbers(raw):
            occurrences.append({
                "number": number,
                "position": match.start(),
                "match_text": match.group(0),
            })
    return occurrences


def extract_urls(text: str | None) -> list[str]:
    if not text:
        return []
    return _URL_RE.findall(text)


def extract_citations_from_text(full_text: str) -> dict[str, Any]:
    """Extract numbered citations and link them to reference entries in the document."""
    section_text, section_offset = find_reference_section(full_text)
    if section_text is None:
        body_text = full_text
        entries: dict[int, str] = {}
    else:
        body_text = full_text[:section_offset]
        entries = split_reference_entries(section_text)

    intext = find_intext_citations(body_text)
    sentence_spans = split_sentences_with_spans(body_text)

    instances: list[dict[str, Any]] = []
    for occurrence in intext:
        entry_text = entries.get(occurrence["number"])
        instances.append({
            "number": occurrence["number"],
            "citing_text": sentence_at(body_text, sentence_spans, occurrence["position"]),
            "match_text": occurrence["match_text"],
            "position": occurrence["position"],
            "reference_text": entry_text,
            "urls": extract_urls(entry_text),
            "found_in_reference_section": occurrence["number"] in entries,
        })

    usage_by_number: dict[int, list[int]] = {}
    for occurrence in intext:
        usage_by_number.setdefault(occurrence["number"], []).append(occurrence["position"])

    all_numbers = sorted(set(entries) | set(usage_by_number))
    citations = []
    for number in all_numbers:
        entry_text = entries.get(number)
        citations.append({
            "number": number,
            "reference_text": entry_text,
            "urls": extract_urls(entry_text),
            "cited_in_text_count": len(usage_by_number.get(number, [])),
            "found_in_reference_section": number in entries,
        })

    return {
        "reference_section_found": section_text is not None,
        "total_references": len(entries),
        "total_intext_citations": len(intext),
        "instances": instances,
        "citations": citations,
    }


def citation_context_for_text(citation_summary: dict[str, Any], text: str | None = None) -> str:
    """Render a compact citation summary suitable for LLM routing context."""
    if not citation_summary.get("instances") and not citation_summary.get("citations"):
        return "No numbered citations detected in this document."

    if text:
        cited_numbers = {item["number"] for item in find_intext_citations(text)}
    else:
        cited_numbers = set()

    lines = ["Document citation context:"]
    for item in citation_summary.get("citations", []):
        number = item["number"]
        if text and number not in cited_numbers:
            continue
        ref = item.get("reference_text") or "(no matching reference entry found)"
        urls = ", ".join(item.get("urls", [])) if item.get("urls") else "(no URL found)"
        lines.append(f"- [{number}]: {ref[:280]} | URLs: {urls}")

    if not lines[1:]:
        lines.append("- The text contains in-text citations but no corresponding reference entries were parsed.")

    return "\n".join(lines)
