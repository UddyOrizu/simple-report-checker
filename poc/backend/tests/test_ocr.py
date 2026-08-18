import os

import yaml

from app.ingestion.parsers.ocr import ocr_page
from app.ingestion.parsers.pdf_parser import parse_pdf


def _config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")
    return yaml.safe_load(open(config_path))


def test_ocr_page_extracts_text_with_confidence(fixtures_dir):
    path = os.path.join(fixtures_dir, "scanned_report.pdf")
    text, confidence = ocr_page(path, 1)

    assert "Annual Compliance Memo" in text
    assert "GDPR" in text
    assert 0 <= confidence <= 1
    assert confidence > 0.5


def test_ocr_routes_every_scanned_page_independently(fixtures_dir):
    path = os.path.join(fixtures_dir, "scanned_report.pdf")
    elements = parse_pdf(path, _config())

    assert all(e["source"] == "ocr" for e in elements)
    page_texts = {e["page_number"]: e["text"] for e in elements}
    assert "GDPR" in page_texts[1]
    assert "Vendor Review" in page_texts[2]


def test_mixed_report_routes_native_and_scanned_pages_independently(fixtures_dir):
    path = os.path.join(fixtures_dir, "mixed_report.pdf")
    elements = parse_pdf(path, _config())

    page1_elements = [e for e in elements if e["page_number"] == 1]
    page2_elements = [e for e in elements if e["page_number"] == 2]
    page3_elements = [e for e in elements if e["page_number"] == 3]

    # page 1: native text, parsed via pdfplumber — no OCR confidence attached
    assert any(e["type"] == "heading" and "Cover Page" in e["text"] for e in page1_elements)
    assert all("ocr_confidence" not in e for e in page1_elements)

    # page 2: scanned image-only page, routed through OCR
    assert all(e.get("source") == "ocr" for e in page2_elements)
    assert any("Scanned Appendix" in e["text"] for e in page2_elements)
    assert all("ocr_confidence" in e for e in page2_elements)

    # page 3: native text resumes
    assert any("Closing Notes" in e["text"] for e in page3_elements)
    assert all("ocr_confidence" not in e for e in page3_elements)

    # document order preserved across the native/scanned/native boundary
    assert [e["page_number"] for e in elements] == sorted(e["page_number"] for e in elements)
