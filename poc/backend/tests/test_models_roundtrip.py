from sqlalchemy import select

from app.models import (
    AgentTrace,
    Claim,
    Document,
    DocumentChunk,
    DocumentSection,
    Evidence,
    ExtractedTable,
    PipelineRun,
    Verdict,
)


async def test_one_row_per_table_roundtrips(db_session):
    document = Document(
        filename="sample_report.docx",
        file_type="docx",
        storage_path="/storage/sample_report.docx",
        file_size_bytes=1024,
    )
    db_session.add(document)
    await db_session.flush()

    section = DocumentSection(
        document_id=document.id,
        title="Financial Highlights",
        order_index=0,
        summary="Revenue and cost figures for the period.",
    )
    db_session.add(section)
    await db_session.flush()

    chunk = DocumentChunk(
        document_id=document.id,
        section_id=section.id,
        chunk_type="paragraph",
        chunk_text="Revenue grew 12% YoY, driven primarily by APAC expansion.",
        context_capsule="Sample Report / Financial Highlights",
        page_number=1,
    )
    db_session.add(chunk)
    await db_session.flush()

    table = ExtractedTable(
        document_id=document.id,
        section_id=section.id,
        page_number=1,
        table_data={"Revenue (current period)": "$112M", "Revenue (prior period)": "$100M"},
    )
    db_session.add(table)

    claim = Claim(
        document_id=document.id,
        chunk_id=chunk.id,
        claim_text="Revenue grew 12% year-over-year",
        source_span="Revenue grew 12% YoY",
        claim_type="statistical",
        scope="internal",
        requires=["current period revenue", "prior period revenue"],
        domain="financial",
        domain_confidence=0.95,
        domain_source="structural",
    )
    db_session.add(claim)
    await db_session.flush()

    evidence = Evidence(
        claim_id=claim.id,
        source_type="internal",
        source_ref="table:1",
        content_snippet="Revenue (current period): $112M",
        authority_score=1.0,
    )
    db_session.add(evidence)

    verdict = Verdict(
        claim_id=claim.id,
        verifier_verdict="supported",
        verifier_confidence=0.99,
        verifier_reasoning="Arithmetic matches.",
        final_verdict="supported",
        final_confidence=0.99,
        severity="info",
        resolved_by="deterministic",
    )
    db_session.add(verdict)

    run = PipelineRun(
        document_id=document.id,
        stage="ingest",
        config_hash="abc123",
        raw_output={"chunks": 1},
    )
    db_session.add(run)

    trace = AgentTrace(
        claim_id=claim.id,
        agent_name="verifier",
        prompt_sent="Given a claim and evidence...",
        raw_response='{"verdict": "supported"}',
        config_hash="abc123",
    )
    db_session.add(trace)

    await db_session.flush()

    assert (await db_session.scalar(select(Document).where(Document.id == document.id))).filename == "sample_report.docx"
    assert (await db_session.scalar(select(DocumentSection).where(DocumentSection.id == section.id))).title == "Financial Highlights"
    assert (await db_session.scalar(select(DocumentChunk).where(DocumentChunk.id == chunk.id))).chunk_type == "paragraph"
    assert (await db_session.scalar(select(ExtractedTable).where(ExtractedTable.id == table.id))).table_data["Revenue (current period)"] == "$112M"
    assert (await db_session.scalar(select(Claim).where(Claim.id == claim.id))).scope == "internal"
    assert (await db_session.scalar(select(Evidence).where(Evidence.id == evidence.id))).source_ref == "table:1"
    assert (await db_session.scalar(select(Verdict).where(Verdict.id == verdict.id))).final_verdict == "supported"
    assert (await db_session.scalar(select(PipelineRun).where(PipelineRun.id == run.id))).stage == "ingest"
    assert (await db_session.scalar(select(AgentTrace).where(AgentTrace.id == trace.id))).agent_name == "verifier"
