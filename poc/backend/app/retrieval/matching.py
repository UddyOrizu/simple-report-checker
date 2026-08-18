_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with", "as", "by", "at",
    "is", "are", "was", "were", "be", "been", "being", "this", "that", "these", "those", "it", "its",
    "we", "our", "their", "has", "have", "had", "will", "would", "can", "could", "than", "then",
    "into", "over", "under", "across", "each", "which", "who", "from", "not", "no", "so", "such",
    "more", "most", "much", "many", "any", "all", "some", "if", "while", "also", "just", "up", "out",
}


def words(text: str) -> set[str]:
    normalized = "".join(c.lower() if c.isalnum() else " " for c in text)
    return {w for w in normalized.split() if w not in _STOPWORDS}


def table_text(table_data: list[list[str]]) -> str:
    return "\n".join(" | ".join(str(cell) for cell in row) for row in table_data)


def matches_requires(text: str, requires: list[str]) -> bool:
    """True if `text` plausibly satisfies one of the claim's `requires` phrases — at least half
    of a phrase's content words appear in the text. A simple word-overlap heuristic, not
    embedding search — 5.1/5.1.1 are direct lookups, not semantic retrieval."""
    text_words = words(text)
    for phrase in requires:
        phrase_words = words(phrase)
        if phrase_words and len(phrase_words & text_words) / len(phrase_words) >= 0.5:
            return True
    return False


def citation(ref: str, page_number: int | None, section_title: str | None) -> str:
    parts = [ref]
    if page_number is not None:
        parts.append(f"page {page_number}")
    if section_title:
        parts.append(f"section '{section_title}'")
    return " ".join(parts)
