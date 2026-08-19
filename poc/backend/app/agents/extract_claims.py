import asyncio
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.decomposer import decompose_sentence
from app.events.broadcaster import broadcaster
from app.llm.client import MissingCredentialsError
from app.models import Claim, DocumentChunk, DocumentSection
from app.nlp.clause_filter import needs_decomposition
from app.nlp.domain_router import classify_domain
from app.nlp.spacy_pipeline import get_nlp


def split_sentences(text: str) -> list[str]:
    doc = get_nlp()(text)
    return [sent.text.strip() for sent in doc.sents if sent.text.strip()]


def direct_claim(sentence: str) -> dict:
    """A simple single-fact sentence skips the decomposition LLM call entirely (Phase 3.2) —
    becomes one claim record via a lightweight heuristic instead of a model call."""
    claim_type = "statistical" if re.search(r"\d", sentence) else "definitional"
    return {"text": sentence, "claim_type": claim_type, "scope": "internal", "source_span": sentence, "requires": []}


async def extract_claims_for_document(session: AsyncSession, document_id: uuid.UUID, registry: list[dict]) -> list[Claim]:
    """Walks every non-table chunk in a document, splits it into sentences, decomposes each
    sentence into claims (or takes the single-fact fast path), classifies each claim's domain
    (Phase 4), and persists the results. Publishes a claim_extracted event per claim. Degrades
    gracefully — not a crash — if ANTHROPIC_API_KEY is missing: decomposable sentences are
    simply skipped rather than retried per-sentence once the key is known to be absent.

    Sentence splitting, clause filtering, and domain classification all run the spaCy transformer
    pipeline — synchronous, CPU-bound work offloaded via asyncio.to_thread, same reasoning as
    Phase 2.7's OCR offload: run inline, a long document would block every other request the API
    is handling for the whole extraction run."""
    chunks = (
        await session.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == document_id).where(DocumentChunk.chunk_type != "table")
        )
    ).scalars().all()

    persisted: list[Claim] = []
    llm_blocked = False

    for chunk in chunks:
        section = await session.get(DocumentSection, chunk.section_id) if chunk.section_id else None

        sentences = await asyncio.to_thread(split_sentences, chunk.chunk_text)
        for sentence in sentences:
            if await asyncio.to_thread(needs_decomposition, sentence):
                if llm_blocked:
                    continue
                try:
                    claim_list = await decompose_sentence(sentence, chunk.context_capsule)
                    print(f"Decomposed sentence into {len(claim_list.claims)} claims: {claim_list.claims}")
                    extracted = [c.model_dump() for c in claim_list.claims]
                except MissingCredentialsError:
                    llm_blocked = True
                    continue
            else:
                extracted = [direct_claim(sentence)]

            for claim_data in extracted:
                domain_result = await asyncio.to_thread(classify_domain, claim_data["text"], section, registry)
                claim = Claim(
                    document_id=document_id,
                    chunk_id=chunk.id,
                    claim_text=claim_data["text"],
                    source_span=claim_data["source_span"],
                    claim_type=claim_data["claim_type"],
                    scope=claim_data["scope"],
                    requires=claim_data["requires"],
                    domain=domain_result["domain"],
                    domain_confidence=domain_result["confidence"],
                    domain_source=domain_result["source"],
                )
                session.add(claim)
                await session.flush()
                persisted.append(claim)

                await broadcaster.publish(
                    document_id,
                    {
                        "event": "claim_extracted",
                        "claim_id": str(claim.id),
                        "claim_text": claim.claim_text,
                        "claim_type": claim.claim_type,
                        "scope": claim.scope,
                    },
                )

    await session.commit()
    return persisted
