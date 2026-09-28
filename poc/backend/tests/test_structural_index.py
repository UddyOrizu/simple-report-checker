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


def test_nested_headings_record_level_and_parent():
    elements = [
        {"type": "heading", "level": 1, "text": "Report", "page_number": 1},
        {"type": "heading", "level": 2, "text": "Financials", "page_number": 1},
        {"type": "paragraph", "text": "Intro.", "page_number": 1},
        {"type": "heading", "level": 3, "text": "Revenue", "page_number": 2},
        {"type": "paragraph", "text": "Revenue grew.", "page_number": 2},
        {"type": "heading", "level": 2, "text": "Outlook", "page_number": 3},
        {"type": "paragraph", "text": "Steady.", "page_number": 3},
    ]

    sections = build_structural_index(page_count=10, elements=elements, config=_config())

    assert [(s["title"], s["level"], s["parent_order_index"]) for s in sections] == [
        ("Report", 1, None),
        ("Financials", 2, 0),
        ("Revenue", 3, 1),
        ("Outlook", 2, 0),
    ]
    # A heading-only section still gets the page its heading sits on.
    assert (sections[0]["page_start"], sections[0]["page_end"]) == (1, 1)


def test_text_before_the_first_heading_gets_a_preamble_section():
    elements = [
        {"type": "paragraph", "text": "Prepared for the board, March 2026.", "page_number": 1},
        {"type": "heading", "level": 1, "text": "Results", "page_number": 2},
        {"type": "paragraph", "text": "Revenue grew.", "page_number": 2},
    ]

    sections = build_structural_index(page_count=10, elements=elements, config=_config())

    assert [(s["title"], s["is_pseudo_section"], s["start_index"], s["end_index"]) for s in sections] == [
        ("Preamble", True, 0, 1),
        ("Results", False, 2, 3),
    ]
    assert [s["order_index"] for s in sections] == [0, 1]


def test_long_sections_split_into_parts_keeping_the_tree():
    para = lambda i, page: {"type": "paragraph", "text": f"Paragraph {i} " + "x" * 90, "page_number": page}  # noqa: E731
    elements = [
        {"type": "heading", "level": 1, "text": "Financials", "page_number": 1},
        *[para(i, 1 + i // 2) for i in range(5)],  # ~500 chars of body
        {"type": "heading", "level": 2, "text": "Revenue", "page_number": 4},
        para(9, 4),
    ]

    sections = build_structural_index(10, elements, {**_config(), "max_section_chars": 250})

    assert [(s["title"], s["level"], s["parent_order_index"], s["is_pseudo_section"]) for s in sections] == [
        ("Financials (part 1 of 3)", 1, None, False),
        ("Financials (part 2 of 3)", 1, None, True),
        ("Financials (part 3 of 3)", 1, None, True),
        ("Revenue", 2, 0, False),  # stays under part 1, renumbered
    ]
    assert [s["order_index"] for s in sections] == [0, 1, 2, 3]
    # parts tile the original body exactly
    assert [(s["start_index"], s["end_index"]) for s in sections[:3]] == [(1, 3), (3, 5), (5, 6)]
    assert (sections[1]["page_start"], sections[2]["page_end"]) == (2, 3)
