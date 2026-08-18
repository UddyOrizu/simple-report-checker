import statistics

import fitz
import pdfplumber

HEADING_SIZE_RATIO = 1.15  # a line whose font size is >= body_size * this ratio is treated as a heading


def get_page_char_count(pdf_path: str, page_number: int) -> int:
    with fitz.open(pdf_path) as doc:
        page = doc[page_number - 1]
        return len(page.get_text().strip())


def is_native_page(pdf_path: str, page_number: int, config: dict) -> bool:
    return get_page_char_count(pdf_path, page_number) > config["native_text_char_threshold"]


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


def parse_native_page(pdf_path: str, page_number: int) -> list[dict]:
    """Extract heading/paragraph/table elements from a single native-text PDF page, in document order."""
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[page_number - 1]
        tables = page.find_tables()
        words = page.extract_words(extra_attrs=["size"])

        # words that fall inside a table's bbox are already represented by the table element
        non_table_words = [
            w for w in words if not any(_bbox_contains(t.bbox, w["x0"], w["top"]) for t in tables)
        ]
        lines = _group_words_into_lines(non_table_words)

        body_size = statistics.median(w["size"] for w in words) if words else 11.0
        heading_threshold = body_size * HEADING_SIZE_RATIO

        elements = []
        for line in lines:
            el_type = "heading" if line["size"] >= heading_threshold else "paragraph"
            elements.append({"type": el_type, "text": line["text"], "top": line["top"], "page_number": page_number})

        for t in tables:
            elements.append(
                {"type": "table", "data": t.extract(), "top": t.bbox[1], "page_number": page_number}
            )

        elements.sort(key=lambda e: e["top"])
        for e in elements:
            e.pop("top")
        return elements


def parse_pdf(path: str, config: dict) -> list[dict]:
    """Parse every page of a PDF, returning elements for native pages only.

    Pages that aren't native (char count at/below the configured threshold) are returned as
    stub {"type": "scanned_page", "page_number": N} markers — the OCR fallback (Phase 2.3)
    fills those in.
    """
    with fitz.open(path) as doc:
        page_count = len(doc)

    elements = []
    for page_number in range(1, page_count + 1):
        if is_native_page(path, page_number, config):
            elements.extend(parse_native_page(path, page_number))
        else:
            elements.append({"type": "scanned_page", "page_number": page_number})
    return elements
