"""Core ingestion pipeline: parse -> chunk -> structural index -> section summaries -> persist,
against an EXISTING documents row. Shared by scripts/run_ingest.py (which creates that row itself
for standalone CLI use) and the real upload endpoint's background processing (Phase 9.1, whose
row already exists by the time this runs).
"""

import hashlib
import json
import os
import time
import uuid

import pymupdf as fitz
import yaml
from sqlalchemy import update

from app.db import async_session
from app.ingestion.chunker import chunk_document
from app.ingestion.parsers.docx_parser import parse_docx
from app.ingestion.parsers.pdf_parser import parse_pdf
from app.ingestion.section_summarizer import generate_all_section_summaries
from app.ingestion.structural_index import build_structural_index
from app.llm.client import MissingCredentialsError
from app.models import Document, DocumentChunk, DocumentSection, ExtractedTable, PipelineRun
from app.ingestion.sentence_level_chunker import EmbeddingService

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "ingestion.yaml")

# python-docx exposes no real page count (that's a Word layout concern) — this rough
# words-per-page estimate is only used for docx's short_document_page_threshold check.
# PDFs get an exact page count for free from PyMuPDF and never use this.
DOCX_WORDS_PER_PAGE = 400


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def estimate_docx_page_count(elements: list[dict]) -> int:
    word_count = sum(len(e["text"].split()) for e in elements if e["type"] != "table")
    return max(1, round(word_count / DOCX_WORDS_PER_PAGE))


def parse_document(path: str, config: dict) -> tuple[list[dict], int, str]:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        elements = parse_docx(path)
        return elements, estimate_docx_page_count(elements), "docx"
    if ext == ".pdf":
        elements = parse_pdf(path, config)
        with fitz.open(path) as doc:
            page_count = len(doc)
        return elements, page_count, "pdf"
    raise ValueError(f"Unsupported file type: {ext}")


def document_title(elements: list[dict], filename: str) -> str:
    first_heading = next((e["text"] for e in elements if e["type"] == "heading"), None)
    return first_heading or filename


async def summarize_sections(sections: list[dict], elements: list[dict], config: dict) -> tuple[dict, dict, bool]:
    """Returns (summaries by order_index, trace by order_index, llm_blocked)."""
    summarizable = []
    for section in sections:
        body = elements[section["start_index"] : section["end_index"]]
        texts = [json.dumps(e["data"]) if e["type"] == "table" else e["text"] for e in body]
        summarizable.append({"order_index": section["order_index"], "title": section["title"], "chunks": texts})

    trace: dict[int, list[dict]] = {}

    def on_trace(order_index: int, prompt: str, response: str) -> None:
        trace.setdefault(order_index, []).append({"prompt": prompt, "response": response})

    try:
        summaries = await generate_all_section_summaries(summarizable, config, on_trace=on_trace)
        return summaries, trace, False
    except MissingCredentialsError:
        return {}, {}, True


async def run_ingestion(document_id: uuid.UUID, path: str, config: dict) -> dict:
    """Parses, chunks, structurally indexes, and summarizes `path`, then persists everything
    against the existing `document_id` row (updating its file_type/page_count/
    has_structural_index/status) and writes a pipeline_runs row."""
    start = time.monotonic()

    elements, page_count, file_type = parse_document(path, config)
    title = document_title(elements, os.path.basename(path))
    chunks = chunk_document(elements, document_title=title)
    sections = build_structural_index(page_count, elements, config)

    embedding_service = EmbeddingService()

    for chunk in chunks:
        chunk["embedding"] = await embedding_service.embed_text(chunk["chunk_text"])

    summary_trace: dict[int, list[dict]] = {}
    llm_blocked = False
    if sections:
        summaries, summary_trace, llm_blocked = await summarize_sections(sections, elements, config)
        for section in sections:
            summary = summaries.get(section["order_index"])
            section["summary"] = summary
            if summary is not None:
                section["summary_word_count"] = len(summary.split())
                calls = summary_trace.get(section["order_index"], [])
                section["summary_method"] = "batch_and_reduce" if len(calls) > 1 else "direct"
            else:
                section["summary_word_count"] = None
                section["summary_method"] = "blocked_credentials" if llm_blocked else None

    duration_ms = int((time.monotonic() - start) * 1000)


    pipeline_run_id = await _persist(
        document_id, path, file_type, page_count, config, elements, chunks, sections, summary_trace, duration_ms
    )

    return {
        "document_id": str(document_id),
        "pipeline_run_id": pipeline_run_id,
        "filename": os.path.basename(path),
        "file_type": file_type,
        "page_count": page_count,
        "has_structural_index": sections is not None,
        "chunk_count": len(chunks),
        "table_count": sum(1 for c in chunks if c["chunk_type"] == "table"),
        "section_count": len(sections) if sections else 0,
        "duration_ms": duration_ms,
        "elements": elements,
        "chunks": chunks,
        "sections": sections,
    }


async def _persist(
    document_id: uuid.UUID,
    path: str,
    file_type: str,
    page_count: int,
    config: dict,
    elements: list[dict],
    chunks: list[dict],
    sections: list[dict] | None,
    summary_trace: dict[int, list[dict]],
    duration_ms: int,
) -> str:
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]

    async with async_session() as session:
        document = await session.get(Document, document_id)
        document.file_type = file_type
        document.page_count = page_count
        document.has_structural_index = sections is not None
        document.status = "ingested"

        section_id_by_order: dict[int, object] = {}
        if sections:
            for section in sections:
                row = DocumentSection(
                    document_id=document_id,
                    title=section["title"],
                    is_pseudo_section=section["is_pseudo_section"],
                    summary=section.get("summary"),
                    order_index=section["order_index"],
                    page_start=section["page_start"],
                    page_end=section["page_end"],
                )
                session.add(row)
                await session.flush()
                section_id_by_order[section["order_index"]] = row.id

        def section_id_for(element_index: int):
            if not sections:
                return None
            for section in sections:
                if section["start_index"] <= element_index < section["end_index"]:
                    return section_id_by_order[section["order_index"]]
            return None

        for chunk in chunks:
            element = elements[chunk["element_index"]]
            section_id = section_id_for(chunk["element_index"])
            session.add(
                DocumentChunk(
                    document_id=document_id,
                    section_id=section_id,
                    chunk_type=chunk["chunk_type"],
                    chunk_text=chunk["chunk_text"],
                    context_capsule=chunk["context_capsule"],
                    page_number=chunk.get("page_number"),
                    char_start=chunk["char_start"],
                    char_end=chunk["char_end"],
                    ocr_confidence=chunk.get("ocr_confidence"),
                    embedding=chunk["embedding"],
                )
            )
            if chunk["chunk_type"] == "table":
                session.add(
                    ExtractedTable(
                        document_id=document_id,
                        section_id=section_id,
                        page_number=chunk.get("page_number"),
                        table_data=element["data"],
                    )
                )

        raw_output = {
            "summary_trace": {
                str(section_id_by_order[order_index]): calls for order_index, calls in summary_trace.items()
            },
            "result_summary": {
                "page_count": page_count,
                "has_structural_index": sections is not None,
                "chunk_count": len(chunks),
                "section_count": len(sections) if sections else 0,
            },
        }
        pipeline_run = PipelineRun(
            document_id=document_id,
            stage="ingest",
            config_hash=config_hash,
            input_ref=path,
            raw_output=raw_output,
            duration_ms=duration_ms,
        )
        session.add(pipeline_run)

        await session.commit()
        return str(pipeline_run.id)


async def finalize_pdf_structure(document_id: uuid.UUID, path: str, page_count: int, config: dict) -> None:
    """Runs structural indexing + section summarization for a PDF already ingested via
    large_file.py's page-by-page path (Phase 2.7) — that path stays memory-bounded by never
    holding the whole document's elements at once, so it can't build the structural index
    inline the way the whole-document pipeline above does. Re-parses the file (a second pass,
    not free, but the deterministic parsing itself is fast — the point of the page-by-page path
    was bounding memory and offloading OCR, not avoiding a second parse) to get elements once
    more, builds sections, and re-links the already-persisted chunks/tables to them by page
    range. Finally marks the document ingested."""
    elements = parse_pdf(path, config)
    sections = build_structural_index(page_count, elements, config)

    async with async_session() as session:
        if sections:
            summaries, _, _ = await summarize_sections(sections, elements, config)
            for section in sections:
                row = DocumentSection(
                    document_id=document_id,
                    title=section["title"],
                    is_pseudo_section=section["is_pseudo_section"],
                    summary=summaries.get(section["order_index"]),
                    order_index=section["order_index"],
                    page_start=section["page_start"],
                    page_end=section["page_end"],
                )
                session.add(row)
                await session.flush()

                page_start = section["page_start"] if section["page_start"] is not None else 0
                page_end = section["page_end"] if section["page_end"] is not None else 10**9
                await session.execute(
                    update(DocumentChunk)
                    .where(DocumentChunk.document_id == document_id)
                    .where(DocumentChunk.page_number >= page_start)
                    .where(DocumentChunk.page_number <= page_end)
                    .values(section_id=row.id)
                )
                await session.execute(
                    update(ExtractedTable)
                    .where(ExtractedTable.document_id == document_id)
                    .where(ExtractedTable.page_number >= page_start)
                    .where(ExtractedTable.page_number <= page_end)
                    .values(section_id=row.id)
                )

        document = await session.get(Document, document_id)
        document.page_count = page_count
        document.has_structural_index = sections is not None
        document.status = "ingested"
        await session.commit()
