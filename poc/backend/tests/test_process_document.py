import os
import uuid

from sqlalchemy import select

from app.agents.process_document import process_document
from app.db import async_session
from app.events.broadcaster import broadcaster
from app.models import Document, DocumentChunk


async def test_process_document_completes_end_to_end_without_an_api_key(fixtures_dir):
    """Ingestion (deterministic) should always finish and leave the document browsable, even
    with zero claims verified, when ANTHROPIC_API_KEY is missing — the graceful-degradation
    contract, exercised through the real top-level orchestrator."""
    assert not os.environ.get("ANTHROPIC_API_KEY")

    path = os.path.join(fixtures_dir, "sample_report.docx")
    async with async_session() as session:
        document = Document(filename="sample_report.docx", file_type="docx", storage_path=path, status="queued")
        session.add(document)
        await session.commit()
        document_id = document.id

    queue = broadcaster.subscribe(document_id)

    await process_document(document_id, path)

    async with async_session() as session:
        document = await session.get(Document, document_id)
        assert document.status == "complete"
        assert document.page_count is not None

        chunks = (await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == document_id))).scalars().all()
        assert len(chunks) == 4  # sample_report.docx's known chunk count

        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    broadcaster.unsubscribe(document_id, queue)

    event_names = [e["event"] for e in events]
    assert "ingest_complete" in event_names
    assert "verification_complete" in event_names
    assert event_names[-1] == "verification_complete"
    # no claim_verified events — every claim needing verification also needs the missing key
    assert "claim_verified" not in event_names


async def test_process_document_marks_failed_on_a_bad_path():
    document_id = uuid.uuid4()
    async with async_session() as session:
        document = Document(
            id=document_id, filename="missing.pdf", file_type="pdf", storage_path="/no/such/file.pdf", status="queued"
        )
        session.add(document)
        await session.commit()

    await process_document(document_id, "/no/such/file.pdf")

    async with async_session() as session:
        document = await session.get(Document, document_id)
        assert document.status == "failed"

        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()
