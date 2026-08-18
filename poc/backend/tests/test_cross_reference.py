import os
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
import yaml

from app.models import Claim, Document, DocumentChunk, DocumentSection, ExtractedTable
from app.retrieval.cross_reference import resolve_cross_reference

HAS_API_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="ANTHROPIC_API_KEY not set — LLM stage is BLOCKED-CREDENTIALS")


async def _make_claim(db_session, *, has_structural_index: bool, requires: list[str]) -> Claim:
    document = Document(
        filename="doc.pdf", file_type="pdf", storage_path="x", status="ingested", has_structural_index=has_structural_index
    )
    db_session.add(document)
    await db_session.flush()

    origin_chunk = DocumentChunk(
        document_id=document.id, chunk_type="paragraph", chunk_text="claim origin text", char_start=0, char_end=10
    )
    db_session.add(origin_chunk)
    await db_session.flush()

    claim = Claim(
        document_id=document.id,
        chunk_id=origin_chunk.id,
        claim_text="claim text",
        source_span="claim text",
        claim_type="statistical",
        scope="internal",
        requires=requires,
    )
    db_session.add(claim)
    await db_session.flush()
    return claim


async def test_returns_none_immediately_for_short_documents_without_structural_index(db_session):
    claim = await _make_claim(db_session, has_structural_index=False, requires=["revenue"])

    with patch("app.retrieval.cross_reference.pick_section", new_callable=AsyncMock) as mock_pick:
        result = await resolve_cross_reference(db_session, claim)

    assert result is None
    mock_pick.assert_not_called()  # no map to navigate — never even asks


async def test_resolves_evidence_in_the_navigator_chosen_section(db_session):
    claim = await _make_claim(
        db_session, has_structural_index=True, requires=["current period revenue", "prior period revenue"]
    )

    decoy_section = DocumentSection(
        document_id=claim.document_id, title="Vendor Relations", is_pseudo_section=False,
        summary="Vendor contract renewals.", order_index=0,
    )
    target_section = DocumentSection(
        document_id=claim.document_id, title="Regional Performance Appendix", is_pseudo_section=False,
        summary="Revenue broken out by period.", order_index=1,
    )
    db_session.add_all([decoy_section, target_section])
    await db_session.flush()

    db_session.add(
        ExtractedTable(
            document_id=claim.document_id,
            section_id=target_section.id,
            page_number=78,
            table_data=[["Metric", "Value"], ["Revenue (current period)", "$112M"], ["Revenue (prior period)", "$100M"]],
        )
    )
    await db_session.flush()

    with patch("app.retrieval.cross_reference.pick_section", new_callable=AsyncMock) as mock_pick:
        mock_pick.return_value = target_section.id
        result = await resolve_cross_reference(db_session, claim)

    mock_pick.assert_called_once()
    called_requires, called_candidates = mock_pick.call_args.args
    assert called_requires == claim.requires
    assert {c[0] for c in called_candidates} == {decoy_section.id, target_section.id}

    assert result is not None
    assert result.source_type == "internal_table"
    assert "$112M" in result.content_snippet
    assert "page 78" in result.source_ref
    assert "Regional Performance Appendix" in result.source_ref


async def test_returns_none_when_navigator_finds_no_plausible_section(db_session):
    claim = await _make_claim(db_session, has_structural_index=True, requires=["revenue"])
    section = DocumentSection(
        document_id=claim.document_id, title="Vendor Relations", is_pseudo_section=False, summary="x", order_index=0
    )
    db_session.add(section)
    await db_session.flush()

    with patch("app.retrieval.cross_reference.pick_section", new_callable=AsyncMock) as mock_pick:
        mock_pick.return_value = None
        result = await resolve_cross_reference(db_session, claim)

    assert result is None


@requires_llm
async def test_large_report_far_apart_claim_resolves_via_real_navigator(fixtures_dir):
    """End-to-end: ingests large_report.pdf for real (Phase 2.7's pipeline), then resolves the
    page-1 claim whose evidence sits ~75 pages later via the actual LLM-backed navigator — the
    scenario the far-apart pair in that fixture exists for."""
    import uuid

    from app.db import async_session
    from app.ingestion.large_file import process_large_pdf
    from app.ingestion.section_summarizer import generate_all_section_summaries
    from app.ingestion.structural_index import build_structural_index
    from app.ingestion.parsers.pdf_parser import parse_pdf
    from sqlalchemy import select, update

    config_path = os.path.join(os.path.dirname(__file__), "..", "config", "ingestion.yaml")
    config = yaml.safe_load(open(config_path))
    path = os.path.join(fixtures_dir, "large_report.pdf")

    async with async_session() as session:
        document = Document(
            filename="large_report.pdf", file_type="pdf", storage_path=path, status="processing"
        )
        session.add(document)
        await session.commit()
        document_id = document.id

    result = await process_large_pdf(document_id, path, "Annual Regional Performance Report", config)

    # process_large_pdf doesn't build/persist the structural index (that's the whole-document
    # path in scripts/run_ingest.py) — build and summarize it here against the same elements
    elements = parse_pdf(path, config)
    sections = build_structural_index(result["page_count"], elements, config)
    assert sections  # large_report.pdf has real headings — should never short-circuit

    async with async_session() as session:
        summaries = await generate_all_section_summaries(
            [{"order_index": s["order_index"], "title": s["title"], "chunks": [
                e["data"] if isinstance(e.get("data"), list) else e.get("text", "")
                for e in elements[s["start_index"]:s["end_index"]]
            ]} for s in sections],
            config,
        )
        section_rows = []
        for s in sections:
            row = DocumentSection(
                document_id=document_id, title=s["title"], is_pseudo_section=s["is_pseudo_section"],
                summary=summaries.get(s["order_index"]), order_index=s["order_index"],
                page_start=s["page_start"], page_end=s["page_end"],
            )
            session.add(row)
            section_rows.append((s, row))
        await session.execute(update(Document).where(Document.id == document_id).values(has_structural_index=True))
        await session.flush()

        # re-link this run's chunks/tables to their sections (process_large_pdf didn't know about
        # sections yet since they didn't exist until just now)
        for s, row in section_rows:
            await session.execute(
                update(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .where(DocumentChunk.page_number >= (s["page_start"] or 0))
                .where(DocumentChunk.page_number <= (s["page_end"] or 10**9))
                .values(section_id=row.id)
            )
            await session.execute(
                update(ExtractedTable)
                .where(ExtractedTable.document_id == document_id)
                .where(ExtractedTable.page_number >= (s["page_start"] or 0))
                .where(ExtractedTable.page_number <= (s["page_end"] or 10**9))
                .values(section_id=row.id)
            )
        await session.commit()

        claim_chunk = (
            await session.execute(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document_id)
                .where(DocumentChunk.page_number == 1)
            )
        ).scalars().first()

        claim = Claim(
            document_id=document_id,
            chunk_id=claim_chunk.id,
            claim_text="Revenue grew 12% YoY, driven primarily by APAC expansion.",
            source_span="Revenue grew 12% YoY",
            claim_type="statistical",
            scope="internal",
            requires=["current period revenue", "prior period revenue"],
        )
        session.add(claim)
        await session.commit()

        evidence = await resolve_cross_reference(session, claim)

        assert evidence is not None
        assert "$112M" in evidence.content_snippet
        assert "page 78" in evidence.source_ref

        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()
