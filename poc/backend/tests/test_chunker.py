import os

import yaml

from app.ingestion.chunker import chunk_document, is_low_confidence_ocr
from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.parsers.pdf_parser import parse_pdf


def _config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")
    return yaml.safe_load(open(config_path))


def test_chunks_align_with_paragraph_and_table_boundaries(fixtures_dir):
    path = os.path.join(fixtures_dir, "sample_report.docx")
    elements = parse_docx(path)
    chunks = chunk_document(elements, document_title="Q3 Business Report")

    # no chunk spans more than one source element — no fixed-window merging/splitting
    source_texts = {
        (e["text"] if e["type"] != "table" else None) for e in elements if e["type"] != "heading"
    }
    for chunk in chunks:
        if chunk["chunk_type"] != "table":
            assert chunk["chunk_text"] in source_texts

    types = [c["chunk_type"] for c in chunks]
    assert "table" in types
    assert "paragraph" in types

    table_chunk = next(c for c in chunks if c["chunk_type"] == "table")
    assert "Revenue (current period)" in table_chunk["chunk_text"]
    assert "$112M" in table_chunk["chunk_text"]


def test_context_capsule_carries_document_and_section_title(fixtures_dir):
    path = os.path.join(fixtures_dir, "sample_report.docx")
    elements = parse_docx(path)
    chunks = chunk_document(elements, document_title="Q3 Business Report")

    financial_chunk = next(c for c in chunks if "Revenue grew 12%" in c["chunk_text"])
    assert financial_chunk["context_capsule"] == "Q3 Business Report > Financial Highlights"


def test_char_offsets_are_non_overlapping_and_increasing(fixtures_dir):
    path = os.path.join(fixtures_dir, "sample_report.docx")
    elements = parse_docx(path)
    chunks = chunk_document(elements, document_title="Q3 Business Report")

    for prev, curr in zip(chunks, chunks[1:]):
        assert curr["char_start"] >= prev["char_end"]
    for chunk in chunks:
        assert chunk["char_end"] - chunk["char_start"] == len(chunk["chunk_text"])


def test_ocr_confidence_carried_through_and_flagged(fixtures_dir):
    config = _config()
    path = os.path.join(fixtures_dir, "scanned_report.pdf")
    elements = parse_pdf(path, config)
    chunks = chunk_document(elements, document_title="Annual Compliance Memo")

    assert all("ocr_confidence" in c for c in chunks)
    assert all(is_low_confidence_ocr(c, config) is False for c in chunks)

    low_conf_chunk = dict(chunks[0])
    low_conf_chunk["ocr_confidence"] = config["ocr_confidence_threshold"] - 0.01
    assert is_low_confidence_ocr(low_conf_chunk, config) is True


def test_native_chunks_have_no_ocr_confidence(fixtures_dir):
    path = os.path.join(fixtures_dir, "native_report.pdf")
    elements = parse_pdf(path, _config())
    chunks = chunk_document(elements, document_title="Q3 Business Report")

    assert all("ocr_confidence" not in c for c in chunks)
