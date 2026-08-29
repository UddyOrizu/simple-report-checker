import pytest
from sqlalchemy import select

from app.agents.verify_claim import verify_claim_via_agents
from app.llm.client import has_llm_credentials
from app.models import AgentTrace, Claim, Document, DocumentChunk, ExtractedTable

HAS_API_KEY = has_llm_credentials()
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="no LLM credentials set for the active LLM_PROVIDER — LLM stage is BLOCKED-CREDENTIALS")


async def _make_claim(db_session, *, claim_text: str, scope: str, requires: list[str]) -> Claim:
    document = Document(filename="sample_report.docx", file_type="docx", storage_path="x", status="ingested")
    db_session.add(document)
    await db_session.flush()

    db_session.add(
        ExtractedTable(
            document_id=document.id,
            page_number=1,
            table_data=[["Metric", "Value"], ["Revenue (current period)", "$112M"], ["Revenue (prior period)", "$100M"]],
        )
    )

    origin_chunk = DocumentChunk(document_id=document.id, chunk_type="paragraph", chunk_text=claim_text, page_number=1)
    db_session.add(origin_chunk)
    await db_session.flush()

    claim = Claim(
        document_id=document.id,
        chunk_id=origin_chunk.id,
        claim_text=claim_text,
        source_span=claim_text,
        claim_type="comparative",
        scope=scope,
        domain="financial",
        requires=requires,
    )
    db_session.add(claim)
    await db_session.flush()
    return claim


@requires_llm
async def test_verifying_one_claim_produces_exactly_two_agent_traces_rows(db_session):
    claim = await _make_claim(
        db_session,
        claim_text="We are ahead of our closest competitor on revenue.",
        scope="both",
        requires=["our revenue figure", "competitor's revenue figure"],
    )

    await verify_claim_via_agents(db_session, claim, config={})

    traces = (await db_session.execute(select(AgentTrace).where(AgentTrace.claim_id == claim.id))).scalars().all()

    assert len(traces) == 2
    agent_names = {t.agent_name for t in traces}
    assert agent_names == {"verifier", "challenger"}
    for trace in traces:
        assert trace.prompt_sent
        assert trace.raw_response
        assert trace.config_hash


@requires_llm
async def test_example_b_both_scope_resolves_via_agent_path_with_external_mock(db_session):
    """Example B: the both-scope comparative claim needs the internal revenue figure plus the
    mock external competitor figure — exercises 5.2's external stub end to end."""
    claim = await _make_claim(
        db_session,
        claim_text="We are ahead of our closest competitor on revenue.",
        scope="both",
        requires=["our revenue figure", "competitor's revenue figure"],
    )

    result = await verify_claim_via_agents(db_session, claim, config={})

    source_types = {e.source_type for e in result["evidence"]}
    assert "internal_table" in source_types
    assert "external" in source_types
    assert result["reconciled"]["resolved_by"] == "agent"
    assert result["reconciled"]["final_verdict"] in ("supported", "contradicted", "insufficient", "disputed")
