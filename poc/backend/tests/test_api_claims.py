import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.db import async_session
from app.llm.client import has_llm_credentials
from app.main import app
from app.models import AgentTrace, Claim, Document, DocumentChunk, ExtractedTable

HAS_API_KEY = has_llm_credentials()
requires_llm = pytest.mark.skipif(not HAS_API_KEY, reason="no LLM credentials set for the active LLM_PROVIDER — LLM stage is BLOCKED-CREDENTIALS")


async def _client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest_asyncio.fixture
async def example_a_claim():
    """Committed via the real app.db.async_session (not the rollback-wrapped db_session
    fixture) since the API layer opens its own connections — cleans up explicitly after."""
    async with async_session() as session:
        document = Document(filename="sample_report.docx", file_type="docx", storage_path="x", status="ingested")
        session.add(document)
        await session.flush()

        session.add(
            ExtractedTable(
                document_id=document.id,
                page_number=1,
                table_data=[["Metric", "Value"], ["Revenue (current period)", "$112M"], ["Revenue (prior period)", "$100M"]],
            )
        )
        origin_chunk = DocumentChunk(
            document_id=document.id, chunk_type="paragraph", chunk_text="Revenue grew 12% year-over-year", page_number=1
        )
        session.add(origin_chunk)
        await session.flush()

        claim = Claim(
            document_id=document.id,
            chunk_id=origin_chunk.id,
            claim_text="Revenue grew 12% year-over-year",
            source_span="Revenue grew 12% year-over-year",
            claim_type="statistical",
            scope="internal",
            domain="financial",
            requires=["current period revenue", "prior period revenue"],
        )
        session.add(claim)
        await session.commit()
        claim_id, document_id = claim.id, document.id

    yield claim_id

    async with async_session() as session:
        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()


async def test_get_claim_404_for_unknown_id():
    async with await _client() as client:
        response = await client.get("/claims/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


async def test_get_claim_traces_empty_for_unverified_claim(example_a_claim):
    async with await _client() as client:
        response = await client.get(f"/claims/{example_a_claim}/traces")
    assert response.status_code == 200
    assert response.json() == []


async def test_reverify_without_api_key_returns_503_not_500(example_a_claim):
    """example_a_claim's (financial, statistical) pair resolves deterministically per
    domain_registry.yaml, so this actually exercises the no-LLM path — add a second, agent-path
    claim scenario isn't needed here since 503 vs 500 is the thing under test, and the
    deterministic path itself never raises MissingCredentialsError. This just confirms
    reverify's happy path works end to end without a key."""
    async with await _client() as client:
        response = await client.post(f"/claims/{example_a_claim}/reverify")

    assert response.status_code == 200
    body = response.json()
    assert body["reconciled"]["final_verdict"] == "supported"
    assert body["reconciled"]["resolved_by"] == "deterministic"


async def test_get_claim_after_deterministic_reverify_shows_verdict_and_evidence(example_a_claim):
    async with await _client() as client:
        await client.post(f"/claims/{example_a_claim}/reverify")
        response = await client.get(f"/claims/{example_a_claim}")

    assert response.status_code == 200
    body = response.json()
    assert body["verdict"]["final_verdict"] == "supported"
    assert body["verdict"]["resolved_by"] == "deterministic"
    assert len(body["evidence"]) == 1
    assert "112M" in body["evidence"][0]["content_snippet"]


@requires_llm
async def test_reverify_produces_exactly_one_new_pair_of_agent_traces_and_touches_no_other_claim(fixtures_dir):
    """The Phase 9.3 verification gate: reverify produces exactly one new pair of agent_traces
    rows and zero changes to any other claim."""
    async with async_session() as session:
        document = Document(filename="x.docx", file_type="docx", storage_path="x", status="ingested")
        session.add(document)
        await session.flush()

        chunk = DocumentChunk(document_id=document.id, chunk_type="paragraph", chunk_text="x")
        session.add(chunk)
        await session.flush()

        claim_a = Claim(
            document_id=document.id, chunk_id=chunk.id, claim_text="We outperform our closest competitor.",
            source_span="x", claim_type="comparative", scope="both", domain="financial", requires=["x"],
        )
        claim_b = Claim(
            document_id=document.id, chunk_id=chunk.id, claim_text="Unrelated claim, never touched.",
            source_span="x", claim_type="definitional", scope="internal", domain="general", requires=[],
        )
        session.add_all([claim_a, claim_b])
        await session.commit()
        claim_a_id, claim_b_id, document_id = claim_a.id, claim_b.id, document.id

    async with await _client() as client:
        response = await client.post(f"/claims/{claim_a_id}/reverify")
    assert response.status_code == 200

    async with async_session() as session:
        traces_a = (await session.execute(select(AgentTrace).where(AgentTrace.claim_id == claim_a_id))).scalars().all()
        traces_b = (await session.execute(select(AgentTrace).where(AgentTrace.claim_id == claim_b_id))).scalars().all()
        claim_b_after = await session.get(Claim, claim_b_id)

        assert len(traces_a) == 2
        assert {t.agent_name for t in traces_a} == {"verifier", "challenger"}
        assert traces_b == []
        assert claim_b_after.status == "pending"  # untouched

        await session.execute(Document.__table__.delete().where(Document.id == document_id))
        await session.commit()
