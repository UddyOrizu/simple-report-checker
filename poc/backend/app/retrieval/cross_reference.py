from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.navigator import pick_section
from app.models import Claim, Document, DocumentChunk, DocumentSection, Evidence, ExtractedTable
from app.retrieval.matching import citation, matches_requires, table_text


async def resolve_cross_reference(session: AsyncSession, claim: Claim) -> Evidence | None:
    """Only called when 5.1's direct lookup misses — never the default path, since this makes an
    LLM call (agentic navigation) that would be wasted cost on the common case where evidence is
    already co-located. Uses the structural index's section titles+summaries (Phase 2.5) so a
    claim doesn't require scanning the whole document to find far-away evidence."""
    document = await session.get(Document, claim.document_id)
    if document is None or not document.has_structural_index:
        return None  # short documents already got full-document context in 5.1 — no map to navigate

    sections = (
        await session.execute(select(DocumentSection).where(DocumentSection.document_id == claim.document_id))
    ).scalars().all()
    if not sections:
        return None

    candidates = [(s.id, s.title, s.summary) for s in sections]
    chosen_section_id = await pick_section(claim.requires or [], candidates)
    if chosen_section_id is None:
        return None  # genuinely not found — resolves to insufficient upstream, not a guess

    return await _lookup_in_section(session, claim, chosen_section_id)


async def _lookup_in_section(session: AsyncSession, claim: Claim, section_id) -> Evidence | None:
    section = await session.get(DocumentSection, section_id)
    requires = claim.requires or []

    section_title = section.title if section is not None else None

    tables = (
        await session.execute(select(ExtractedTable).where(ExtractedTable.section_id == section_id))
    ).scalars().all()
    for table in tables:
        text = table_text(table.table_data)
        if matches_requires(text, requires):
            return Evidence(
                claim_id=claim.id,
                source_type="internal_table",
                source_ref=citation(f"extracted_table:{table.id}", table.page_number, section_title),
                content_snippet=text,
                authority_score=1.0,
            )

    chunks = (
        await session.execute(select(DocumentChunk).where(DocumentChunk.section_id == section_id))
    ).scalars().all()
    for chunk in chunks:
        if chunk.chunk_type == "table":
            continue
        if matches_requires(chunk.chunk_text, requires):
            return Evidence(
                claim_id=claim.id,
                source_type="internal_chunk",
                source_ref=citation(f"document_chunk:{chunk.id}", chunk.page_number, section_title),
                content_snippet=chunk.chunk_text,
                authority_score=0.9,
            )

    return None
