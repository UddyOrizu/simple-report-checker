"""Conversion stage: turns an uploaded file into parsed elements (and its Markdown rendering), in
page batches with progress callbacks in between. The same code runs for every file size; a
one-page memo is simply a single batch.

  PDF, PyMuPDF4LLM installed  -> PyMuPDF4LLM converts `marker_pages_per_batch` pages per call -> parse_markdown
  PDF, no PyMuPDF4LLM         -> pdfplumber (native text) or Tesseract (scanned), one page per batch
  DOCX                        -> mammoth + markdownify (or python-docx), one batch — Word has no pages

Every batch is thread-offloaded (PyMuPDF4LLM and OCR are CPU-bound), so the API stays responsive
while a large document converts. Everything after conversion — generated headings, chunking, the
structural index, summaries, persistence — happens once over the whole document in pipeline.py.
"""

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Awaitable, Callable

import pdfplumber
import pymupdf as fitz

from app.ingestion.converters.docx_to_markdown import docx_to_markdown
from app.ingestion.converters.pdf_to_markdown import pymupdf4llm_available, pdf_pages_to_markdown
from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.parsers.markdown_parser import PAGE_SEPARATOR_RE, elements_to_markdown, parse_markdown
from app.ingestion.parsers.pdf_parser import is_native_page, parse_native_page, parse_ocr_page
from app.retrieval.quote_match import normalize

logger = logging.getLogger(__name__)

# python-docx exposes no real page count (that's a Word layout concern) — this rough
# words-per-page estimate is only used for docx's short_document_page_threshold check.
# PDFs get an exact page count for free from PyMuPDF and never use this.
DOCX_WORDS_PER_PAGE = 400

# How many of an element's opening words are looked up in the PDF's own text layer when a
# batch's page numbers have to be re-derived (see _realign_pages).
PAGE_ANCHOR_WORDS = 5

ProgressCallback = Callable[[int, int], Awaitable[None]]


@dataclass
class ConvertedDocument:
    elements: list[dict]
    page_count: int
    file_type: str
    markdown: str  # raw converter output (marker/mammoth), or the elements rendered to Markdown


def uses_marker_for_pdf(config: dict) -> bool:
    """PDFs go through PyMuPDF4LLM only when it's both enabled in config and actually installed — it's
    an optional extra, and a missing install falls back to the pdfplumber/OCR parser rather than
    failing the upload."""
    return config.get("markdown_conversion", {}).get("pdf", False) and pymupdf4llm_available()


def estimate_docx_page_count(elements: list[dict]) -> int:
    word_count = sum(len(e["text"].split()) for e in elements if e["type"] != "table")
    return max(1, round(word_count / DOCX_WORDS_PER_PAGE))


async def convert_document(path: str, config: dict, on_progress: ProgressCallback | None = None) -> ConvertedDocument:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        converted = await asyncio.to_thread(_convert_docx, path, config)
        if on_progress:
            await on_progress(converted.page_count, converted.page_count)
        return converted
    if ext == ".pdf":
        if uses_marker_for_pdf(config):
            return await _convert_pdf_with_marker(path, config, on_progress)
        if config.get("markdown_conversion", {}).get("pdf", False):
            logger.warning("markdown_conversion.pdf is enabled but PyMuPDF4LLM isn't installed — using the native PDF parser")
        return await _convert_pdf_natively(path, config, on_progress)
    raise ValueError(f"Unsupported file type: {ext}")


def _convert_docx(path: str, config: dict) -> ConvertedDocument:
    if config.get("markdown_conversion", {}).get("docx", False):
        markdown = docx_to_markdown(path)
        elements = clean_parsed_elements(parse_markdown(markdown))
    else:
        elements = clean_parsed_elements(parse_docx(path))
        markdown = elements_to_markdown(elements)
    return ConvertedDocument(elements, estimate_docx_page_count(elements), "docx", markdown)


async def _convert_pdf_with_marker(path: str, config: dict, on_progress: ProgressCallback | None) -> ConvertedDocument:
    batch_size = max(1, config.get("markdown_conversion", {}).get("marker_pages_per_batch", 10))
    with fitz.open(path) as doc:
        page_count = len(doc)

    elements: list[dict] = []
    markdown_parts: list[str] = []
    for start in range(0, page_count, batch_size):
        pages_in_batch = min(batch_size, page_count - start)
        markdown = await asyncio.to_thread(pdf_pages_to_markdown, path, start, pages_in_batch, config)
        batch = clean_parsed_elements(parse_markdown(markdown, paginated=True, first_page=start + 1))

        separators = sum(1 for line in markdown.splitlines() if PAGE_SEPARATOR_RE.match(line.strip()))
        if separators + 1 < pages_in_batch:
            # A page marker went missing (marker drops blank pages' separators), so every page
            # number after it in this batch is off — re-derive them from the PDF's text layer.
            batch = await asyncio.to_thread(_realign_pages, batch, path, start + 1, start + pages_in_batch)

        elements.extend(batch)
        markdown_parts.append(markdown)
        if on_progress:
            await on_progress(start + pages_in_batch, page_count)

    return ConvertedDocument(elements, page_count, "pdf", "\n\n".join(markdown_parts))


def _realign_pages(elements: list[dict], path: str, first_page: int, last_page: int) -> list[dict]:
    """Assigns each element the first page (at or after the previous element's page) whose native
    text contains the element's opening words. Elements that can't be located — scanned pages
    have no text layer to search — stay on the previous element's page, so numbering is always
    monotonic and never leaves the batch's page range."""
    with fitz.open(path) as doc:
        page_texts = {p: normalize(doc[p - 1].get_text().replace("-\n", "")) for p in range(first_page, last_page + 1)}

    current = first_page
    realigned = []
    for element in elements:
        text = " | ".join(" ".join(row) for row in element.get("data") or []) if element["type"] == "table" else element.get("text", "")
        anchor = normalize(" ".join(text.split()[:PAGE_ANCHOR_WORDS]))
        if anchor:
            found = next((p for p in range(current, last_page + 1) if anchor in page_texts[p]), None)
            if found is not None:
                current = found
        realigned.append({**element, "page_number": current})
    return realigned


async def _convert_pdf_natively(path: str, config: dict, on_progress: ProgressCallback | None) -> ConvertedDocument:
    """One page per batch. The file is opened once (fitz for the native/scanned decision,
    pdfplumber for layout extraction) and both handles are reused across pages — safe because
    pages are processed strictly sequentially, never from two threads at once."""
    elements: list[dict] = []
    with fitz.open(path) as doc, pdfplumber.open(path) as pdf:
        page_count = len(doc)
        for page_number in range(1, page_count + 1):
            page_elements = await asyncio.to_thread(_parse_pdf_page, path, page_number, config, doc, pdf)
            elements.extend(clean_parsed_elements(page_elements))
            if on_progress:
                await on_progress(page_number, page_count)
    return ConvertedDocument(elements, page_count, "pdf", elements_to_markdown(elements))


def _parse_pdf_page(path: str, page_number: int, config: dict, doc: fitz.Document, pdf: "pdfplumber.PDF") -> list[dict]:
    if is_native_page(path, page_number, config, doc=doc):
        return parse_native_page(path, page_number, pdf=pdf)
    return parse_ocr_page(path, page_number)


def _clean_text(text: str) -> str:
    if not text:
        return ""
    # Normalize line endings, remove BOM, collapse whitespace
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("﻿", "")
    # Collapse multiple newlines into a single newline, then collapse remaining whitespace
    text = re.sub(r"\n{2,}", "\n", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_parsed_elements(elements: list[dict]) -> list[dict]:
    """Normalize text in parsed elements immediately after file parsing so downstream
    chunking and extraction sees consistent whitespace and no stray linebreaks.
    Handles paragraphs, headings, and table cell text.
    """
    cleaned: list[dict] = []
    for el in elements:
        if el.get("type") in ("paragraph", "heading"):
            el = {**el, "text": _clean_text(el.get("text", ""))}
        elif el.get("type") == "table":
            # table `data` is a list of rows; each row is list of cell strings
            table = el.get("data") or []
            cleaned_table = [[_clean_text(cell) for cell in row] for row in table]
            el = {**el, "data": cleaned_table}
        cleaned.append(el)
    return cleaned
