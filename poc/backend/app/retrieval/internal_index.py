import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.sentence_level_chunker import EmbeddingService
from app.models import Claim, DocumentChunk, DocumentSection, Evidence, ExtractedTable
from app.retrieval.matching import citation, matches_requires, table_text

# A hit below this cosine similarity is too weak to trust as evidence on its own — this mirrors
# the "strong in-document match" threshold the router prompt itself uses (router.md rule 3) so a
# claim that was routed "internal" because of a >0.7 hit gets verified against that same bar of
# confidence, not a looser one.
SEMANTIC_EVIDENCE_SIMILARITY_THRESHOLD = 0.75


async def lookup_internal_evidence(session: AsyncSession, claim: Claim) -> list[Evidence]:
    """Direct lookup against this document's own tables/chunks for `claim.requires` — no
    embedding search. Scoped to the claim's own section when its origin chunk has one (the
    common case: evidence co-located with the claim); falls back to the whole document when the
    chunk has no section (e.g. a short document that skipped structural indexing entirely, which
    already gets full-document context by design)."""
    origin_chunk = await session.get(DocumentChunk, claim.chunk_id)
    section_id = origin_chunk.section_id if origin_chunk else None

    tables_stmt = select(ExtractedTable).where(ExtractedTable.document_id == claim.document_id)
    chunks_stmt = select(DocumentChunk).where(DocumentChunk.document_id == claim.document_id)
    if section_id is not None:
        tables_stmt = tables_stmt.where(ExtractedTable.section_id == section_id)
        chunks_stmt = chunks_stmt.where(DocumentChunk.section_id == section_id)

    tables = (await session.execute(tables_stmt)).scalars().all()
    chunks = (await session.execute(chunks_stmt)).scalars().all()

    section_title = None
    if section_id is not None:
        section_title = await session.scalar(select(DocumentSection.title).where(DocumentSection.id == section_id))

    requires = claim.requires or []
    evidence: list[Evidence] = []

    for table in tables:
        text = table_text(table.table_data)
        if matches_requires(text, requires):
            evidence.append(
                Evidence(
                    claim_id=claim.id,
                    source_type="internal_table",
                    source_ref=citation(f"extracted_table:{table.id}", table.page_number, section_title),
                    content_snippet=text,
                    authority_score=1.0,
                )
            )

    for chunk in chunks:
        if chunk.chunk_type == "table":
            continue  # covered by extracted_tables above — avoid double-counting the same evidence
        if matches_requires(chunk.chunk_text, requires):
            evidence.append(
                Evidence(
                    claim_id=claim.id,
                    source_type="internal_chunk",
                    source_ref=citation(f"document_chunk:{chunk.id}", chunk.page_number, section_title),
                    content_snippet=chunk.chunk_text,
                    authority_score=0.9,
                )
            )

    return evidence


async def query_similar(
    session: AsyncSession,
    document_id: uuid.UUID,
    query_embedding: list[float],
    top_k: int = 5,
    exclude_chunk_id: uuid.UUID | None = None,
) -> list[dict]:
    """Cosine-similarity search scoped to a single document's chunks — the semantic counterpart
    to lookup_internal_evidence's direct keyword lookup, for when the in-doc verifier needs
    nearby-meaning evidence rather than an exact requires-phrase match. `exclude_chunk_range`
    (min_chunk_index, max_chunk_index) excludes the chunk(s) a claim was originally extracted
    from, so a claim is never "verified" against its own source sentence."""

    print(f"query_similar: document_id={document_id}, top_k={top_k}, exclude_chunk_id={exclude_chunk_id}")
    print(f"query_similar: length={len(query_embedding)} query_embedding={query_embedding}")

    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    stmt = (
        select(DocumentChunk, distance.label("distance"))
        .where(DocumentChunk.document_id == document_id)
        .where(DocumentChunk.embedding.is_not(None))
        .where(distance.is_not(None))
    )

    if exclude_chunk_id is not None:        
        stmt = stmt.where(DocumentChunk.id != exclude_chunk_id)

    stmt = stmt.order_by(distance).limit(top_k)

    rows = (await session.execute(stmt)).all()

    return [
        {
            "chunk_id": str(chunk.id),
            "text": chunk.chunk_text,
            "page_number": chunk.page_number,
            "start_sentence_index": chunk.char_start,
            "end_sentence_index": chunk.char_end,
            "similarity": 1 - chunk_distance,
        }
        for chunk, chunk_distance in rows
    ]


async def semantic_internal_lookup(
    session: AsyncSession, claim: Claim, embedding_service: EmbeddingService | None = None
) -> list[Evidence]:
    """The embedding-search counterpart to lookup_internal_evidence's exact word-overlap match —
    tried only after that direct lookup finds nothing, since it costs an embedding call per
    `requires` phrase. Each phrase in `claim.requires` (already the decomposer's own statement of
    what's needed to verify the claim) becomes its own semantic query rather than embedding the
    whole claim text once, so a claim needing two distinct facts ("Q3 headcount" and "attrition
    rate") can surface evidence for each independently instead of one blended, diluted vector.
    Hits below SEMANTIC_EVIDENCE_SIMILARITY_THRESHOLD are dropped rather than kept as weak
    evidence — this is meant to find a real match with a different vocabulary than the exact
    lookup missed, not a vaguely-related passage."""
    requires = [r for r in (claim.requires or []) if r and r.strip()]
    if not requires:
        return []

    service = embedding_service or EmbeddingService()
    query_embeddings = await service.embed_texts(requires)

    seen_chunk_ids: set[str] = set()
    evidence: list[Evidence] = []
    for embedding in query_embeddings:
        hits = await query_similar(session, claim.document_id, embedding, top_k=3, exclude_chunk_id=claim.chunk_id)
        for hit in hits:
            if hit["similarity"] < SEMANTIC_EVIDENCE_SIMILARITY_THRESHOLD:
                continue
            if hit["chunk_id"] in seen_chunk_ids:
                continue  # multiple requires-phrases can surface the same chunk — cite it once
            seen_chunk_ids.add(hit["chunk_id"])
            evidence.append(
                Evidence(
                    claim_id=claim.id,
                    source_type="internal_semantic",
                    source_ref=citation(f"document_chunk:{hit['chunk_id']}", hit["page_number"], None),
                    content_snippet=hit["text"],
                    authority_score=hit["similarity"],
                )
            )

    return evidence