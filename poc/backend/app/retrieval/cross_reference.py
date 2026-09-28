import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.navigator import pick_section
from app.models import Claim, Document, DocumentChunk, DocumentSection, Evidence, ExtractedTable
from app.retrieval.matching import citation, matches_requires, table_text


REFERENCE_TYPES = {
    "Figure": {"alias": r"figures?|figs?\.", "num": r"\d+[a-zA-Z]?"},
    "Table": {"alias": r"tables?|tabs?\.", "num": r"\d+[a-zA-Z]?"},
    "Section": {"alias": r"sections?|secs?\.|§", "num": r"\d+(?:\.\d+)*"},
    "Chapter": {"alias": r"chapters?|chs?\.", "num": r"\d+"},
    "Appendix": {"alias": r"appendi(?:x|ces)|app\.", "num": r"[A-Z]\d*|\d+"},
    "Equation": {"alias": r"equations?|eqn?s?\.", "num": r"\d+"},
}


def _normalize_reference_token(value: str | None) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower()).rstrip(".")


def _extract_reference_mentions(text: str) -> list[tuple[str, str]]:
    mentions: list[tuple[str, str]] = []
    for ref_type, config in REFERENCE_TYPES.items():
        pattern = re.compile(rf"\b(?:{config['alias']})\s+({config['num']})", re.IGNORECASE)
        for match in pattern.finditer(text):
            mentions.append((ref_type, match.group(1).strip()))
    return mentions


def _candidate_matches_reference(candidate_text: str, ref_type: str, ref_number: str) -> bool:
    candidate_norm = _normalize_reference_token(candidate_text)
    ref_norm = _normalize_reference_token(ref_number)
    if not ref_norm or not candidate_norm:
        return False

    if ref_norm not in candidate_norm:
        return False

    type_tokens = {
        "Figure": ("figure",),
        "Table": ("table",),
        "Section": ("section", "chapter", "appendix"),
        "Chapter": ("chapter",),
        "Appendix": ("appendix", "app"),
        "Equation": ("equation", "eq", "eqn"),
    }
    match_tokens = type_tokens.get(ref_type, (ref_type.lower(),))
    if any(token in candidate_norm for token in match_tokens):
        return True

    if ref_type == "Section":
        bare_heading = bool(re.match(rf"^\s*(?:\d+(?:\.\d+)*)\b", candidate_text.strip()))
        if bare_heading:
            return True
        return bool(re.search(rf"(?:section|chapter|appendix)\s*[:.]?\s*{re.escape(ref_number)}", candidate_text, re.IGNORECASE))
    if ref_type in {"Figure", "Table", "Equation"}:
        return bool(re.search(rf"{re.escape(ref_type.lower())}\s*[:.]?\s*{re.escape(ref_number)}", candidate_text, re.IGNORECASE))
    return True


async def _resolve_by_document_reference(session: AsyncSession, claim: Claim) -> Evidence | None:
    """Find same-document mentions like 'Table 2' or 'Section 3.1' in the claim and resolve them
    against the document's actual section titles/captions. This complements the navigator-based
    cross-reference lookup and is especially useful when the claim text explicitly points to a
    numbered document component instead of a lexical requirement phrase."""
    sections = (
        await session.execute(select(DocumentSection).where(DocumentSection.document_id == claim.document_id))
    ).scalars().all()
    if not sections:
        return None

    mentions = _extract_reference_mentions(claim.claim_text)
    if not mentions:
        return None

    for ref_type, ref_number in mentions:
        normalized_number = _normalize_reference_token(ref_number)
        for section in sections:
            title = section.title or ""
            if _candidate_matches_reference(title, ref_type, normalized_number):
                section_chunks = (
                    await session.execute(select(DocumentChunk).where(DocumentChunk.section_id == section.id))
                ).scalars().all()
                snippet = section.summary or (section_chunks[0].chunk_text if section_chunks else title)
                return Evidence(
                    claim_id=claim.id,
                    source_type="internal_cross_reference",
                    source_ref=citation(f"document_section:{section.id}", (section_chunks[0].page_number if section_chunks else None), section.title),
                    content_snippet=snippet,
                    authority_score=1.0,
                )

        # Explicit figure/equation/table captions are often in the document body or in extracted
        # table cell text rather than in a section heading alone.
        document_chunks = (
            await session.execute(select(DocumentChunk).where(DocumentChunk.document_id == claim.document_id))
        ).scalars().all()
        for chunk in document_chunks:
            if _candidate_matches_reference(chunk.chunk_text, ref_type, normalized_number):
                section = await session.get(DocumentSection, chunk.section_id) if chunk.section_id else None
                return Evidence(
                    claim_id=claim.id,
                    source_type="internal_cross_reference",
                    source_ref=citation(f"document_chunk:{chunk.id}", chunk.page_number, section.title if section else None),
                    content_snippet=chunk.chunk_text,
                    authority_score=1.0,
                )

        tables = (
            await session.execute(select(ExtractedTable).where(ExtractedTable.document_id == claim.document_id))
        ).scalars().all()
        for table in tables:
            table_text_blob = table_text(table.table_data)
            if _candidate_matches_reference(table_text_blob, ref_type, normalized_number):
                section = await session.get(DocumentSection, table.section_id) if table.section_id else None
                return Evidence(
                    claim_id=claim.id,
                    source_type="internal_cross_reference",
                    source_ref=citation(f"extracted_table:{table.id}", table.page_number, section.title if section else None),
                    content_snippet=table_text_blob,
                    authority_score=1.0,
                )

    return None


async def resolve_cross_reference(session: AsyncSession, claim: Claim) -> Evidence | None:
    """Only called when 5.1's direct lookup misses — never the default path, since this makes an
    LLM call (agentic navigation) that would be wasted cost on the common case where evidence is
    already co-located. Uses the structural index's section titles+summaries (Phase 2.5) so a
    claim doesn't require scanning the whole document to find far-away evidence."""
    document = await session.get(Document, claim.document_id)
    if document is None or not document.has_structural_index:
        same_doc = await _resolve_by_document_reference(session, claim)
        return same_doc

    sections = (
        await session.execute(select(DocumentSection).where(DocumentSection.document_id == claim.document_id))
    ).scalars().all()
    if not sections:
        same_doc = await _resolve_by_document_reference(session, claim)
        return same_doc

    candidates = [(s.id, s.title, s.summary) for s in sections]
    chosen_section_id = await pick_section(claim.requires or [], candidates)
    if chosen_section_id is None:
        same_doc = await _resolve_by_document_reference(session, claim)
        return same_doc

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
