import os

from app.ingestion.converters.docx_to_markdown import docx_to_markdown
from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.parsers.markdown_parser import elements_to_markdown, parse_markdown

PAGE_BREAK = "\n\n" + "-" * 48 + "\n\n"


def test_headings_keep_their_level():
    elements = parse_markdown("# Annual Report\n\n## Financial Review\n\n### Revenue\n\nRevenue grew 12%.")

    assert [(e["type"], e.get("level"), e["text"]) for e in elements] == [
        ("heading", 1, "Annual Report"),
        ("heading", 2, "Financial Review"),
        ("heading", 3, "Revenue"),
        ("paragraph", None, "Revenue grew 12%."),
    ]


def test_wrapped_lines_join_into_one_paragraph_and_blank_lines_split():
    elements = parse_markdown("Revenue grew 12%\ndriven by APAC.\n\nMargins held flat.")

    assert [e["text"] for e in elements] == ["Revenue grew 12% driven by APAC.", "Margins held flat."]


def test_pipe_table_becomes_table_rows_without_divider_or_blank_header():
    markdown = "|  |  |\n| --- | --- |\n| Metric | Value |\n| Revenue | $112M |"

    (table,) = parse_markdown(markdown)

    assert table == {"type": "table", "data": [["Metric", "Value"], ["Revenue", "$112M"]]}


def test_page_separators_assign_page_numbers_when_paginated():
    markdown = "# Intro\n\nFirst page text." + PAGE_BREAK + "Second page text." + PAGE_BREAK + "| A | B |\n|---|---|\n| 1 | 2 |"

    elements = parse_markdown(markdown, paginated=True)

    assert [e["page_number"] for e in elements] == [1, 1, 2, 3]


def test_unpaginated_markdown_has_no_page_numbers():
    elements = parse_markdown("# Intro\n\nText.")

    assert all("page_number" not in e for e in elements)


def test_list_items_become_separate_paragraphs():
    elements = parse_markdown("- Revenue up 12%\n- Headcount flat\n1. First\n2. Second")

    assert [e["text"] for e in elements] == ["Revenue up 12%", "Headcount flat", "First", "Second"]


def test_inline_markup_is_stripped():
    (element,) = parse_markdown("Revenue was **$112M** per [the filing](https://x.test) and `FY24` ![chart](c.png)")

    assert element["text"] == "Revenue was $112M per the filing and FY24"


def test_elements_round_trip_through_markdown():
    elements = [
        {"type": "heading", "level": 2, "text": "Results"},
        {"type": "paragraph", "text": "Revenue grew 12%."},
        {"type": "table", "data": [["Metric", "Value"], ["Revenue", "$112M"]]},
    ]

    assert parse_markdown(elements_to_markdown(elements)) == elements


def test_docx_markdown_path_matches_python_docx_parser(fixtures_dir):
    path = os.path.join(fixtures_dir, "sample_report.docx")

    via_markdown = parse_markdown(docx_to_markdown(path))
    via_python_docx = parse_docx(path)

    # Same content in the same order; only the Title style's level differs (python-docx: 0, markdown: h1).
    strip_level = lambda els: [{k: v for k, v in e.items() if k != "level"} for e in els]  # noqa: E731
    assert strip_level(via_markdown) == strip_level(via_python_docx)
