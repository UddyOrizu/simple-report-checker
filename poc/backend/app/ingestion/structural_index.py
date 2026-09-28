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


def sections_from_headings(elements: list[dict]) -> list[dict]:
    """Group elements into one section per heading, spanning up to the next heading (of any
    level — a section's own body stops where its first subsection starts). Each section also
    records its heading `level` and `parent_order_index`: the nearest preceding section with a
    shallower level, which turns the flat list into a tree for vectorless retrieval to navigate.
    Headings without a level (the pdfplumber parser's font-size headings) are all level 1."""
    heading_indices = [i for i, e in enumerate(elements) if e["type"] == "heading"]

    sections = []
    open_sections: list[tuple[int, int]] = []  # (level, order_index) stack of current ancestors
    for order_index, h_idx in enumerate(heading_indices):
        start = h_idx + 1
        end = heading_indices[order_index + 1] if order_index + 1 < len(heading_indices) else len(elements)
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
        return sections_from_headings(elements)

    return pseudo_sections_from_topic_shift(elements, sensitivity=config["topic_shift_sensitivity"])
