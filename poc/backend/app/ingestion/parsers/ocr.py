import pytesseract
from pdf2image import convert_from_path



def ocr_page(pdf_path: str, page_number: int) -> tuple[str, float]:
    """OCR a single PDF page (1-indexed). Returns (text, average_confidence in 0-1).

    image_to_data emits one row per detected word with its pixel position (left/top) and which
    block/paragraph/line tesseract's layout analysis assigned it to. Words are grouped into lines
    by that (block, paragraph, line) key, ordered left-to-right within a line and top-to-bottom
    across lines — both by actual pixel position rather than row order — and joined with
    newlines, so the page's line structure survives instead of every word on the page collapsing
    into one run-on string."""
    image = convert_from_path(pdf_path, first_page=page_number, last_page=page_number, dpi=300)[0]
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    # (left, top, word) per line key, so each line can be sorted left-to-right and lines
    # themselves sorted top-to-bottom by actual pixel position.
    lines: dict[tuple[int, int, int], list[tuple[int, int, str]]] = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        line_key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(line_key, []).append((data["left"][i], data["top"][i], word))

    ordered_lines = sorted(lines.values(), key=lambda words: min(top for _, top, _ in words))
    text = "\n".join(
        " ".join(word for _, _, word in sorted(line_words, key=lambda w: w[0])) for line_words in ordered_lines
    )

    confidences = [c for c in data["conf"] if c != -1]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    return text, avg_confidence / 100

