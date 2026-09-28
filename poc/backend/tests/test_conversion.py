import os
from unittest.mock import patch

import pymupdf as fitz

from app.ingestion.conversion import convert_document

SEPARATOR = "\n\n" + "-" * 48 + "\n\n"
PAGE_TEXTS = [
    "Alpha revenue discussion opens the report",
    "",  # blank page — marker drops its page separator
    "Gamma regional breakdown for APAC markets",
    "Delta cost programme savings summary",
    "Epsilon headcount and attrition figures",
    "Zeta outlook for the coming year",
    "Eta appendix with supporting tables",
]


def _make_pdf(tmp_path) -> str:
    path = os.path.join(tmp_path, "report.pdf")
    doc = fitz.open()
    for text in PAGE_TEXTS:
        page = doc.new_page()
        if text:
            page.insert_text((72, 72), text)
    doc.save(path)
    doc.close()
    return path


def _config(pages_per_batch: int) -> dict:
    return {
        "native_text_char_threshold": 20,
        "markdown_conversion": {"pdf": True, "docx": True, "marker_pages_per_batch": pages_per_batch},
    }


def _fake_marker(calls: list):
    """Mimics marker: paginated output for the requested page range, with no page break emitted
    for a page that has no text (marker filters out empty blocks, separator included)."""

    def fake(path, start_page, max_pages, config):
        calls.append((start_page, max_pages))
        pages = [t for t in PAGE_TEXTS[start_page : start_page + max_pages] if t]
        return SEPARATOR.join(pages)

    return fake


async def _convert(path, config, calls, progress):
    async def on_progress(done, total):
        progress.append((done, total))

    with patch("app.ingestion.conversion.marker_available", return_value=True), patch(
        "app.ingestion.conversion.pdf_pages_to_markdown", side_effect=_fake_marker(calls)
    ):
        return await convert_document(path, config, on_progress=on_progress)


async def test_marker_converts_in_page_batches_with_progress_between(tmp_path):
    path = _make_pdf(tmp_path)
    calls, progress = [], []

    converted = await _convert(path, _config(pages_per_batch=3), calls, progress)

    assert calls == [(0, 3), (3, 3), (6, 1)]
    assert progress == [(3, 7), (6, 7), (7, 7)]
    assert converted.page_count == 7
    assert converted.file_type == "pdf"


async def test_page_numbers_stay_correct_across_batches_and_blank_pages(tmp_path):
    path = _make_pdf(tmp_path)

    converted = await _convert(path, _config(pages_per_batch=3), [], [])

    pages = {e["text"].split()[0]: e["page_number"] for e in converted.elements}
    # Gamma is on page 3: the blank page 2 cost the first batch a separator, which would have
    # put Gamma on page 2 without re-deriving page numbers from the PDF's text layer.
    assert pages == {"Alpha": 1, "Gamma": 3, "Delta": 4, "Epsilon": 5, "Zeta": 6, "Eta": 7}


async def test_one_batch_for_the_whole_document_gives_the_same_result(tmp_path):
    path = _make_pdf(tmp_path)

    batched = await _convert(path, _config(pages_per_batch=3), [], [])
    whole = await _convert(path, _config(pages_per_batch=100), [], [])

    assert batched.elements == whole.elements


async def test_native_fallback_reports_progress_per_page(tmp_path):
    path = _make_pdf(tmp_path)
    progress = []

    async def on_progress(done, total):
        progress.append((done, total))

    with patch("app.ingestion.conversion.marker_available", return_value=False):
        converted = await convert_document(path, _config(pages_per_batch=3), on_progress=on_progress)

    assert progress == [(p, 7) for p in range(1, 8)]
    assert converted.markdown  # rendered from elements when no converter produced Markdown


async def test_docx_is_a_single_batch(fixtures_dir):
    progress = []

    async def on_progress(done, total):
        progress.append((done, total))

    converted = await convert_document(os.path.join(fixtures_dir, "sample_report.docx"), _config(3), on_progress=on_progress)

    assert converted.file_type == "docx"
    assert progress == [(converted.page_count, converted.page_count)]
    assert converted.markdown.startswith("# Q3 Business Report")
