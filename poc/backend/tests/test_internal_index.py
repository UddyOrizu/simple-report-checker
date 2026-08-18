import pytest_asyncio

from app.models import Claim, Document, DocumentChunk, ExtractedTable
from app.retrieval.internal_index import lookup_internal_evidence


@pytest_asyncio.fixture
async def example_a_claim(db_session):
    document = Document(filename="sample_report.docx", file_type="docx", storage_path="x", status="ingested")
    db_session.add(document)
    await db_session.flush()

    db_session.add(
        ExtractedTable(
            document_id=document.id,
            page_number=1,
            table_data=[
                ["Metric", "Value"],
                ["Revenue (current period)", "$112M"],
                ["Revenue (prior period)", "$100M"],
            ],
        )
    )

    origin_chunk = DocumentChunk(
        document_id=document.id,
        chunk_type="paragraph",
        chunk_text="Revenue grew 12% YoY, driven primarily by APAC expansion and improved cost discipline.",
        context_capsule="Q3 Business Report > Financial Highlights",
        page_number=1,
    )
    db_session.add(origin_chunk)
    await db_session.flush()

    claim = Claim(
        document_id=document.id,
        chunk_id=origin_chunk.id,
        claim_text="Revenue grew 12% year-over-year",
        source_span="Revenue grew 12% YoY",
        claim_type="statistical",
        scope="internal",
        requires=["current period revenue", "prior period revenue"],
    )
    db_session.add(claim)
    await db_session.flush()
    return claim


async def test_internal_lookup_returns_correct_table_with_citation(db_session, example_a_claim):
    evidence = await lookup_internal_evidence(db_session, example_a_claim)

    assert len(evidence) == 1
    assert evidence[0].source_type == "internal_table"
    assert "$112M" in evidence[0].content_snippet
    assert "$100M" in evidence[0].content_snippet
    assert "page 1" in evidence[0].source_ref
    assert evidence[0].claim_id == example_a_claim.id


async def test_internal_lookup_returns_nothing_for_unrelated_requires(db_session, example_a_claim):
    example_a_claim.requires = ["cost breakdown by department"]

    evidence = await lookup_internal_evidence(db_session, example_a_claim)

    assert evidence == []
