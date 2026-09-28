_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with", "as", "by", "at",
    "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "it", "its",
    "we", "our", "their", "has", "have", "had", "will", "would", "can", "could", "than", "then",
    "into", "over", "under", "across", "each", "which", "who", "from", "not", "no", "so", "such",
    "more", "most", "much", "many", "any", "all", "some", "if", "while", "also", "just", "up", "out",
}


def _content_words(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {w for w in normalized.split() if w not in _STOPWORDS}


def _similarity(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _page_range(body: list[dict]) -> tuple[int | None, int | None]:
    pages = [e["page_number"] for e in body if e.get("page_number") is not None]
    return (min(pages), max(pages)) if pages else (None, None)


PREAMBLE_TITLE = "Preamble"


def sections_from_headings(elements: list[dict]) -> list[dict]:
    """Group elements into one section per heading, spanning up to the next heading (of any
    level — a section's own body stops where its first subsection starts). Each section also
    records its heading `level` and `parent_order_index`: the nearest preceding section with a
    shallower level, which turns the flat list into a tree for vectorless retrieval to navigate.
    Headings without a level (the pdfplumber parser's font-size headings) are all level 1."""
    heading_indices = [i for i, e in enumerate(elements) if e["type"] == "heading"]

    sections = []
    # Text before the first heading (an abstract, cover-page text, an untitled opening paragraph)
    # would otherwise belong to no section at all — invisible to anything navigating the index.
    if heading_indices and any(_element_chars(e) for e in elements[: heading_indices[0]]):
        page_start, page_end = _page_range(elements[: heading_indices[0]])
        sections.append(
            {
                "title": PREAMBLE_TITLE,
                "is_pseudo_section": True,
                "order_index": 0,
                "level": 1,
                "parent_order_index": None,
                "page_start": page_start,
                "page_end": page_end,
                "start_index": 0,
                "end_index": heading_indices[0],
            }
        )

    open_sections: list[tuple[int, int]] = []  # (level, order_index) stack of current ancestors
    for position, h_idx in enumerate(heading_indices):
        order_index = len(sections)
        start = h_idx + 1
        end = heading_indices[position + 1] if position + 1 < len(heading_indices) else len(elements)
        page_start, page_end = _page_range(elements[h_idx:end])
        level = max(1, elements[h_idx].get("level") or 1)
        while open_sections and open_sections[-1][0] >= level:
            open_sections.pop()
        sections.append(
            {
                "title": elements[h_idx]["text"],
                # Sections under an LLM-generated heading are flagged pseudo: the title describes
                # the content but isn't the author's.
                "is_pseudo_section": bool(elements[h_idx].get("synthetic")),
                "order_index": order_index,
                "level": level,
                "parent_order_index": open_sections[-1][1] if open_sections else None,
                "page_start": page_start,
                "page_end": page_end,
                "start_index": start,
                "end_index": end,
            }
        )
        open_sections.append((level, order_index))
    return sections


def _element_chars(element: dict) -> int:
    if element["type"] == "table":
        return sum(len(str(cell or "")) + 3 for row in element.get("data") or [] for cell in row)
    return len(element.get("text", "").strip())


def _partition(start: int, end: int, elements: list[dict], max_chars: int) -> list[tuple[int, int]]:
    """Greedily packs elements [start, end) into consecutive runs of at most `max_chars`, cutting
    only between elements. A single element over the limit stays whole in a run of its own."""
    parts: list[tuple[int, int]] = []
    part_start, size = start, 0
    for i in range(start, end):
        chars = _element_chars(elements[i])
        if i > part_start and size + chars > max_chars:
            parts.append((part_start, i))
            part_start, size = i, 0
        size += chars
    parts.append((part_start, end))
    return parts


def split_long_sections(sections: list[dict], elements: list[dict], max_chars: int | None) -> list[dict]:
    """Splits any section whose own body exceeds `max_chars` into consecutive parts titled
    "<title> (part k of n)", so no section is too long to be read in full. Every part keeps the
    original's level and parent (continuation parts are siblings, flagged pseudo), subsections
    stay under part 1, and order_index / parent_order_index are renumbered to match."""
    if not max_chars:
        return sections

    result: list[dict] = []
    new_order_of: dict[int, int] = {}
    for section in sections:
        parts = _partition(section["start_index"], section["end_index"], elements, max_chars)
        parent = section["parent_order_index"]
        for n, (start, end) in enumerate(parts):
            if n == 0:
                new_order_of[section["order_index"]] = len(result)
            title = section["title"]
            if len(parts) > 1:
                title = f"{title or 'Untitled section'} (part {n + 1} of {len(parts)})"
            page_start, page_end = _page_range(elements[start:end])
            result.append(
                {
                    **section,
                    "title": title,
                    "is_pseudo_section": section["is_pseudo_section"] or n > 0,
                    "order_index": len(result),
                    # Parents always come earlier in the document, so they're already renumbered.
                    "parent_order_index": new_order_of[parent] if parent is not None else None,
                    "page_start": section["page_start"] if n == 0 else page_start,
                    "page_end": page_end if len(parts) > 1 else section["page_end"],
                    "start_index": start,
                    "end_index": end,
                }
            )
    return result


def pseudo_sections_from_topic_shift(elements: list[dict], sensitivity: float) -> list[dict]:
    """No headings at all: cut a new pseudo-section wherever consecutive paragraphs' content-word
    overlap drops below the sensitivity-derived threshold — lower sensitivity means a higher bar
    for staying in the same section, i.e. more cuts."""
    paragraph_indices = [i for i, e in enumerate(elements) if e["type"] == "paragraph"]
    if not paragraph_indices:
        return []

    cut_threshold = 1 - sensitivity
    section_starts = [paragraph_indices[0]]
    prev_words = _content_words(elements[paragraph_indices[0]]["text"])

    for idx in paragraph_indices[1:]:
        words = _content_words(elements[idx]["text"])
        if _similarity(prev_words, words) < cut_threshold:
            section_starts.append(idx)
        prev_words = words

    sections = []
    for order_index, start in enumerate(section_starts):
        end = section_starts[order_index + 1] if order_index + 1 < len(section_starts) else len(elements)
        page_start, page_end = _page_range(elements[start:end])
        sections.append(
            {
                "title": None,
                "is_pseudo_section": True,
                "order_index": order_index,
                "level": 1,
                "parent_order_index": None,
                "page_start": page_start,
                "page_end": page_end,
                "start_index": start,
                "end_index": end,
            }
        )
    return sections


def build_structural_index(page_count: int, elements: list[dict], config: dict) -> list[dict] | None:
    """Returns None (has_structural_index=false) for documents too short to bother sectioning."""
    if page_count <= config["short_document_page_threshold"]:
        return None

    headings = [e for e in elements if e["type"] == "heading"]
    if headings:
        sections = sections_from_headings(elements)
    else:
        sections = pseudo_sections_from_topic_shift(elements, sensitivity=config["topic_shift_sensitivity"])
    return split_long_sections(sections, elements, config.get("max_section_chars"))
