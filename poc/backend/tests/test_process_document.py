import os
import uuid

from sqlalchemy import select

from app.agents.process_document import process_document
from app.db import async_session
from app.events.broadcaster import broadcaster
from app.models import Claim, Document, DocumentChunk, Verdict


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


async def test_process_document_resumes_only_the_claims_that_never_got_a_verdict(monkeypatch):
    """Simulates a verify-stage crash on one claim among several: that claim's exception must not
    take the rest of the batch down with it (return_exceptions in the gather), the document should
    land in "failed" with failed_stage="verify" so a resume call knows where to pick up, and a
    second process_document call — exactly what POST /documents/{id}/resume triggers — must retry
    only the claim that never got a verdict, leaving the one that already succeeded untouched."""
    document_id = uuid.uuid4()
    chunk_a_id = uuid.uuid4()
    chunk_b_id = uuid.uuid4()
    claim_a_id = uuid.uuid4()
    claim_b_id = uuid.uuid4()

    async with async_session() as session:
        # status="ingested" and claims_extracted=True on both chunks put this document past the
        # ingest and extract-claims stages already, isolating the test to verify-stage resume.
        # Flushed level-by-level (document, then chunks, then claims) since a single flush across
        # all three doesn't guarantee parent-before-child insert order.
        session.add(Document(id=document_id, filename="x.docx", file_type="docx", storage_path="x", status="ingested"))
        await session.flush()
        session.add(
            DocumentChunk(
                id=chunk_a_id, document_id=document_id, chunk_type="paragraph", chunk_text="a",
                context_capsule="c", claims_extracted=True,
            )
        )
        session.add(
            DocumentChunk(
                id=chunk_b_id, document_id=document_id, chunk_type="paragraph", chunk_text="b",
                context_capsule="c", claims_extracted=True,
            )
        )
        await session.flush()
        session.add(
            Claim(
                id=claim_a_id, document_id=document_id, chunk_id=chunk_a_id, claim_text="claim-a",
                source_span="a", claim_type="statistical", scope="internal",
            )
        )
        session.add(
            Claim(
                id=claim_b_id, document_id=document_id, chunk_id=chunk_b_id, claim_text="claim-b",
                source_span="b", claim_type="statistical", scope="internal",
            )
        )
        await session.commit()

    attempts_on_a = 0

    async def fake_verify_claim(session, claim, config, thresholds, registry):
        nonlocal attempts_on_a
        if claim.id == claim_a_id and attempts_on_a == 0:
            attempts_on_a += 1
            raise RuntimeError("simulated verification crash")
        session.add(
            Verdict(claim_id=claim.id, final_verdict="supported", final_confidence=0.9, severity="info", resolved_by="deterministic")
        )
        await session.commit()
        return {"reconciled": {"final_verdict": "supported", "severity": "info"}}

    monkeypatch.setattr("app.agents.process_document.verify_claim", fake_verify_claim)

    await process_document(document_id, "x")

    async with async_session() as session:
        document = await session.get(Document, document_id)
        assert document.status == "failed"
        assert document.failed_stage == "verify"

        verdicts = (
            await session.execute(select(Verdict.claim_id).where(Verdict.claim_id.in_([claim_a_id, claim_b_id])))
        ).scalars().all()
        assert set(verdicts) == {claim_b_id}

    # Resume: re-invoking process_document is exactly what the /resume endpoint does.
    await process_document(document_id, "x")

    async with async_session() as session:
        document = await session.get(Document, document_id)
        assert document.status == "complete"
        assert document.failed_stage is None

        verdicts = (
            await session.execute(select(Verdict.claim_id).where(Verdict.claim_id.in_([claim_a_id, claim_b_id])))
        ).scalars().all()
        assert set(verdicts) == {claim_a_id, claim_b_id}

        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()

    assert attempts_on_a == 1  # resume retried claim A exactly once, not the whole batch again
