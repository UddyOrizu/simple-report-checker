"""The API surface behind clickable citations: an evidence item's structured citation fields, and
the section endpoint they point at."""

import uuid

import httpx

from app.db import async_session
from app.main import app
from app.models import Claim, Document, DocumentChunk, DocumentSection, Evidence


async def _client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    async with async_session() as session:
        document = Document(filename="report.pdf", file_type="pdf", storage_path="x", status="complete")
        session.add(document)
        await session.flush()
        section = DocumentSection(
            document_id=document.id, title="Revenue", is_pseudo_section=False, order_index=0, level=2,
            page_start=5, page_end=6, summary="Revenue by period.", content="Revenue for FY24 was **$112M**.",
        )
        chunk = DocumentChunk(document_id=document.id, chunk_type="paragraph", chunk_text="Revenue grew 12%.", page_number=1)
        session.add_all([section, chunk])
        await session.flush()
        claim = Claim(document_id=document.id, chunk_id=chunk.id, claim_text="Revenue grew 12%.", source_span="Revenue grew 12%.",
                      claim_type="statistical", scope="internal")
        session.add(claim)
        await session.flush()
        session.add(Evidence(
            claim_id=claim.id, source_type="internal_vectorless", source_ref="ref", content_snippet='SUPPORTS: "q" — why',
            authority_score=1.0, section_id=section.id, section_path="Financials > Revenue", page_number=5,
            quote="Revenue for FY24 was $112M.", stance="supports",
        ))
        await session.commit()
        return document.id, section.id, claim.id


async def _cleanup(document_id: uuid.UUID) -> None:
    async with async_session() as session:
        await session.execute(Claim.__table__.delete().where(Claim.document_id == document_id))
        await session.execute(DocumentChunk.__table__.delete().where(DocumentChunk.document_id == document_id))
        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()


async def test_claim_evidence_carries_structured_citation_and_section_resolves():
    document_id, section_id, claim_id = await _seed()
    try:
        async with await _client() as client:
            claim = (await client.get(f"/claims/{claim_id}")).json()
            (evidence,) = claim["evidence"]
            assert {k: evidence[k] for k in ("section_id", "section_path", "page_number", "quote", "stance")} == {
                "section_id": str(section_id),
                "section_path": "Financials > Revenue",
                "page_number": 5,
                "quote": "Revenue for FY24 was $112M.",
                "stance": "supports",
            }

            response = await client.get(f"/documents/{document_id}/sections/{section_id}")
            assert response.status_code == 200
            section = response.json()
            assert (section["title"], section["level"], section["page_start"], section["content"]) == (
                "Revenue", 2, 5, "Revenue for FY24 was **$112M**.",
            )

            # a section id under the wrong document is a 404, not a leak across documents
            assert (await client.get(f"/documents/{uuid.uuid4()}/sections/{section_id}")).status_code == 404
    finally:
        await _cleanup(document_id)
