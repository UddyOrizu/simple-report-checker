import os

import yaml

from app.ingestion.parsers.pdf_parser import is_native_page, parse_pdf


def _config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")
    return yaml.safe_load(open(config_path))


def test_native_report_is_native(fixtures_dir):
    path = os.path.join(fixtures_dir, "native_report.pdf")
    assert is_native_page(path, 1, _config()) is True


def test_parses_native_report_elements(fixtures_dir):
    path = os.path.join(fixtures_dir, "native_report.pdf")
    elements = parse_pdf(path, _config())

    types = [e["type"] for e in elements]
    assert "heading" in types
    assert "table" in types

    headings = [e["text"] for e in elements if e["type"] == "heading"]
    assert any("Financial Highlights" in h for h in headings)

    table = next(e for e in elements if e["type"] == "table")
    assert table["data"] == [
        ["Metric", "Value"],
        ["Revenue (current period)", "$112M"],
        ["Revenue (prior period)", "$100M"],
    ]

    paragraphs = [e["text"] for e in elements if e["type"] == "paragraph"]
    assert any("Revenue grew 12% YoY" in p for p in paragraphs)

    # document order: title heading appears before the Financial Highlights heading
    heading_indices = [i for i, e in enumerate(elements) if e["type"] == "heading"]
    assert elements[heading_indices[0]]["text"] == "Q3 Business Report"
