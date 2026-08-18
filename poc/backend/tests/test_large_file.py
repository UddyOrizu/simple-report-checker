import asyncio
import os
import resource
import sys

import pytest_asyncio
import yaml
from sqlalchemy import delete, select

from app.db import async_session
from app.events.broadcaster import broadcaster
from app.ingestion.large_file import process_large_pdf
from app.models import Document, DocumentChunk, ExtractedTable

DOCUMENT_TITLE = "Annual Regional Performance Report"


def _config():
    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")
    return yaml.safe_load(open(config_path))


@pytest_asyncio.fixture
async def large_report_document(fixtures_dir):
    """process_large_pdf writes directly to the real dev DB (not the rollback-wrapped
    db_session fixture used elsewhere), since it exercises app.db.async_session the same way
    production background processing will — so this fixture cleans up explicitly instead."""
    async with async_session() as session:
        document = Document(
            filename="large_report.pdf",
            file_type="pdf",
            storage_path=os.path.join(fixtures_dir, "large_report.pdf"),
            status="processing",
        )
        session.add(document)
        await session.commit()
        document_id = document.id

    yield document_id

    async with async_session() as session:
        await session.execute(delete(Document).where(Document.id == document_id))
        await session.commit()


async def test_thread_pool_offload_does_not_block_event_loop(large_report_document, fixtures_dir):
    path = os.path.join(fixtures_dir, "large_report.pdf")
    config = _config()

    tick_count = 0
    stop = False

    async def ticker():
        nonlocal tick_count
        while not stop:
            tick_count += 1
            await asyncio.sleep(0.05)

    ticker_task = asyncio.create_task(ticker())
    await process_large_pdf(large_report_document, path, DOCUMENT_TITLE, config)
    stop = True
    await ticker_task

    # a naive synchronous implementation would block the event loop for the whole run, so the
    # ticker would barely advance; dozens of ticks proves the loop stayed responsive throughout
    assert tick_count > 20


async def test_progress_events_arrive_at_configured_interval(large_report_document, fixtures_dir):
    path = os.path.join(fixtures_dir, "large_report.pdf")
    config = _config()
    every_n = config["progress_event_every_n_pages"]

    queue = broadcaster.subscribe(large_report_document)
    result = await process_large_pdf(large_report_document, path, DOCUMENT_TITLE, config)
    broadcaster.unsubscribe(large_report_document, queue)

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())

    progress_events = [e for e in events if e["event"] == "ingest_progress"]
    complete_events = [e for e in events if e["event"] == "ingest_complete"]

    assert progress_events
    pages_done = [e["pages_done"] for e in progress_events]
    expected = sorted(set(range(every_n, result["page_count"], every_n)) | {result["page_count"]})
    assert pages_done == expected
    assert progress_events[-1]["pages_total"] == result["page_count"]

    # ingest_complete fires exactly once, after every progress event
    assert len(complete_events) == 1
    assert events[-1]["event"] == "ingest_complete"
    assert complete_events[0]["page_count"] == result["page_count"]


async def test_memory_does_not_scale_with_page_count(large_report_document, fixtures_dir):
    path = os.path.join(fixtures_dir, "large_report.pdf")
    config = _config()

    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    await process_large_pdf(large_report_document, path, DOCUMENT_TITLE, config)
    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    # ru_maxrss is KB on Linux, bytes on macOS — normalize to MB either way
    units_per_mb = 1024 * 1024 if sys.platform == "darwin" else 1024
    growth_mb = (rss_after - rss_before) / units_per_mb
    # incremental per-page persistence means peak memory holds ~one page's elements at a time,
    # not all 80 pages' worth — well under what accumulating everything in a list would cost
    assert growth_mb < 300


async def test_persists_chunks_and_tables_incrementally(large_report_document, fixtures_dir):
    path = os.path.join(fixtures_dir, "large_report.pdf")
    config = _config()

    result = await process_large_pdf(large_report_document, path, DOCUMENT_TITLE, config)

    assert result["page_count"] == 80
    assert result["table_count"] == 1

    async with async_session() as session:
        chunks = (
            await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == large_report_document))
        ).scalars().all()
        tables = (
            await session.execute(select(ExtractedTable).where(ExtractedTable.document_id == large_report_document))
        ).scalars().all()

    assert len(chunks) == result["chunk_count"]
    assert len(tables) == 1

    # the far-apart claim/evidence pair Phase 5.1.1 depends on: a claim on page 1, its
    # supporting table ~75 pages later, in a different section
    claim_chunk = next(c for c in chunks if "Regional Performance Appendix" in c.chunk_text and c.page_number == 1)
    assert claim_chunk.context_capsule == DOCUMENT_TITLE  # title heading immediately preceding it, not duplicated

    appendix_table = tables[0]
    assert appendix_table.page_number == 78
    assert appendix_table.table_data == [
        ["Metric", "Value"],
        ["Revenue (current period)", "$112M"],
        ["Revenue (prior period)", "$100M"],
    ]

    # OCR-routed pages produced chunks with confidence scores, alongside native ones without
    ocr_chunks = [c for c in chunks if c.ocr_confidence is not None]
    native_chunks = [c for c in chunks if c.ocr_confidence is None]
    assert ocr_chunks
    assert native_chunks
    assert {c.page_number for c in ocr_chunks} <= {15, 28, 41, 54, 67}
