import pytesseract
from pdf2image import convert_from_path


def ocr_page(pdf_path: str, page_number: int) -> tuple[str, float]:
    """OCR a single PDF page (1-indexed). Returns (text, average_confidence in 0-1)."""
    image = convert_from_path(pdf_path, first_page=page_number, last_page=page_number)[0]
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    text = " ".join(w for w in data["text"] if w.strip())
    confidences = [c for c in data["conf"] if c != -1]
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0
    return text, avg_confidence / 100

