import asyncio
import logging
import os
import uuid

import yaml
from sqlalchemy import delete, select

from app.agents.extract_claims import extract_claims_for_document
from app.agents.verify_claim import verify_claim
from app.db import async_session
from app.events.broadcaster import broadcaster
from app.ingestion.large_file import process_large_pdf
from app.ingestion.pipeline import finalize_pdf_structure, load_config, run_ingestion
from app.llm.client import MissingCredentialsError
from app.models import Claim, Document, DocumentChunk, DocumentSection, ExtractedTable, Verdict

logger = logging.getLogger(__name__)

# failed_stage values for which ingestion is already known to have completed successfully — a
# resume with the document in any of these stages skips straight past _ingest rather than
# redoing it. Any other failed_stage (including None, for a document that never got an explicit
# failure marker — e.g. the process was killed outright) means ingestion must be (re)run.
_POST_INGEST_STAGES = ("extract_claims", "verify")

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config")

# Bounded fan-out for per-claim verification — each claim gets its own AsyncSession (required
# for true concurrency; SQLAlchemy sessions aren't safe to share across concurrent coroutines),
# capped so a large document's hundreds of claims don't open hundreds of connections/agent runs
# at once.
VERIFICATION_CONCURRENCY = 4


def _load_yaml(name: str):
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return yaml.safe_load(f)


async def _ingest(document_id: uuid.UUID, path: str, config: dict) -> None:
    """PDFs go through Phase 2.7's page-by-page path — thread-offloaded parsing/OCR, bounded
    memory, and real ingest_progress events, which matter most on exactly the large documents
    this branch exists for. DOCX files go through the simpler whole-document path (Phase 2.6);
    python-docx has no per-page concept to stream over in the first place."""
    if path.lower().endswith(".pdf"):
        result = await process_large_pdf(document_id, path, os.path.basename(path), config)
        await finalize_pdf_structure(document_id, path, result["page_count"], config, elements=result["elements"])
    else:
        await run_ingestion(document_id, path, config)
        await broadcaster.publish(document_id, {"event": "ingest_complete"})


async def process_document(document_id: uuid.UUID, path: str) -> None:
    """The full background pipeline behind POST /documents: ingest -> extract claims -> verify
    each claim, publishing SSE events throughout. Deterministic stages (ingestion, arithmetic
    verification) always complete; LLM-dependent stages degrade gracefully rather than crash the
    whole run when ANTHROPIC_API_KEY is missing, per the .env.example BLOCKED-CREDENTIALS
    contract — the document still ends up fully ingested and browsable even with zero claims
    verified.

    Resume-aware by construction, so this same function backs both a fresh upload and
    POST /documents/{id}/resume: each stage is skipped or picked back up based on what's already
    persisted, rather than assuming a clean start.
      - Ingest: skipped once the document's status/failed_stage show it already completed.
        Otherwise any chunks/sections/tables left over from an attempt that didn't finish are
        discarded and the stage runs in full — ingestion has no cheaper partial-resume point,
        since a PDF's page-by-page writes and a DOCX's one-shot persist both leave no reliable
        record of exactly how far they got.
      - Extract claims: extract_claims_for_document itself only queries chunks not yet marked
        claims_extracted, so a resumed call redoes at most the one chunk that was in flight.
      - Verify: only claims without an existing verdicts row are (re)dispatched, so already-
        verified claims are never touched again.
    A stage that raises marks the document "failed" with failed_stage set to that stage, which is
    exactly what a resume call reads to decide where to pick back up.
    """
    config = load_config()
    thresholds = _load_yaml("thresholds.yaml")
    registry = _load_yaml("domain_registry.yaml")

    async with async_session() as session:
        document = await session.get(Document, document_id)
        # failed_stage alone (not paired with status == "failed") decides this: a resume call
        # moves status to "processing" before this function ever runs, precisely so the document
        # doesn't sit there showing "failed" while it's actively being retried — failed_stage is
        # left in place as the record of how far a previous attempt got.
        ingestion_done = document.status in ("ingested", "complete") or document.failed_stage in _POST_INGEST_STAGES

    if not ingestion_done:
        await _discard_partial_ingestion(document_id)
        try:
            await _ingest(document_id, path, config)
        except Exception:
            logger.exception("Ingestion failed for document %s", document_id)
            await _mark_failed(document_id, "ingest")
            return

    try:
        async with async_session() as session:
            await extract_claims_for_document(session, document_id, registry)
    except Exception:
        logger.exception("Claim extraction failed for document %s", document_id)
        await _mark_failed(document_id, "extract_claims")
        return

    semaphore = asyncio.Semaphore(VERIFICATION_CONCURRENCY)

    async def _verify_one(claim_stub: Claim) -> None:
        async with async_session() as session:
            claim = await session.get(Claim, claim_stub.id)
            try:
                async with semaphore:
                    result = await verify_claim(session, claim, config={}, thresholds=thresholds, registry=registry)
            except MissingCredentialsError:
                return

            final_verdict = None
            severity = None
            if result and result.get("reconciled"):
                final_verdict = result["reconciled"]["final_verdict"]
                severity = result["reconciled"]["severity"]

            await broadcaster.publish(
                document_id,
                {
                    "event": "claim_verified",
                    "claim_id": str(claim.id),
                    "final_verdict": final_verdict,
                    "severity": severity,
                },
            )

    pending_claims = await _claims_pending_verification(document_id)
    # return_exceptions=True: one claim's verification blowing up shouldn't cancel every other
    # claim still in flight — collect failures and mark the document resumable instead.
    results = await asyncio.gather(*(_verify_one(claim) for claim in pending_claims), return_exceptions=True)
    failures = [r for r in results if isinstance(r, BaseException)]
    if failures:
        logger.error(
            "Verification failed for %d/%d claim(s) on document %s",
            len(failures),
            len(pending_claims),
            document_id,
            exc_info=failures[0],
        )
        await _mark_failed(document_id, "verify")
        return

    async with async_session() as session:
        document = await session.get(Document, document_id)
        document.status = "complete"
        document.failed_stage = None
        await session.commit()

    await broadcaster.publish(document_id, {"event": "verification_complete"})


async def _claims_pending_verification(document_id: uuid.UUID) -> list[Claim]:
    """All of a document's claims that don't yet have a verdicts row — on a fresh run that's
    every extracted claim; on a resume it's only the ones an earlier attempt never got to."""
    async with async_session() as session:
        already_verified = select(Verdict.claim_id)
        stmt = select(Claim).where(Claim.document_id == document_id, Claim.id.not_in(already_verified))
        return (await session.execute(stmt)).scalars().all()


async def _discard_partial_ingestion(document_id: uuid.UUID) -> None:
    """Ingestion only ever marks a document "ingested" once every chunk/section/table for it is
    persisted, so anything already written for this document at this point is necessarily left
    over from an attempt that didn't finish. Deleted in FK-safe order (tables and chunks
    reference sections) before the stage reruns from scratch."""
    async with async_session() as session:
        await session.execute(delete(ExtractedTable).where(ExtractedTable.document_id == document_id))
        await session.execute(delete(DocumentChunk).where(DocumentChunk.document_id == document_id))
        await session.execute(delete(DocumentSection).where(DocumentSection.document_id == document_id))
        await session.commit()


async def _mark_failed(document_id: uuid.UUID, stage: str) -> None:
    async with async_session() as session:
        document = await session.get(Document, document_id)
        document.status = "failed"
        document.failed_stage = stage
        await session.commit()
    event = "ingest_failed" if stage == "ingest" else "processing_failed"
    await broadcaster.publish(document_id, {"event": event, "stage": stage})
