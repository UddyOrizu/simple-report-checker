import asyncio
import uuid

import pymupdf as fitz

from app.db import async_session
from app.events.broadcaster import broadcaster
from app.ingestion.chunker import chunk_document
from app.ingestion.parsers.pdf_parser import is_native_page, parse_native_page, parse_ocr_page
from app.models import DocumentChunk, ExtractedTable
from poc.backend.app.ingestion.sentence_level_chunker import EmbeddingService


async def process_page(pdf_path: str, page_number: int, config: dict) -> list[dict]:
    """Parse or OCR a single page, off the event loop — both are sync/CPU-bound calls, so a large
    scanned document run naively would block every other request the API is handling for the
    entire duration of processing."""
    native = await asyncio.to_thread(is_native_page, pdf_path, page_number, config)
    if native:
        return await asyncio.to_thread(parse_native_page, pdf_path, page_number)
    return await asyncio.to_thread(parse_ocr_page, pdf_path, page_number)


async def process_large_pdf(document_id: uuid.UUID, pdf_path: str, document_title: str, config: dict) -> dict:
    """Process a PDF page-by-page: each page's parsing/OCR is offloaded to a thread, its
    resulting chunks/tables are written to Postgres immediately (never accumulated across pages
    in memory), and an ingest_progress event is published every `progress_event_every_n_pages`
    pages so a multi-minute run shows real percentage progress instead of one final jump."""
    with fitz.open(pdf_path) as doc:
        page_count = len(doc)

    every_n = config["progress_event_every_n_pages"]
    chunk_state: dict = {"section_title": None, "offset": 0}
    total_chunks = 0
    total_tables = 0
    embedding_service = EmbeddingService()
    for page_number in range(1, page_count + 1):
        elements = await process_page(pdf_path, page_number, config)
        chunks = chunk_document(elements, document_title=document_title, state=chunk_state)

        async with async_session() as session:
            for chunk in chunks:
                element = elements[chunk["element_index"]]
                embedding = await embedding_service.embed_text(chunk["chunk_text"])
                session.add(
                    DocumentChunk(
                        document_id=document_id,
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
                total_chunks += 1
                if chunk["chunk_type"] == "table":
                    session.add(
                        ExtractedTable(
                            document_id=document_id,
                            page_number=chunk.get("page_number"),
                            table_data=element["data"],
                        )
                    )
                    total_tables += 1
            await session.commit()  # durable before moving to the next page — bounds peak memory

        if page_number % every_n == 0 or page_number == page_count:
            await broadcaster.publish(
                document_id, {"event": "ingest_progress", "pages_done": page_number, "pages_total": page_count}
            )

    result = {"page_count": page_count, "chunk_count": total_chunks, "table_count": total_tables}
    await broadcaster.publish(document_id, {"event": "ingest_complete", **result})
    return result
