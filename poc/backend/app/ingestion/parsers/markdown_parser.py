"""Markdown <-> parsed elements.

parse_markdown turns converter output (marker for PDFs, mammoth for DOCX) into the same
heading/paragraph/table element dicts the pdfplumber and python-docx parsers produce, so chunking,
claim extraction and the structural index run unchanged whichever path produced the document.

elements_to_markdown goes the other way, so every document — including ones parsed by the native
fallback parsers — ends up with a Markdown rendering for vectorless retrieval to read and quote.
"""

import re

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# marker's settings.PAGE_SEPARATOR is "\n\n" + "-" * 48 + "\n\n" — a line of exactly 48 dashes, far
# longer than any hand-written horizontal rule.
PAGE_SEPARATOR_RE = re.compile(r"^-{48}$")
TABLE_DIVIDER_RE = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")
LIST_ITEM_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")
FENCE_RE = re.compile(r"^\s*(```|~~~)")

_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_EMPHASIS_RE = re.compile(r"(\*\*|__|\*|_)(\S(?:.*?\S)?)\1")
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")


def strip_inline_markdown(text: str) -> str:
    """Drops inline markup (emphasis, links, images, code spans, escapes) but keeps the words —
    claim extraction and quote matching need the prose, not the formatting."""
    text = _IMAGE_RE.sub("", text)
    text = _LINK_RE.sub(r"\1", text)
    text = _INLINE_CODE_RE.sub(r"\1", text)
    for _ in range(2):  # nested emphasis like ***bold italic***
        text = _EMPHASIS_RE.sub(r"\2", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|])", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text)
    return text.strip()


def _split_table_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [strip_inline_markdown(cell.replace("\\|", "|")) for cell in cells]


def parse_markdown(markdown: str, paginated: bool = False) -> list[dict]:
    """`paginated`: the markdown carries marker's page separators, so every element gets a
    page_number (1-based). Without it (DOCX has no page concept) page_number is omitted, matching
    what parse_docx produces."""
    elements: list[dict] = []
    page_number = 1
    paragraph: list[str] = []
    table: list[list[str]] = []
    in_fence = False

    def with_page(element: dict) -> dict:
        if paginated:
            element["page_number"] = page_number
        return element

    def flush_paragraph() -> None:
        if paragraph:
            text = strip_inline_markdown(" ".join(paragraph))
            if text:
                elements.append(with_page({"type": "paragraph", "text": text}))
            paragraph.clear()

    def flush_table() -> None:
        # markdownify emits an all-blank header row for tables without a <thead> (mammoth never
        # produces one) — drop blank rows rather than carrying empty cells into the table data.
        rows = [row for row in table if any(cell for cell in row)]
        if rows:
            elements.append(with_page({"type": "table", "data": rows}))
        table.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if FENCE_RE.match(line):
            flush_table()
            in_fence = not in_fence
            if not in_fence:
                flush_paragraph()  # a code block becomes one paragraph element of its own
            continue
        if in_fence:
            paragraph.append(stripped)
            continue

        if paginated and PAGE_SEPARATOR_RE.match(stripped):
            flush_paragraph()
            flush_table()
            page_number += 1
            continue

        if stripped.startswith("|"):
            flush_paragraph()
            if not TABLE_DIVIDER_RE.match(stripped):
                table.append(_split_table_row(stripped))
            continue
        flush_table()

        heading = HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            title = strip_inline_markdown(heading.group(2))
            if title:
                elements.append(with_page({"type": "heading", "level": len(heading.group(1)), "text": title}))
            continue

        if not stripped:
            flush_paragraph()
            continue

        # Each list item is its own paragraph: list items rarely end in a full stop, so merging
        # them would hand the sentence splitter one run-on "sentence" spanning the whole list.
        if LIST_ITEM_RE.match(line):
            flush_paragraph()
            paragraph.append(LIST_ITEM_RE.sub("", line, count=1))
            continue

        paragraph.append(stripped.lstrip("> ").strip())

    flush_paragraph()
    flush_table()
    return elements


def _table_to_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)

    def fmt(row: list[str]) -> str:
        cells = [str(c or "").replace("|", "\\|").replace("\n", " ") for c in row] + [""] * (width - len(row))
        return "| " + " | ".join(cells) + " |"

    return "\n".join([fmt(rows[0]), "|" + "---|" * width, *(fmt(row) for row in rows[1:])])


def elements_to_markdown(elements: list[dict]) -> str:
    """Renders parsed elements back to Markdown. Headings without a level (the pdfplumber parser
    only knows "bigger font = heading") render as level 1."""
    blocks = []
    for element in elements:
        if element["type"] == "heading":
            blocks.append("#" * max(1, min(element.get("level") or 1, 6)) + " " + element["text"])
        elif element["type"] == "table":
            blocks.append(_table_to_markdown(element.get("data") or []))
        else:
            blocks.append(element.get("text", ""))
    return "\n\n".join(b for b in blocks if b.strip())
