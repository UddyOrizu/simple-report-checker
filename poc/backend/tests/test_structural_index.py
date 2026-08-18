import os

import pymupdf as fitz
import yaml

from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.structural_index import build_structural_index


def _config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")
    return yaml.safe_load(open(config_path))


def test_short_document_skips_index_building(fixtures_dir):
    path = os.path.join(fixtures_dir, "short_memo.docx")
    elements = parse_docx(path)

    sections = build_structural_index(page_count=1, elements=elements, config=_config())

    assert sections is None


def test_headed_document_produces_real_sections(fixtures_dir):
    path = os.path.join(fixtures_dir, "sample_report.docx")
    elements = parse_docx(path)

    sections = build_structural_index(page_count=6, elements=elements, config=_config())

    assert sections is not None
    assert all(s["is_pseudo_section"] is False for s in sections)
    titles = [s["title"] for s in sections]
    assert "Financial Highlights" in titles

    financial = next(s for s in sections if s["title"] == "Financial Highlights")
    body = elements[financial["start_index"] : financial["end_index"]]
    assert any("Revenue grew 12%" in e.get("text", "") for e in body)
    assert any(e["type"] == "table" for e in body)


def test_unstructured_document_falls_back_to_pseudo_sections(fixtures_dir):
    path = os.path.join(fixtures_dir, "unstructured_essay.pdf")
    with fitz.open(path) as doc:
        page_count = len(doc)

    from app.ingestion.parsers.pdf_parser import parse_native_page

    elements = []
    for page_number in range(1, page_count + 1):
        elements.extend(parse_native_page(path, page_number))

    assert not any(e["type"] == "heading" for e in elements)  # confirms this is the zero-heading fixture

    sections = build_structural_index(page_count=page_count, elements=elements, config=_config())

    assert sections is not None
    assert len(sections) > 1
    assert all(s["is_pseudo_section"] is True for s in sections)
