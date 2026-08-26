import statistics

import pymupdf as fitz
import pdfplumber

from app.ingestion.parsers.ocr import ocr_page

HEADING_SIZE_RATIO = 1.15  # a line whose font size is >= body_size * this ratio is treated as a heading
PARAGRAPH_LINE_GAP_RATIO = 1.9  # gap/font_size below this merges a line into the previous element (line-wrap continuation, not a new paragraph)


def get_page_char_count(pdf_path: str, page_number: int, doc: "fitz.Document | None" = None) -> int:
    """`doc` (optional): an already-open fitz.Document, so a caller processing every page of a
    document doesn't reopen (and re-parse the xref table of) the whole PDF file once per page.
    Opens its own handle when omitted, for standalone callers."""
    if doc is not None:
        return len(doc[page_number - 1].get_text().strip())
    with fitz.open(pdf_path) as opened:
        return len(opened[page_number - 1].get_text().strip())


def is_native_page(pdf_path: str, page_number: int, config: dict, doc: "fitz.Document | None" = None) -> bool:
    return get_page_char_count(pdf_path, page_number, doc=doc) > config["native_text_char_threshold"]


def _bbox_contains(bbox, x, y) -> bool:
    x0, top, x1, bottom = bbox
    return x0 <= x <= x1 and top <= y <= bottom


def _group_words_into_lines(words: list[dict]) -> list[dict]:
    """Group words with close 'top' coordinates into lines, keeping average font size."""
    lines: list[dict] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        if lines and abs(lines[-1]["top"] - w["top"]) < 3:
            lines[-1]["words"].append(w)
        else:
            lines.append({"top": w["top"], "words": [w]})
    for line in lines:
        line["text"] = " ".join(w["text"] for w in line["words"])
        line["size"] = statistics.mean(w["size"] for w in line["words"])
    return lines


def _merge_lines_into_elements(lines: list[dict], heading_threshold: float) -> list[dict]:
    """Merge consecutive lines that are the same type, the same font size, and close enough
    together vertically into one element — a wrapped line within a single paragraph, not a real
    paragraph/heading break. A type or size change, or a bigger gap, starts a new element."""
    blocks: list[dict] = []
    current = None

    for line in lines:
        el_type = "heading" if line["size"] >= heading_threshold else "paragraph"
        if current is not None:
            gap = line["top"] - current["last_top"]
            same_block = (
                el_type == current["type"]
                and abs(line["size"] - current["size"]) < 0.5
                and gap < current["size"] * PARAGRAPH_LINE_GAP_RATIO
            )
            if same_block:
                current["texts"].append(line["text"])
                current["last_top"] = line["top"]
                continue
            blocks.append(current)
        current = {"type": el_type, "texts": [line["text"]], "top": line["top"], "last_top": line["top"], "size": line["size"]}

    if current is not None:
        blocks.append(current)

    return [{"type": b["type"], "text": " ".join(b["texts"]), "top": b["top"]} for b in blocks]


def _extract_native_page(page, page_number: int) -> list[dict]:
    tables = page.find_tables()
    words = page.extract_words(extra_attrs=["size"])

    # words that fall inside a table's bbox are already represented by the table element
    non_table_words = [
        w for w in words if not any(_bbox_contains(t.bbox, w["x0"], w["top"]) for t in tables)
    ]
    lines = _group_words_into_lines(non_table_words)

    body_size = statistics.median(w["size"] for w in words) if words else 11.0
    heading_threshold = body_size * HEADING_SIZE_RATIO

    elements = [{**e, "page_number": page_number} for e in _merge_lines_into_elements(lines, heading_threshold)]

    for t in tables:
        elements.append(
            {"type": "table", "data": t.extract(), "top": t.bbox[1], "page_number": page_number}
        )

    elements.sort(key=lambda e: e["top"])
    for e in elements:
        e.pop("top")
    return elements


def parse_native_page(pdf_path: str, page_number: int, pdf: "pdfplumber.PDF | None" = None) -> list[dict]:
    """Extract heading/paragraph/table elements from a single native-text PDF page, in document
    order. `pdf` (optional): an already-open pdfplumber.PDF, so a per-page caller doesn't reopen
    (and re-parse) the whole file on every page."""
    if pdf is not None:
        return _extract_native_page(pdf.pages[page_number - 1], page_number)
    with pdfplumber.open(pdf_path) as opened:
        return _extract_native_page(opened.pages[page_number - 1], page_number)


def parse_ocr_page(pdf_path: str, page_number: int) -> list[dict]:
    """OCR a single non-native page, returning it as one paragraph element carrying its confidence."""
    text, confidence = ocr_page(pdf_path, page_number)
    return [
        {
            "type": "paragraph",
            "text": text,
            "page_number": page_number,
            "source": "ocr",
            "ocr_confidence": confidence,
        }
    ]


def parse_pdf(path: str, config: dict) -> list[dict]:
    """Parse every page of a PDF, in document order.

    Native pages (char count above the configured threshold) go through the pdfplumber-based
    layout parser; every other page is routed through OCR independently, so a document can mix
    native and scanned pages freely.

    Opens the file once (via fitz for the native/OCR decision, via pdfplumber for native-page
    extraction) and reuses both handles across every page, rather than reopening — and
    re-parsing the file's xref table — once per page.
    """
    elements = []
    with fitz.open(path) as doc, pdfplumber.open(path) as pdf:
        page_count = len(doc)
        for page_number in range(1, page_count + 1):
            if is_native_page(path, page_number, config, doc=doc):
                elements.extend(parse_native_page(path, page_number, pdf=pdf))
            else:
                elements.extend(parse_ocr_page(path, page_number))
    return elements
