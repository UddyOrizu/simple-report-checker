import logging
import os
from unittest import result
from unittest import result
import uuid

import yaml

from app.agents.extract_claims import extract_claims_for_document
from app.agents.verify_claim import verify_claim
from app.db import async_session
from app.events.broadcaster import broadcaster
from app.ingestion.large_file import process_large_pdf
from app.ingestion.pipeline import finalize_pdf_structure, load_config, run_ingestion
from app.llm.client import MissingCredentialsError
from app.models import Claim, Document

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "config")


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
        await finalize_pdf_structure(document_id, path, result["page_count"], config)
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
    """
    config = load_config()
    thresholds = _load_yaml("thresholds.yaml")
    registry = _load_yaml("domain_registry.yaml")

    try:
        await _ingest(document_id, path, config)
    except Exception:
        logger.exception("Ingestion failed for document %s", document_id)
        await _mark_failed(document_id)
        return

    async with async_session() as session:
        claims = await extract_claims_for_document(session, document_id, registry)

    for claim_stub in claims:
        async with async_session() as session:
            claim = await session.get(Claim, claim_stub.id)
            try:
                result = await verify_claim(session, claim, config={}, thresholds=thresholds, registry=registry)
            except MissingCredentialsError:
                continue

            print(f"Claim {claim.id} verification result: {result}")

            final_verdict = None

            if result and result.get("verifier"):
                final_verdict = result["verifier"].final_verdict

            severity = result.get("severity") if result else None

            
            await broadcaster.publish(
                document_id,
                {
                    "event": "claim_verified",
                    "claim_id": str(claim.id),
                    "final_verdict":final_verdict,
                    "severity": severity,
                },
            )

    async with async_session() as session:
        document = await session.get(Document, document_id)
        document.status = "complete"
        await session.commit()

    await broadcaster.publish(document_id, {"event": "verification_complete"})


async def _mark_failed(document_id: uuid.UUID) -> None:
    async with async_session() as session:
        document = await session.get(Document, document_id)
        document.status = "failed"
        await session.commit()
    await broadcaster.publish(document_id, {"event": "ingest_failed"})
