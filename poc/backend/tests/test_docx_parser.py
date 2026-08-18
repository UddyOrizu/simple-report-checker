import os

from app.ingestion.parsers.docx_parser import parse_docx


def test_parses_sample_report_in_order(fixtures_dir):
    elements = parse_docx(os.path.join(fixtures_dir, "sample_report.docx"))

    types = [e["type"] for e in elements]
    assert types == [
        "heading",  # Q3 Business Report
        "heading",  # Executive Summary
        "paragraph",
        "heading",  # Financial Highlights
        "paragraph",
        "table",
        "heading",  # Outlook
        "paragraph",
    ]

    headings = [e["text"] for e in elements if e["type"] == "heading"]
    assert "Financial Highlights" in headings

    financial_para = elements[4]
    assert financial_para["type"] == "paragraph"
    assert "Revenue grew 12% YoY" in financial_para["text"]
    assert "ahead of our closest competitor" in financial_para["text"]

    table = elements[5]
    assert table["type"] == "table"
    assert table["data"] == [
        ["Metric", "Value"],
        ["Revenue (current period)", "$112M"],
        ["Revenue (prior period)", "$100M"],
    ]


def test_heading_levels(fixtures_dir):
    elements = parse_docx(os.path.join(fixtures_dir, "sample_report.docx"))
    title = next(e for e in elements if e["type"] == "heading" and e["text"] == "Q3 Business Report")
    assert title["level"] == 0
    section = next(e for e in elements if e["type"] == "heading" and e["text"] == "Financial Highlights")
    assert section["level"] == 1
