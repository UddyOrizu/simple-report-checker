"""Core ingestion pipeline — the one path every document takes, whatever its type or size:

    convert (page batches, progress events)  ->  generated headings  ->  chunk  ->  structural
    index  ->  section summaries  ->  persist (sections, then chunks + embeddings in batches)

against an EXISTING documents row. Shared by scripts/run_ingest.py (which creates that row itself
for standalone CLI use) and the upload endpoint's background processing (whose row already exists
by the time this runs).

Only conversion is incremental (see app/ingestion/conversion.py): it's the expensive, page-shaped
step — OCR / marker's layout models — and the one worth reporting progress on. Parsed elements are
text only (a few MB even for hundreds of pages), so the steps after it see the whole document at
once: generated headings need the whole document to decide structure, and chunks need final
headings for their context capsules. Chunk embeddings — the one large per-chunk payload — are
computed and written `persist_chunk_batch` chunks at a time, so memory stays bounded regardless.
"""

import hashlib
import json
import logging
import os
import time
import uuid

import yaml

from app.db import async_session
from app.events.broadcaster import broadcaster
from app.ingestion.chunker import chunk_document
from app.ingestion.conversion import convert_document
from app.ingestion.heading_generator import add_generated_headings, needs_generated_headings
from app.ingestion.parsers.markdown_parser import elements_to_markdown
from app.ingestion.section_summarizer import generate_all_section_summaries
from app.ingestion.structural_index import build_structural_index
from app.llm.client import MissingCredentialsError
from app.models import Document, DocumentChunk, DocumentSection, ExtractedTable, PipelineRun
from app.ingestion.sentence_level_chunker import EmbeddingService

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config", "ingestion.yaml")


def load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def _save_markdown(path: str, markdown: str, config: dict) -> None:
    if config.get("markdown_conversion", {}).get("save_markdown", False):
        with open(f"{path}.md", "w", encoding="utf-8") as f:
            f.write(markdown)


def section_content(section: dict, elements: list[dict]) -> str:
    """The section's own body as Markdown — what vectorless retrieval reads and quotes from."""
    return elements_to_markdown(elements[section["start_index"] : section["end_index"]])


def document_title(elements: list[dict], filename: str) -> str:
    first_heading = next((e["text"] for e in elements if e["type"] == "heading"), None)
    return first_heading or filename


def _outline_summary(section: dict, sections: list[dict]) -> str | None:
    """Summary for a section with no body of its own (a heading followed straight by a
    subheading): it names what the section contains. Asking the model to summarize empty text
    would only invent content from the title, and the navigator trusts summaries."""
    children = [s["title"] for s in sections if s.get("parent_order_index") == section["order_index"] and s["title"]]
    return f"Contains: {'; '.join(children)}" if children else None


async def summarize_sections(sections: list[dict], elements: list[dict], config: dict) -> tuple[dict, dict, bool]:
    """Returns (summaries by order_index, trace by order_index, llm_blocked). Sections with no
    body text get an outline summary from their subsections instead of an LLM call."""
    summarizable = []
    outline_summaries: dict[int, str] = {}
    for section in sections:
        body = elements[section["start_index"] : section["end_index"]]
        texts = [json.dumps(e["data"]) if e["type"] == "table" else e["text"] for e in body]
        if not any(t.strip() for t in texts):
            summary = _outline_summary(section, sections)
            if summary:
                outline_summaries[section["order_index"]] = summary
            continue
        summarizable.append({"order_index": section["order_index"], "title": section["title"], "chunks": texts})

    trace: dict[int, list[dict]] = {}

    def on_trace(order_index: int, prompt: str, response: str) -> None:
        trace.setdefault(order_index, []).append({"prompt": prompt, "response": response})

    try:
        summaries = await generate_all_section_summaries(summarizable, config, on_trace=on_trace)
        return {**summaries, **outline_summaries}, trace, False
    except MissingCredentialsError:
        return outline_summaries, {}, True


async def run_ingestion(document_id: uuid.UUID, path: str, config: dict) -> dict:
    """Converts, chunks, structurally indexes, and summarizes `path`, then persists everything
    against the existing `document_id` row (updating its file_type/page_count/
    has_structural_index/markdown/status) and writes a pipeline_runs row. Publishes
    ingest_progress events during conversion and one ingest_complete at the end."""
    start = time.monotonic()
    every_n = config["progress_event_every_n_pages"]
    last_reported = 0

    async def on_progress(pages_done: int, pages_total: int) -> None:
        # Fires whenever conversion crosses a multiple of every_n pages (a marker batch can cross
        # one mid-batch — reported with its real pages_done) and always on the final page.
        nonlocal last_reported
        if pages_done // every_n > last_reported // every_n or pages_done == pages_total:
            await broadcaster.publish(document_id, {"event": "ingest_progress", "pages_done": pages_done, "pages_total": pages_total})
        last_reported = pages_done

    converted = await convert_document(path, config, on_progress=on_progress)
    _save_markdown(path, converted.markdown, config)
    elements, page_count, file_type = converted.elements, converted.page_count, converted.file_type

    title = document_title(elements, os.path.basename(path))
    # Before chunking, so chunks' context capsules carry the generated section titles too. The
    # document title is taken first so a generated heading can never become it.
    heading_trace: list[dict] = []
    if needs_generated_headings(elements, page_count, config):
        elements = await add_generated_headings(
            elements, config, on_trace=lambda prompt, response: heading_trace.append({"prompt": prompt, "response": response})
        )
    chunks = chunk_document(elements, document_title=title)
    sections = build_structural_index(page_count, elements, config)

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
                section["summary_method"] = "batch_and_reduce" if len(calls) > 1 else "direct" if calls else "outline"
            else:
                section["summary_word_count"] = None
                section["summary_method"] = "blocked_credentials" if llm_blocked else None

    pipeline_run_id = await _persist(
        document_id, path, file_type, page_count, config, elements, chunks, sections, summary_trace, start,
        heading_trace=heading_trace,
    )
    duration_ms = int((time.monotonic() - start) * 1000)
    table_count = sum(1 for c in chunks if c["chunk_type"] == "table")
    await broadcaster.publish(
        document_id, {"event": "ingest_complete", "page_count": page_count, "chunk_count": len(chunks), "table_count": table_count}
    )

    return {
        "document_id": str(document_id),
        "pipeline_run_id": pipeline_run_id,
        "filename": os.path.basename(path),
        "file_type": file_type,
        "page_count": page_count,
        "has_structural_index": sections is not None,
        "chunk_count": len(chunks),
        "table_count": table_count,
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
    start: float,
    heading_trace: list[dict] | None = None,
) -> str:
    """Three phases, each committed: sections (chunks reference them), then chunks + tables in
    batches of `persist_chunk_batch` (embedded just before they're written, so at most one batch
    of embedding vectors is ever held in memory), then the document's final state + the
    pipeline_runs row. The document is only marked "ingested" in that last phase, so an attempt
    that dies partway leaves it un-ingested and process_document's resume discards the partial
    rows before rerunning."""
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]
    batch_size = max(1, config.get("persist_chunk_batch", 200))

    section_id_by_order: dict[int, uuid.UUID] = {}
    section_id_by_element: list[uuid.UUID | None] = [None] * len(elements)
    if sections:
        async with async_session() as session:
            for section in sections:
                row = _section_row(document_id, section, elements, section.get("summary"), section_id_by_order)
                session.add(row)
                await session.flush()
                section_id_by_order[section["order_index"]] = row.id
                for i in range(section["start_index"], section["end_index"]):
                    section_id_by_element[i] = row.id
            await session.commit()

    embedding_service = EmbeddingService()
    for batch_start in range(0, len(chunks), batch_size):
        batch = chunks[batch_start : batch_start + batch_size]
        embeddings = await embedding_service.embed_texts([c["chunk_text"] for c in batch])
        async with async_session() as session:
            for chunk, embedding in zip(batch, embeddings):
                element = elements[chunk["element_index"]]
                section_id = section_id_by_element[chunk["element_index"]]
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
                        embedding=embedding,
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
            await session.commit()

    async with async_session() as session:
        document = await session.get(Document, document_id)
        document.file_type = file_type
        document.page_count = page_count
        document.has_structural_index = sections is not None
        document.markdown = elements_to_markdown(elements)
        document.status = "ingested"
        document.failed_stage = None

        raw_output = {
            "heading_generation_trace": heading_trace or [],
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
            duration_ms=int((time.monotonic() - start) * 1000),
        )
        session.add(pipeline_run)

        await session.commit()
        return str(pipeline_run.id)


def _section_row(
    document_id: uuid.UUID, section: dict, elements: list[dict], summary: str | None, section_id_by_order: dict[int, object]
) -> DocumentSection:
    """Sections are flushed in order_index order, so a section's parent (always earlier in the
    document) already has its id in `section_id_by_order` by the time its children are built."""
    parent_order_index = section.get("parent_order_index")
    return DocumentSection(
        document_id=document_id,
        title=section["title"],
        is_pseudo_section=section["is_pseudo_section"],
        summary=summary,
        order_index=section["order_index"],
        page_start=section["page_start"],
        page_end=section["page_end"],
        level=section.get("level"),
        parent_id=section_id_by_order.get(parent_order_index) if parent_order_index is not None else None,
        content=section_content(section, elements),
    )
